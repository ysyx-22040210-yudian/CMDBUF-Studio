"""Compile one set of sampled properties for VCS and Yosys-Slang."""
import hashlib
import re
from pathlib import Path

import sva_source
from wavedrom_adapter import PORTS


HERE = Path(__file__).resolve().parent
USER_FILE = HERE / 'functional_coverage.sv'
VERSION = 1
SOURCE = Path(__file__).relative_to(HERE.parents[1]).as_posix()
INPUTS = [(name, width) for name, width in PORTS if name not in ('clk', 'rst_n')]
WIDTH = sum(width for _, width in INPUTS)
TOP = 'test_mode_cover_top'
LABEL = r'[A-Za-z_][A-Za-z0-9_]*'
CLOCK = r'@\s*\(\s*posedge\s+clk\s*\)\s*disable\s+iff\s*\(\s*!\s*rst_n\s*\)'
DECLARATION = re.compile(r'(' + LABEL + r')\s*:\s*(assert|assume|cover)\s+property\s*\(\s*' + CLOCK)
EDITOR_DECLARATION = re.compile(r'(' + LABEL + r')\s*:\s*(assert|assume|cover)\s+(property|sequence)\s*\(\s*' + CLOCK)
EDITOR_IMMEDIATE = re.compile(r'always\s*@\s*\(\s*posedge\s+clk\s*\)\s*(?P<block>begin\b\s*)?'
                              r'if\s*\(\s*rst_n\s*\)\s*(?P<guard>begin\b\s*)?'
                              r'(?P<name>' + LABEL + r')\s*:\s*(?P<kind>assert|assume|cover)\s*\(')
OPERATORS = re.compile(r'[()\[\]{}]|\|[-=]>|##\s*[0-9]+\b')
RANGE_OPERATORS = re.compile(r'##\s*(?:\[\s*(?P<lo>\d+)\s*:\s*(?P<hi>\d+|\$)\s*\]|'
                             r'(?P<fixed>\d+))|[()\[\]{}]')
UNBOUNDED_DELAY = re.compile(r'##\s*\[\s*\d+\s*:\s*\$\s*\]')
DELAY_ALIAS = re.compile(r'##\s*\[\s*(?P<mode>[*+])\s*\]')
SAMPLED_CALL = re.compile(r'\$(rose|fell|stable|changed|sampled|past)\s*\(')
TEMPORAL = re.compile(r'\|[-=]>|##|\[\s*(?:\*|=|->)|`|\blocal::|\b(?:first_match|throughout|'
                      r'within|intersect|until|until_with|s_until|s_until_with|s_eventually|'
                      r'eventually|always|s_always|nexttime|s_nexttime|accept_on|reject_on|'
                      r'sync_accept_on|sync_reject_on|implies|iff|or|and|strong|weak)\b')


def ungroup(expression):
    expression = expression.strip()
    while expression.startswith('(') and sva_source.after_paren(expression, 0) == len(expression):
        expression = expression[1:-1].strip()
    return expression


def split_at_level(expression, prefix):
    """Split temporal operators outside Boolean subexpressions."""
    depth, start, terms, operators = 0, 0, [], []
    for match in OPERATORS.finditer(expression):
        token = match.group()
        if token in ('(', '[', '{'):
            depth += 1
        elif token in (')', ']', '}'):
            depth -= 1
        elif depth == 0 and token.startswith(prefix):
            terms.append(expression[start:match.start()].strip())
            operators.append(token)
            start = match.end()
    terms.append(expression[start:].strip())
    return terms, operators


def boolean(expression):
    if not expression or TEMPORAL.search(expression):
        raise ValueError('需要单拍布尔表达式，或使用受支持的固定拍数写法')
    return '(' + expression + ')'


def sequence(expression, leading_delay=False):
    parts, operators = split_at_level(ungroup(expression), '##')
    if leading_delay and not parts[0] and operators:
        parts[0] = "1'b1"
    offsets = [0]
    for op in operators:
        offsets.append(offsets[-1] + int(op[2:]))
    return [(offset, boolean(part)) for offset, part in zip(offsets, parts)], offsets[-1]


