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
TOP = 'cmdbuf_cover_top'
LABEL = r'[A-Za-z_][A-Za-z0-9_]*'
CLOCK = r'@\s*\(\s*posedge\s+clk\s*\)\s*disable\s+iff\s*\(\s*!\s*rst_n\s*\)'
DECLARATION = re.compile(r'(' + LABEL + r')\s*:\s*(assert|assume|cover)\s+property\s*\(\s*' + CLOCK)
OPERATORS = re.compile(r'[()\[\]{}]|\|[-=]>|##\s*[0-9]+\b')
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


def predicate(kind, source):
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

    history = 'fc_history[{}]'.format(end - 1) if end else "1'b1"
    if kind == 'cover':
        return ' && '.join([history] + [sampled(at, expr) for at, expr in terms]), end
    return '!{} || !{} || {}'.format(history, sampled(*terms[0]), right[-1][1]), end


def property_at(clean, match):
    opening = clean.index('(', match.start(2) + len(match[2]))
    end = sva_source.after_paren(clean, opening)
    if end is None:
        raise ValueError('缺少结束括号')
    condition = clean[match.end():end - 1].strip()
    tail = sva_source.next_token(clean, end)
    if clean[tail:tail + 1] != ';':
        raise ValueError('属性须以分号结束，不附带动作块')
    return condition, tail + 1


def user_properties(text):
    clean = sva_source.mask_sv(text)
    properties, names, cursor = [], set(), 0
    while clean[cursor:].strip():
        cursor = sva_source.next_token(clean, cursor)
        line = clean.count('\n', 0, cursor) + 1
        try:
            match = DECLARATION.match(clean, cursor)
            if not match:
                raise ValueError('需要带标签的 assert/assume/cover property，使用 @(posedge clk) disable iff (!rst_n)')
            name, kind = match.groups()
            if name in names:
                raise ValueError('标签重复 ' + name)
            condition, cursor = property_at(clean, match)
            if re.search(r'(?<![.\w$])clk\b|(?<![\w$])fc_[A-Za-z_0-9]+\b', condition):
                raise ValueError('clk 用于指定采样事件，fc_ 前缀保留给工具')
            expr, history = predicate(kind, condition)
        except ValueError as exc:
            raise ValueError('{}:{} {}'.format(USER_FILE.name, line, exc)) from exc
        properties.append(dict(name=name, kind=kind, line=line, expression=condition,
                               predicate=expr, history=history))
        names.add(name)
    return properties


def manifest(raw):
    properties = user_properties(raw.decode('utf-8-sig'))
    return dict(version=VERSION, sha256=hashlib.sha256(raw).hexdigest(),
                adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), properties=properties)


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


def statement(label, kind, expr):
    return '{}: {} property (@(posedge clk) disable iff (!rst_n) ({}))'.format(label, kind, ' '.join(expr.split()))


def simulation(properties):
    # VCS can evaluate the original four-state sequence and implication.
    # The single-cycle lowering is needed only by the Slang frontend.
    lines = helpers(1)
    for p in properties:
        name, kind, expr = p['name'], p['kind'], p['expression']
        if kind == 'cover':
            lines += ['bit fc_hit_{} = 0;'.format(name),
                      statement('FC_C_' + name, kind, expr) + ' begin',
                      '    if (!fc_hit_{0}) $display("FUNCTIONAL_HIT {0}");'.format(name),
                      '    fc_hit_{} = 1;'.format(name), 'end']
        else:
            label = 'FC_USER_' + name
            lines += [statement(label, 'assert', expr) +
                      ' else monitor.native_failure("{}", $realtime/1ps, "user {}");'.format(label, kind)]
    return '\n'.join(lines) + '\n'


def collect_hits(log, properties, require_complete):
    hits = sorted(set(re.findall(r'^FUNCTIONAL_HIT (' + LABEL + r')\s*$', log, re.M)))
    if (set(hits) - set(cover_names(properties)) or
            require_complete and len(re.findall(r'^FUNCTIONAL_COMPLETE\s*$', log, re.M)) != 1):
        raise ValueError('功能覆盖率采集记录不完整或与编译版本不一致')
    return dict(version=properties['version'], sha256=properties['sha256'], hits=hits,
                adapter_sha256=properties['adapter_sha256'],
                complete=bool(require_complete))


def summary(properties, results):
    names = cover_names(properties)
    hits = {name: 0 for name in names}
    for row in results:
        if row['status'] == 'PASS' and not row.get('injection'):
            for name in row['functional']['hits']:
                hits[name] += 1
    return dict(manifest=properties, hits=hits, covered=sum(bool(n) for n in hits.values()), total=len(hits))