def predicate(kind, source, history_name='fc_history'):
    """Lower fixed delays to past samples, with an explicit reset history guard."""
    parts, ops = split_at_level(ungroup(source), '|')
    if kind == 'cover':
        if ops:
            raise ValueError('cover 请使用布尔条件或 ##N 序列，避免蕴含的空真命中')
        terms, end = sequence(parts[0])
    elif ops:
        if len(ops) != 1:
            raise ValueError('每条属性只支持一层 |-> 或 |=>')
        terms = [(0, boolean(parts[0]))]
        right, duration = sequence(parts[1], leading_delay=True)
        if len(right) > 2 or (len(right) == 2 and right[0][1] != "(1'b1)"):
            raise ValueError('蕴含后件请使用单拍条件或 ##N 后的单拍条件')
        end = duration + int(ops[0] == '|=>')
    else:
        return boolean(parts[0]), 0
    if end > 1000000:
        raise ValueError('固定拍数超过环境的 1000000 拍上限')

    def sampled(offset, expr):
        age = end - offset
        return '$past({}, {})'.format(expr, age) if age else expr

    history = '{}[{}]'.format(history_name, end - 1) if end else "1'b1"
    if kind == 'cover':
        return ' && '.join([history] + [sampled(at, expr) for at, expr in terms]), end
    return '!{} || !{} || {}'.format(history, sampled(*terms[0]), right[-1][1]), end


def cover_steps(source):
    """Parse a linear sequence of Boolean samples and inclusive delay ranges."""
    source = ungroup(source)
    samples, delays, start, depth = [], [], 0, 0
    for token in RANGE_OPERATORS.finditer(source):
        if not token.group().startswith('##'):
            depth += 1 if token.group() in '([{' else -1
            continue
        if depth:
            raise ValueError('嵌套时序序列超出当前 Formal 支持范围')
        samples.append(boolean(source[start:token.start()].strip()))
        low = int(token['lo'] or token['fixed'])
        high = None if token['hi'] == '$' else int(token['hi'] or token['fixed'])
        delays.append((low, high))
        start = token.end()
    samples.append(boolean(source[start:].strip()))
    maximum = sum(low if high is None else high for low, high in delays)
    if maximum > 1000000 or any(high is not None and low > high for low, high in delays):
        raise ValueError('Cover 的延迟范围无效或超过环境的 1000000 拍上限')
    return samples, delays


def cover_true(expression):
    return "((({}) ? 1'b1 : 1'b0) === 1'b1)".format(expression)


def normalize_cover(source):
    from sva_sequences import timed_property

    source, aborts = property_aborts(source)
    if aborts:
        # A Boolean property ends on its first sample. An asynchronous
        # acceptance after that sample only affects pending attempts.
        try:
            return '(' + ' || '.join(aborts + [boolean(source)]) + ')'
        except ValueError as exc:
            raise ValueError('Cover 的 accept_on 当前支持单拍布尔属性') from exc
    source = DELAY_ALIAS.sub(lambda match: '##[{}:$]'.format(int(match['mode'] == '+')), source)
    if source.startswith('##'):
        source = "1'b1 " + source
    timing = timed_property(source)
    if timing and not timing[0].endswith('nexttime'):
        return source
    parts, ops = split_at_level(source, '|')
    if len(ops) == 1:
        # Give a Cover a hit only on a triggered implication.
        return '({}) ##{} ({})'.format(parts[0], int(ops[0] == '|=>'), normalize_cover(parts[1]))
    return source


def sampled_arguments(source):
    start, depth = 0, 0
    for index, character in enumerate(source):
        if character in '([{':
            depth += 1
        elif character in ')]}':
            depth -= 1
        elif character == ',' and depth == 0:
            yield source[start:index].strip()
            start = index + 1
    yield source[start:].strip()


def lower_sampled_calls(source, prefix, clock, all_past=False):
    """Lower sampled functions missing in the open-source frontend."""
    declarations = []

    def rewrite(text):
        pieces, start = [], 0
        while True:
            call = SAMPLED_CALL.search(text, start)
            if not call:
                return ''.join(pieces) + text[start:]
            end = sva_source.after_paren(text, call.end() - 1)
            if end is None:
                raise ValueError('采样函数缺少结束括号')
            args = [rewrite(arg) for arg in sampled_arguments(text[call.end():end-1])]
            function = call[1]
            current = args[0]
            event = '@(posedge{})'.format(clock)
            if function == 'past' and (all_past or len(args) > 2):
                if len(args) > 4 or len(args) == 4 and args[3] and \
                        re.sub(r'\s+', '', args[3]) != event:
                    raise ValueError('$past 指定的时钟须与当前属性的采样时钟一致')
                delay = args[1] if len(args) > 1 else '1'
                if delay and not delay.isdecimal():
                    raise ValueError('带门控的 $past 需要正整数拍数')
                cycles = int(delay or '1')
                if not 1 <= cycles <= 1000000:
                    raise ValueError('$past 的拍数超出支持范围')
                flag = '{}_past_{}'.format(prefix, len(declarations))
                gate = args[2] if len(args) > 2 and args[2] else "1'b1"
                width = '$bits({})'.format(current)
                declarations.extend([
                    'typedef type({}) {}_type;'.format(current, flag),
                    'reg [({}*{})-1:0] {};'.format(width, cycles, flag),
                    'always @(posedge {})'.format(clock),
                    '  if ({}) {} <= ({} << {}) | $unsigned({});'.format(gate, flag, flag, width, current),
                ])
                replace = "{0}_type'({0}[({1}-1)*{2} +: {2}])".format(flag, cycles, width)
            elif function == 'past':
                replace = '$past({})'.format(', '.join(args))
            elif len(args) > 1 and (function == 'sampled' or len(args) != 2 or
                                    re.sub(r'\s+', '', args[1]) != event):
                raise ValueError('采样函数 ' + '$' + function + ' 的额外事件需要独立时钟建模')
            elif function == 'sampled':
                replace = '({})'.format(current)
            elif function in ('rose', 'fell'):
                value = int(function == 'rose')
                bit = "1'({})".format(current)
                replace = "(({} === 1'b{}) && ($past({}, 1) !== 1'b{}))".format(bit, value, bit, value)
            else:
                compare = '===' if function == 'stable' else '!=='
                replace = '(({}) {} $past({}, 1))'.format(current, compare, current)
            pieces.extend((text[start:call.start()], replace))
            start = end

    predicate = rewrite(source)
    if all_past and SAMPLED_CALL.search(predicate):
        predicate = rewrite(predicate)
    return predicate, declarations


def unbounded_cover(source, prefix, clock='clk', reset='!rst_n'):
    """Emit a clocked matcher for a linear sequence with ##[N:$] delays."""
    samples, delays = cover_steps(source)
    widths = [max(0, low - 1) if high is None else high for low, high in delays]
    declarations, updates = [], []
    valid = prefix + '_valid'
    if reset:
        cancel = prefix + '_disabled'
        declarations += [
            'wire {} = {};'.format(cancel, cover_true(reset)),
            "reg [{}:0] {} = '0;".format(max([1] + widths) - 1, valid),
            'always @(posedge {} or posedge {})'.format(clock, cancel),
            "    if ({}) {} <= '0;".format(cancel, valid),
            "    else {0} <= ({0} << 1) | 1'b1;".format(valid),
        ]

    # Use only the sampling clock for the data updates. The validity register
    # discards all pre-disable samples even if disable iff pulses between edges.
    matched = samples[0]
    for index, (term, (low, high), width) in enumerate(zip(samples[1:], delays, widths)):
        state = '{}_{}'.format(prefix, index)
        past = ''
        if width:
            delay = state + '_delay'
            declarations.append("reg [{}:0] {} = '0;".format(width - 1, delay))
            updates.append('{} <= ({} << 1) | {};'.format(delay, delay, cover_true(matched)))
            first = width - 1 if high is None else max(0, low - 1)
            window = '[{}:{}]'.format(width - 1, first)
            history = delay + window
            if reset:
                history += ' & ' + valid + window
            past = '(|({}))'.format(history)
        if high is None:
            seen = state + '_seen'
            declarations.append("reg {} = 1'b0;".format(seen))
            waiting = '({} && {}[0])'.format(seen, valid) if reset else seen
            updates.append('{} <= {} || {};'.format(seen, waiting, past or cover_true(matched)))
            past = waiting
        if low == 0:
            past = '({} || {})'.format(past, matched) if past else matched
        matched = '({} && {})'.format(past, term)

    return matched, declarations + ['always @(posedge {}) begin'.format(clock)] + [
        '    ' + update for update in updates] + ['end']


def structured_cover(source, prefix, clock, reset, horizon=None):
    from sva_sequences import Matcher, max_span, parse

    source, sampled = lower_sampled_calls(source, prefix, clock, all_past=True)
    sequence = parse(normalize_cover(source), boolean)
    if horizon is None:
        return None, []
    span = max_span(sequence)
    if span is not None:
        horizon = min(horizon, max(0, span-1))
    try:
        predicate, state = Matcher(prefix, horizon).model(sequence, clock, reset, cover_true)
    except RecursionError as exc:
        raise ValueError('SVA 序列在当前求解深度的嵌套规模过大') from exc
    return predicate, sampled + state


def property_aborts(source):
    source = ungroup(source)
    aborts = []
    while True:
        accepted = re.match(r'accept_on\s*\(', source)
        if not accepted:
            break
        close = sva_source.after_paren(source, accepted.end()-1)
        if close is None:
            raise ValueError('accept_on 缺少结束括号')
        aborts.append(boolean(source[accepted.end():close-1].strip()))
        source = ungroup(source[close:])
    return source, aborts


def safety_property(source, reset):
    """Normalize sampled Boolean safety forms while preserving abort conditions."""
    source, aborts = property_aborts(source)
    if reset:
        aborts.insert(0, reset)
    always = re.match(r'always\b(?!\s*\[)\s*', source)
    if always:
        source = ungroup(source[always.end():])
    if len(aborts) <= 1:
        return source, aborts[0] if aborts else ''
    return source, ' || '.join('(' + term + ')' for term in aborts)


def structured_implication(source, prefix, clock, reset, horizon=None):
    from rtl_cover import history_register
    from sva_sequences import timed_property

    terms, operators = split_at_level(ungroup(source), '|')
    if len(operators) > 1:
        raise ValueError('需要单层时序蕴含')
    lead = int(operators == ['|=>'])
    left = terms[0] if operators else "1'b1"
    consequent = ungroup(terms[-1])
    wrapper = re.match(r'(?:strong|weak)\s*\(', consequent)
    if wrapper and sva_source.after_paren(consequent, wrapper.end()-1) == len(consequent):
        consequent = ungroup(consequent[wrapper.end():-1])
    timing = timed_property(consequent)
    window = re.match(r'##\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*', consequent)
    if timing:
        kind, (first, last), operand = timing
        body = boolean(operand)
        if not isinstance(first, int):
            raise ValueError('此属性的时间下界需要非负整数')
        if kind == 'always' and not operators:
            # A new unconditional attempt starts at each edge. The earliest
            # required sample of every attempt covers all subsequent samples.
            required = [(first, body, 0)]
        else:
            if not isinstance(last, int) or last-first > 4096:
                raise ValueError('此属性需要有限时间区间，或独立的活性检查')
            if kind.endswith('always'):
                required = [(offset, body, 0) for offset in range(first, last+1)]
            else:
                required = [(last, body, last-first)]
    elif window:
        first, last = int(window[1]), int(window[2])
        body = boolean(consequent[window.end():])
        if not 0 <= first <= last or last-first > 4096:
            raise ValueError('蕴含后件的有限延时范围无效或展开规模过大')
        required = [(last, body, last-first)]
    else:
        samples, _ = sequence(consequent, leading_delay=True)
        required = [(offset, body, 0) for offset, body in samples]
    if lead + max(offset for offset, _, _ in required) > 1000000:
        raise ValueError('蕴含后件的等待拍数超过环境上限')
    antecedent, helpers = structured_cover(left, prefix + '_left', clock, reset, horizon)
    if horizon is None:
        return None, []
    trigger = prefix + '_trigger'
    history = prefix + '_history'
    checks = []
    helpers.append('wire {} = {};'.format(trigger, antecedent))
    helpers.append(history_register(max(offset for offset, _, _ in required) + lead, history, clock, reset,
                                    asynchronous=True))
    for offset, body, width in required:
        body, sampled = lower_sampled_calls(body, '{}_right{}'.format(prefix, offset), clock)
        helpers.extend(sampled)
        delay = offset + lead
        start = ('({}[{}] && $past({}, {}))'.format(history, delay-1, trigger, delay) if delay else trigger)
        hit = ' || '.join([body] + ['$past({}, {})'.format(body, k) for k in range(1, width+1)])
        checks.append('(!({}) || ({}))'.format(start, hit))
    return ' && '.join(checks), helpers


def property_at(clean, match, require_bare=True, opening=None):
    if opening is None:
        opening = clean.index('(', match.start(2) + len(match[2]))
    end = sva_source.after_paren(clean, opening)
    if end is None:
        raise ValueError('缺少结束括号')
    condition = clean[match.end():end - 1].strip()
    tail = sva_source.next_token(clean, end)
    if not require_bare:
        return condition, tail
    if clean[tail:tail + 1] != ';':
        raise ValueError('属性须以分号结束，不附带动作块')
    return condition, tail + 1


def property_actions(text, clean, start, kind):
    actions = {}
    failure = re.match(r'else\b', clean[start:])
    if failure:
        after = start
    else:
        after = sva_source.action_end(clean, start)
        if after is None:
            raise ValueError('需要结束分号、单条调用或 begin/end 动作块')
        if clean[start:after].strip() != ';':
            actions['pass'] = text[start:after]
    tail = sva_source.next_token(clean, after)
    failure = re.match(r'else\b', clean[tail:])
    if failure and kind != 'cover':
        start = sva_source.next_token(clean, tail + failure.end())
        after = sva_source.action_end(clean, start)
        if after is None:
            raise ValueError('else 后需要单条调用或 begin/end 动作块')
        actions['fail'] = text[start:after]
    return after, actions


def user_properties(text):
    clean = sva_source.mask_sv(text)
    properties, names, cursor = [], set(), 0
    definitions = {entry['name']: entry['body'] for entry in sva_source.NAMED_DECLARATION.finditer(clean)}
    written = set()
    while clean[cursor:].strip():
        cursor = sva_source.next_token(clean, cursor)
        line = clean.count('\n', 0, cursor) + 1
        try:
            declaration = sva_source.NAMED_DECLARATION.match(clean, cursor)
            if declaration:
                name = declaration['name']
                if name in names or name in written:
                    raise ValueError('标签或声明重名 ' + name)
                written.add(name)
                cursor = declaration.end()
                continue
            immediate = EDITOR_IMMEDIATE.match(clean, cursor)
            match = immediate or EDITOR_DECLARATION.match(clean, cursor)
            if not match:
                raise ValueError('需要带标签的主时钟并发属性，或主时钟复位释放后的即时断言')
            name, kind, form = (match['name'], match['kind'], 'immediate') if immediate else match.groups()
            if form == 'sequence' and kind != 'cover':
                raise ValueError('sequence 声明形式用于 cover')
            if name in names or name in definitions:
                raise ValueError('标签重复 ' + name)
            condition, tail = property_at(clean, match, require_bare=False,
                                          opening=match.end()-1 if immediate else None)
            cursor, actions = property_actions(text, clean, tail, kind)
            if immediate:
                for _ in range(bool(match['block']) + bool(match['guard'])):
                    close = re.match(r'\s*end\b', clean[cursor:])
                    if not close:
                        raise ValueError('即时断言的时钟过程缺少 end')
                    cursor += close.end()
                boolean(condition)
            expanded = sva_source.expand_named(condition, definitions)
            values = re.sub(r'@\s*\(\s*posedge\s+clk\s*\)', '', expanded)
            if re.search(r'(?<![.\w$])clk\b|(?<![\w$])fc_[A-Za-z_0-9]+\b', values):
                raise ValueError('clk 用于指定采样事件，fc_ 前缀保留给工具')
            bounded_model = ''
            formal_source, reset = (expanded, '!rst_n') if kind == 'cover' else safety_property(expanded, '!rst_n')
            try:
                condition_formal, helpers = lower_sampled_calls(formal_source, 'fc_sample_' + name, 'clk')
                if kind == 'cover':
                    condition_formal = normalize_cover(condition_formal)
                if kind == 'cover' and UNBOUNDED_DELAY.search(condition_formal):
                    expr, state = unbounded_cover(condition_formal, 'fc_wait_' + name)
                    helpers.extend(state)
                    history = 0
                elif kind == 'cover' and re.search(r'##\s*\[', condition_formal):
                    from rtl_cover import lower_cover
                    expr, history = lower_cover(condition_formal)
                else:
                    expr, history = predicate(kind, condition_formal)
                    if history and reset != '!rst_n':
                        from rtl_cover import history_register
                        history_name = 'fc_abort_' + name
                        expr, _ = predicate(kind, condition_formal, history_name)
                        helpers.append(history_register(history, history_name, 'clk', reset, asynchronous=True))
                        history = 0
            except ValueError:
                compiler = structured_cover if kind == 'cover' else structured_implication
                expr, helpers = compiler(formal_source, 'fc_seq_' + name, 'clk', reset)
                history, bounded_model = 0, 'cover' if kind == 'cover' else 'implication'
        except ValueError as exc:
            raise ValueError('{}:{} {}'.format(USER_FILE.name, line, exc)) from exc
        properties.append(dict(name=name, kind=kind, line=line, expression=condition,
                               predicate=expr, history=history))
        if helpers:
            properties[-1]['formal_helpers'] = helpers
        if bounded_model:
            properties[-1]['bounded_model'] = bounded_model
            if expanded != condition:
                properties[-1]['formal_expression'] = expanded
        if reset != '!rst_n':
            properties[-1]['formal_reset'] = reset
        if form != 'property':
            properties[-1]['form'] = form
        if actions:
            properties[-1]['actions'] = actions
        names.add(name)
    return properties


def manifest(raw, rtl=()):
    text = raw.decode('utf-8-sig')
    properties = user_properties(text) + list(rtl)
    if len({row['name'] for row in properties}) != len(properties):
        raise ValueError('属性编辑器标签与 DUT Cover 标识重复，请修改编辑器标签。')
    result = dict(version=VERSION, sha256=hashlib.sha256(raw).hexdigest(),
                  adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), properties=properties)
    declarations = [text[entry.start():entry.end()]
                    for entry in sva_source.NAMED_DECLARATION.finditer(sva_source.mask_sv(text))]
    if declarations:
        result['declarations'] = declarations
    if rtl:
        import rtl_cover
        result['rtl_cover_adapter_sha256'] = hashlib.sha256(Path(rtl_cover.__file__).read_bytes()).hexdigest()
    return result


def cover_names(properties):
    return [p['name'] for p in properties['properties'] if p['kind'] == 'cover']


def builtin_properties(text):
    """Read the common port contract from the same source VCS instruments."""
    from check_policy import COMMON

    clean = sva_source.mask_sv(text)
    found = {}
    for match in DECLARATION.finditer(clean):
        name, kind = match.groups()
        if name not in COMMON and kind != 'assume':
            continue
        if name in found:
            raise ValueError('基线端口约束标签重复 ' + name)
        condition, _ = property_at(clean, match)
        if name == 'E06_SUCCESS_BUSY' and re.search(r'\brequested_half_busy\b', condition):
            # Use the authored helper from the native bank generate block.
            busy = re.search(r'\bassign\s+requested_half_busy\s*=\s*([^;]+);', clean)
            if busy is None:
                raise ValueError('无法读取 E06 的 requested_half_busy 定义')
            condition = re.sub(r'\brequested_half_busy\b', '(' + busy[1].strip() + ')', condition)
        expr, history = predicate(kind, condition)
        found[name] = dict(name=name, kind=kind, predicate=expr, history=history)
    if COMMON - found.keys():
        raise ValueError('无法读取兼容的基线端口约束 ' + ', '.join(sorted(COMMON - found.keys())))
    return list(found.values())


def input_concat():
    return '{' + ', '.join(name for name, _ in INPUTS) + '}'


def stimulus_assignment():
    return '            ' + input_concat() + ' = frames[index];\n'


def packed(values):
    value = 0
    for name, width in INPUTS:
        value = (value << width) | values.get(name, 0)
    return value


def sv_literal(value):
    return "{}'h{:x}".format(WIDTH, value)


def helpers(history):
    return [
        'wire ar_fire = arvalid && ((src < 3) ? arready[src] : 1\'b0);',
        'wire issue_fire = |(req & ar_rd_gnt & rw_rd_gnt);',
        'wire r_fire = |(rvalid & rready);',
        'reg [{}:0] fc_history;'.format(history - 1),
        'wire past_valid = fc_history[0];',
        'always @(posedge clk or negedge rst_n)',
        "    if (!rst_n) fc_history <= '0;",
        "    else fc_history <= (fc_history << 1) | 1'b1;",
    ]


def statement(label, kind, expr, form='property', reset='!rst_n'):
    if form == 'immediate':
        return '{}: {} ({})'.format(label, kind, ' '.join(expr.split()))
    return '{}: {} {} (@(posedge clk) disable iff ({}) ({}))'.format(
        label, kind, form, reset, ' '.join(expr.split()))


def simulation(properties, declarations=()):
    # VCS can evaluate the original four-state sequence and implication.
    # The single-cycle lowering is needed only by the Slang frontend.
    lines = helpers(1)
    lines.extend(declarations)
    for p in properties:
        if p.get('origin') == 'rtl':
            continue
        name, kind, expr = p['name'], p['kind'], p['expression']
        actions = p.get('actions', {})
        form = p.get('form', 'property')
        if kind == 'cover':
            lines.append('bit fc_hit_{} = 0;'.format(name))
        if form == 'immediate':
            lines.append('always @(posedge clk) if (rst_n) begin')
        if kind == 'cover':
            lines += [statement('FC_C_' + name, kind, expr, form) + ' begin',
                      '    if (!fc_hit_{0}) $display("FUNCTIONAL_HIT {0}");'.format(name),
                      '    fc_hit_{} = 1;'.format(name)]
            if 'pass' in actions:
                lines.append(actions['pass'])
            lines.append('end')
        else:
            label = 'FC_USER_' + name
            source = statement(label, 'assert', expr, form)
            if actions:
                if 'pass' in actions:
                    source += ' ' + actions['pass']
                source += ' else begin\n'
                if 'fail' in actions:
                    source += actions['fail'] + '\n'
                source += 'monitor.native_failure("{}", $realtime/1ps, "user {}");\nend'.format(label, kind)
            else:
                source += ' else monitor.native_failure("{}", $realtime/1ps, "user {}");'.format(label, kind)
            lines.append(source)
        if form == 'immediate':
            lines.append('end')
    return '\n'.join(lines) + '\n'


def collect_hits(log, properties, require_complete):
    from rtl_cover import native_hits

    unavailable = []
    hits = sorted(set(re.findall(r'^FUNCTIONAL_HIT (' + LABEL + r')\s*$', log, re.M)) |
                  native_hits(log, properties['properties'], require_complete, unavailable))
    if (set(hits) - set(cover_names(properties)) or
            require_complete and len(re.findall(r'^FUNCTIONAL_COMPLETE\s*$', log, re.M)) != 1):
        raise ValueError('功能覆盖率采集记录不完整或与编译版本不一致')
    receipt = dict(version=properties['version'], sha256=properties['sha256'], hits=hits,
                   adapter_sha256=properties['adapter_sha256'], complete=bool(require_complete))
    if unavailable:
        receipt['vcs_unavailable'] = sorted(unavailable)
    if 'rtl_cover_adapter_sha256' in properties:
        receipt['rtl_cover_adapter_sha256'] = properties['rtl_cover_adapter_sha256']
    return receipt


def summary(properties, results):
    names = cover_names(properties)
    hits = {name: 0 for name in names}
    for row in results:
        if row['status'] == 'PASS' and not row.get('injection'):
            for name in row['functional']['hits']:
                hits[name] += 1
    return dict(manifest=properties, hits=hits, covered=sum(bool(n) for n in hits.values()), total=len(hits))
