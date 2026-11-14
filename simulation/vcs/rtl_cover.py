"""Discover instantiated DUT properties and prepare Cover search models."""
from bisect import bisect_right
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import functional_properties as props
import sva_source


TOP = props.TOP
SOURCE = Path(__file__).resolve().relative_to(props.HERE.parents[1]).as_posix()
COVER = re.compile(r'\bcover\s*(?P<form>property\b|sequence\b)?\s*\(')
INERT_DIRECTIVE = re.compile(r'(?m)^[ \t]*`(?:default_nettype|timescale|ifn?def|elsif|else|endif)(?![\w$])')
COVER_OR_MACRO = re.compile(r'(?<![\w$\\])cover(?![\w$])|`')
ASSERTION_KINDS = {'Assert': 'assert', 'Assume': 'assume', 'CoverProperty': 'cover', 'CoverSequence': 'cover'}
SYSTEM_STOP = re.compile(r'\$(?:stop|fatal|finish)\b\s*(?=\(|;)')
SAMPLE = re.compile(r'@\s*\(\s*posedge\s+(?P<clock>[A-Za-z_][\w$]*)\s*\)')
RESET = re.compile(r'\s*disable\s+iff\s*(?=\()')
SOURCE_LOCATION = re.compile(r'^(.+):(\d+)\.(\d+): error:')
MACRO_DEFINITION = re.compile(r'(?m)^[ \t]*`define\b(?:[^\n]*?\\\r?\n)*[^\n]*')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')


def source_arguments(dut, files, includes):
    return ['--compat', 'vcs', '--single-unit', '--timescale', '1ns/1ps'] + [
        flag for path in includes for flag in ('-I', path)] + [
        flag for define in dut['defines'] for flag in ('-D', define)] + files


def has_covers(sources):
    return any(COVER.search(sva_source.mask_sv(raw.decode('utf-8', errors='replace')))
               for raw in sources.values())


def may_declare_cover(code):
    # Includes and macro expansions can introduce a Cover without a literal declaration.
    clean = INERT_DIRECTIVE.sub('', sva_source.mask_sv(code))
    return COVER_OR_MACRO.search(clean) is not None


def sampled_expression(expression):
    clock = SAMPLE.match(expression)
    if not clock:
        return None
    start, reset = clock.end(), ''
    disable = RESET.match(expression, start)
    if disable:
        end = sva_source.after_paren(expression, disable.end())
        if end is None:
            return None
        reset = expression[disable.end()+1:end-1]
        start = end
    return clock['clock'], reset, expression[start:].strip()


def lower_cover(condition, history_name='fc_history'):
    """Lower direct Boolean sequences, including finite delay ranges."""
    if not re.search(r'##\s*\[', condition):
        return props.predicate('cover', condition, history_name)
    condition = props.ungroup(condition)
    parts, operators, start, depth = [], [], 0, 0
    tokens = re.finditer(r'##\s*(?:\[\s*\d+\s*:\s*\d+\s*\]|\d+)|[()\[\]{}]', condition)
    for token in tokens:
        value = token.group()
        if value.startswith('##'):
            if depth:
                raise ValueError('嵌套时序序列超出当前 Formal 支持范围')
            parts.append(props.boolean(condition[start:token.start()].strip()))
            limits = [int(value) for value in re.findall(r'\d+', value)]
            operators.append((limits[0], limits[-1]))
            start = token.end()
        else:
            depth += 1 if value in '([{' else -1
    parts.append(props.boolean(condition[start:].strip()))
    maximum = sum(high for _, high in operators)
    if maximum > 1000000 or any(low > high for low, high in operators):
        raise ValueError('Cover 的延迟范围无效或超过环境的 1000000 拍上限')
    # Every path is checked with its own history guard. Avoid a large
    # expansion before allocating either the model or an SBY job.
    if any(high-low > 4096 for low, high in operators):
        raise ValueError('此 Cover 的延迟范围过大，开源前端无法直接展开')
    pending = {(len(parts)-1, 0)}
    visited = set()
    while pending:
        term, age = pending.pop()
        visited.add((term, age))
        if term:
            lo, hi = operators[term-1]
            pending.update((term-1, age+delay) for delay in range(lo, hi+1)
                           if (term-1, age+delay) not in visited)
        if len(pending) + len(visited) > 4096:
            raise ValueError('此 Cover 延迟范围展开发散，请缩小范围或使用显式状态')
    terms = {}
    for term, age in sorted(visited):
        point = '$past({}, {})'.format(parts[term], age) if age else parts[term]
        prefix = None
        if term:
            lo, hi = operators[term-1]
            branches = [terms[term-1, age+delay] for delay in range(lo, hi+1)]
            if sum(map(len, branches)) > 200000:
                raise ValueError('此 Cover 序列超出直接布尔展开范围')
            prefix = '(' + ' || '.join(branches) + ')'
        elif age:
            prefix = '{}[{}]'.format(history_name, age-1)
        terms[term, age] = '({} && {})'.format(prefix, point) if prefix else point
        if len(terms[term, age]) > 200000:
            raise ValueError('此 Cover 序列超出直接布尔展开范围')
    return terms[len(parts)-1, 0], maximum


def temporal(expression):
    return any(match.group() != '`' for match in props.TEMPORAL.finditer(expression))


def write_sources(directory, dut, sources):
    for relative, data in sources.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return list(dut['files']), list(dut['include_dirs'])


def syntax_spans(text):
    """Find direct Cover declarations and keep source offsets and line numbers."""
    clean = sva_source.mask_sv(text)
    clean = MACRO_DEFINITION.sub(lambda match: re.sub(r'[^\n]', ' ', match.group()), clean)
    starts = [0] + [match.end() for match in re.finditer(r'\n', clean)]
    for match in COVER.finditer(clean):
        if match.start() and (clean[match.start()-1] == '$' or clean[match.start()-1] == '\\'):
            continue
        start = match.start()
        row = bisect_right(starts, start)
        label = re.search(r'(?<![\w$])([A-Za-z_$][\w$]*|\\\S+)\s*:\s*$',
                          clean[starts[max(0, row-2)]:start])
        if label:
            start = starts[max(0, row-2)] + label.start()
        close = sva_source.after_paren(clean, match.end() - 1)
        if close is None:
            continue
        tail = sva_source.next_token(clean, close)
        action = sva_source.action_end(clean, tail)
        if action is None:
            statement = re.match(r'(?:[A-Za-z_$][\w$]*\s*(?:<=|=))[^;]*;', clean[tail:])
            action = tail + statement.end() if statement else None
        yield dict(label=label[1] if label else None, form=match['form'], start=start, close=close,
                   tail=tail, end=action or close, line=bisect_right(starts, start),
                   expression=clean[match.end():close - 1].strip(), bare=clean[tail:tail + 1] == ';')


def declarations(ast, root=TOP, instance='dut'):
    """Walk elaborated scopes. A generate iteration uses its value, not its ordinal."""
    def walk(node, scope, label='', loop_variable=''):
        if isinstance(node, list):
            for item in node:
                yield from walk(item, scope, label, loop_variable)
        if not isinstance(node, dict) or node.get('isUninstantiated'):
            return
        kind, name = node.get('kind'), node.get('name', '')
        if kind in ('Instance', 'InstanceArray'):
            if not scope and name != root:
                return
            if scope == (root,) and instance is not None and name != instance:
                return
        if kind == 'InstanceBody':
            for member in node.get('members', []):
                yield from walk(member, scope, label, loop_variable)
            return
        if kind in ('NamedValue', 'HierarchicalValue', 'ArbitrarySymbol', 'Property', 'Sequence'):
            return
        if kind in ('Instance', 'InstanceArray', 'GenerateBlockArray', 'GenerateBlock'):
            if kind == 'GenerateBlockArray':
                loop_variable = node.get('loopVariable', '').partition(' ')[2]
            elif node.get('branchKind') == 'LoopIteration' and scope:
                index = next((item['value'] for item in node.get('members', [])
                              if item['kind'] == 'Parameter' and item['name'] == loop_variable),
                             node.get('constructIndex'))
                scope = scope[:-1] + ('{}[{}]'.format(scope[-1], index),)
            if name:
                scope += (name,)
        if kind == 'Block' and node.get('block'):
            block_name = node['block'].partition(' ')[2]
            if node.get('body', {}).get('kind') in ('ConcurrentAssertion', 'ImmediateAssertion'):
                label = block_name
            elif block_name:
                scope += (block_name,)
        if (kind in ('ConcurrentAssertion', 'ImmediateAssertion') and node.get('assertionKind') in ASSERTION_KINDS
                and scope and scope[0] == root and (instance is None or scope[1:2] == (instance,))):
            yield node, '.'.join(scope[1:] if instance is not None else scope), label
            for action in ('ifTrue', 'ifFalse'):
                yield from walk(node.get(action), scope, '', loop_variable)
            return
        for item in node.values():
            if isinstance(item, (dict, list)):
                yield from walk(item, scope, label, loop_variable)

    yield from walk(ast['design'], ())


def relative_source(path, directory, sources):
    resolved = (directory / path).resolve()
    try:
        resolved = resolved.relative_to(directory.resolve())
    except ValueError:
        return None
    name = resolved.as_posix()
    return name if name in sources else None


def collect(ast, directory, sources, root=TOP, instance='dut', origin='rtl'):
    rows = []
    spans = {}
    source_paths = {}
    for node, scope, label in declarations(ast, root, instance):
        kind = ASSERTION_KINDS[node['assertionKind']]
        if origin == 'checker' and kind == 'cover':
            continue
        location = node.get('source_file_start', '')
        if location not in source_paths:
            source_paths[location] = relative_source(location, directory, sources)
        path = source_paths[location]
        if path is None:
            raise ValueError('找不到属性的工程内源码 · ' + (label or scope))
        first, last = node['source_line_start'], node['source_line_end']
        if path not in spans:
            text = sources[path].decode('utf-8-sig')
            spans[path] = (text, text.splitlines(), list(syntax_spans(text)))
        text, lines, statements = spans[path]
        label = label or ''
        candidates = [item for item in statements if item['line'] == first and
                      (item['label'] or '').lstrip('\\') == label]
        if not label and len(candidates) > 1:
            candidates = [item for item in candidates if item['start'] - text.rfind('\n', 0, item['start']) ==
                          node.get('source_column_start')]
        if label:
            hierarchy = scope + '.' + label
        else:
            hierarchy = '{} · {}:{}'.format(scope, Path(path).name, first)
        code = '\n'.join(lines[first-1:last])
        error = None
        if kind == 'cover' and node['kind'] == 'ConcurrentAssertion' and len(candidates) == 1:
            sample = sampled_expression(candidates[0]['expression'])
            if sample and temporal(sample[2]):
                try:
                    lower_cover(sample[2])
                except ValueError as exc:
                    error = str(exc)
        identity = '\0'.join((scope, label, path, str(first), str(node.get('source_column_start', 0))))
        prefix = 'RTL_' if origin == 'rtl' else 'CHECKER_'
        rows.append(dict(name=prefix + hashlib.sha256(identity.encode()).hexdigest()[:24],
                         kind=kind, origin=origin, label=label, scope=scope, hierarchy=hierarchy,
                         file=path, line=first, end_line=last, column=node.get('source_column_start', 0),
                         code=code))
        if error:
            rows[-1]['unsupported'] = error
    rows.sort(key=lambda p: (p['file'], p['line'], p['column'], p['scope'], p['label']))
    if len({p['name'] for p in rows}) != len(rows):
        raise ValueError('有无法区分的同位置属性，请为它们添加不同标签。')
    locations = Counter((p['scope'], p['file'], p['line']) for p in rows if not p['label'])
    cover_locations = Counter((p['scope'], p['file'], p['line']) for p in rows
                              if p['kind'] == 'cover' and not p['label'])
    for row in rows:
        if not row['label'] and locations[(row['scope'], row['file'], row['line'])] > 1:
            if row['kind'] == 'cover' and cover_locations[(row['scope'], row['file'], row['line'])] > 1:
                row['unsupported'] = '同一实例同一行有多条无名 Cover，请为它们加标签以核对回放。'
                row['ambiguous'] = True
            row['hierarchy'] += ':{}'.format(row['column'])
    return rows


def discover(directory, dut, sources, env, run, folder='rtl_snapshot'):
    source_root = directory / folder
    files, includes = write_sources(source_root, dut, sources)
    source_root.mkdir(exist_ok=True)
    wrapper = 'cmdbuf_cover_discovery.sv'
    (source_root / wrapper).write_text('module {} (input wire clk);\n{}\n{}\nendmodule\n'.format(
        TOP, '\n'.join(discovery_ports()), dut['instance']), encoding='utf-8')
    ast = elaborate(source_root, TOP, source_arguments(dut, files + [wrapper], includes), env, run)
    return collect(ast, source_root, sources)


def elaborate(directory, top, arguments, env, run):
    if not shutil.which('slang', path=env.get('PATH')):
        raise RuntimeError('开源工具链缺少 slang，无法展开 SystemVerilog 属性。')
    command = ['slang', '--top', top, '--disable-instance-caching', '--disable-analysis',
               '--ast-json-source-info', '--ast-json', 'studio_ast.json']
    log = directory / 'property_discovery.log'
    if run(command + arguments, directory, log, env):
        raise ValueError(log.read_text(encoding='utf-8', errors='replace')[-4000:].strip())
    return read_json(directory / 'studio_ast.json')


def discovery_ports():
    from dut_config import PORTS

    for name, _, width in PORTS:
        if name == 'clk':
            continue
        if isinstance(width, str):
            yield 'wire [1:0] rd_mem_status [7:0];'
        else:
            yield 'wire {}{};'.format('' if width == 1 else '[{}:0] '.format(width - 1), name)


def formal_rewrites(sources, disabled=()):
    """Lower the same fixed-delay sequences accepted by the attribute editor."""
    rewritten = {}
    disabled = set(disabled)
    for name, raw in sources.items():
        text = raw.decode('utf-8-sig')
        clean = sva_source.mask_sv(text)
        edits = []
        for item in syntax_spans(text):
            sample = sampled_expression(item['expression']) if item['form'] else None
            if (name, item['start']) in disabled:
                label = item['label'] + ': ' if item['label'] else ''
                if not sample:
                    if not item['bare'] and item['end'] <= item['close']:
                        continue
                    statement, end = '', item['end']
                else:
                    statement = label + "cover property (@(posedge {}) 1'b0)".format(sample[0])
                    end = item['close']
                edits.append((item['start'], end, statement + '\n' * text[item['start']:end].count('\n')))
                if not statement:
                    continue
                sample = None
            if sample and temporal(sample[2]):
                clock, reset, condition = sample
                token = hashlib.sha256((name + ':' + str(item['start'])).encode()).hexdigest()[:16]
                history_name = 'cmdbuf_cover_history_' + token
                try:
                    predicate, history = lower_cover(condition, history_name)
                except ValueError:
                    predicate, history = "1'b0", 0
                # Reset the history whenever prior sampled attempts are disabled.
                # The tool's reset is stable across sampling edges.
                prefix = ''
                if history:
                    guard = "if ({}) {} <= '0; else ".format(' '.join(reset.split()), history_name) if reset else ''
                    prefix = ("reg [{width}:0] {flag} = '0; always @(posedge {clock}) "
                              "{guard}{flag} <= ({flag} << 1) | 1'b1; ").format(
                                  width=history - 1, flag=history_name, clock=clock, guard=guard)
                label = item['label'] + ': ' if item['label'] else ''
                disable = ' disable iff ({})'.format(' '.join(reset.split())) if reset else ''
                statement = '{}{}cover property (@(posedge {}){} ({}))'.format(
                    prefix, label, clock, disable, ' '.join(predicate.split()))
                edits.append((item['start'], item['close'], statement + '\n' *
                              text[item['start']:item['close']].count('\n')))
            if not item['bare'] and item['end'] > item['close']:
                edits.append((item['tail'], item['end'], ';' + '\n' * text[item['tail']:item['end']].count('\n')))
        # VCS still executes these simulation controls in the original DUT. They
        # have no transition semantics in the Formal copy.
        for match in SYSTEM_STOP.finditer(clean):
            if any(first <= match.start() < last for first, last, _ in edits):
                continue
            end = sva_source.action_end(clean, match.start())
            if end:
                edits.append((match.start(), end, ';' + '\n' * text[match.start():end].count('\n')))
        for first, last, replacement in sorted(edits, reverse=True):
            text = text[:first] + replacement + text[last:]
        rewritten[name] = text.encode('utf-8') if edits else raw
    return rewritten


def unsupported_declarations(log, directory, result):
    """Identify frontend rejections inside a Cover, by archived source location."""
    spans = {}
    rejected = {}
    for line in log.splitlines():
        location = SOURCE_LOCATION.match(line)
        if not location or 'unsupported SVA feature' not in line:
            continue
        path, row, column = location.groups()
        relative = Path(path).as_posix()
        if not relative.startswith('rtl_formal/'):
            continue
        relative = relative[len('rtl_formal/'):]
        if relative not in result['sources']:
            continue
        if relative not in spans:
            text = (directory/'rtl_snapshot'/relative).read_text(encoding='utf-8-sig')
            spans[relative] = [(item, item['line'] + text[item['start']:item['end']].count('\n'))
                               for item in syntax_spans(text) if item['form']]
        found = [item for item, end in spans[relative] if item['line'] <= int(row) <= end]
        if len(found) == 1 and any(p.get('file') == relative and p['line'] == found[0]['line']
                                   for p in result['functional']['properties']):
            rejected[(relative, found[0]['start'])] = found[0]['line']
    return rejected


def baseline_digest(raw):
    """Fingerprint the sealed baseline after removing passive Cover declarations."""
    text = raw.decode('utf-8-sig')
    for item in reversed(list(syntax_spans(text))):
        if item['form'] and item['bare']:
            text = text[:item['start']] + text[item['end']:]
    text = re.sub(r'(?m)^[ \t]*`ifn?def[ \t]+[\w$]+[ \t]*\n(?:[ \t]*\n)*[ \t]*`endif[ \t]*$', '', text)
    return hashlib.sha256('\n'.join(line.rstrip() for line in text.splitlines() if line.strip()).encode()).hexdigest()


def native_rows(dut, sources, probe):
    from fsdb_formal import toolchain

    with tempfile.TemporaryDirectory(prefix='cmdbuf-dut-cover-') as temporary, toolchain.environment() as env:
        directory = Path(temporary).resolve()
        return discover(directory, dut, sources, env, probe)


def match_netlist(module, rows, source_folder):
    """Map Yosys cover cells to elaborated source occurrences before optimization."""
    cells = {name: cell for name, cell in module.get('cells', {}).items()
             if cell['type'] == '$check' and cell['parameters'].get('FLAVOR') == 'cover'}
    matched, remaining = {}, set(cells)
    for row in rows:
        if not row['label']:
            continue
        cell = row['scope'] + '.' + row['label']
        if cell in remaining:
            remaining.remove(cell)
            matched[row['name']] = cell
    for row in rows:
        if row['name'] in matched:
            continue
        source = (source_folder + '/' + row['file']).replace('\\', '/')
        prefix = '{}:{}'.format(source, row['line'])
        matches = []
        for name in remaining:
            src = cells[name].get('attributes', {}).get('src', '').replace('\\', '/')
            if not src.startswith(prefix + '.') and src != prefix:
                continue
            if name.startswith('$') or name.startswith(row['scope'] + '.'):
                matches.append(name)
        if len(matches) > 1:
            precise = [name for name in matches if cells[name].get('attributes', {}).get('src', '').startswith(
                '{}.{}-'.format(prefix, row['column']))]
            matches = precise or matches
        peers = [other for other in rows if other['file'] == row['file'] and other['line'] == row['line'] and
                 other['label'] == row['label'] and other['name'] not in matched]
        if len(matches) == 1 and matches[0].startswith('$') and len(peers) > 1:
            continue
        if len(matches) == 1:
            matched[row['name']] = matches[0]
            remaining.remove(matches[0])
    return cells, matched


class Netlist:
    """Insert a completion condition after the frontend's clocked Cover sample."""
    def __init__(self, module):
        self.cells = module['cells']
        self.wires = module['netnames']
        self.clock = module['ports']['clk']['bits']
        self.next_bit = 1 + max(bit for net in self.wires.values() for bit in net['bits'] if isinstance(bit, int))

    def bit(self, name, **attributes):
        bits = [self.next_bit]
        self.next_bit += 1
        self.wires[name] = dict(bits=bits, hide_name=0, attributes=attributes)
        return bits

    def cell(self, name, kind, pins, parameters):
        self.cells[name] = dict(type=kind, hide_name=0, connections=pins, parameters=parameters, attributes={})

    def logic(self, name, kind, a, b):
        output = self.bit(name)
        self.cell(name + '_logic', kind, dict(A=a, B=b, Y=output),
                  dict(A_WIDTH='1', B_WIDTH='1', Y_WIDTH='1', A_SIGNED='0', B_SIGNED='0'))
        return output

    def witness(self, name, cell, quiet):
        connections = cell['connections']
        flag = 'fc_seen_' + name
        hit = self.logic(flag + '_hit', '$and', connections['A'], connections['EN'])
        seen = self.bit(flag, init='0', keep='1')
        remembered = self.logic(flag + '_next', '$or', seen, hit)
        self.cell(flag + '_reg', '$dff', dict(CLK=self.clock, D=remembered, Q=seen),
                  dict(WIDTH='1', CLK_POLARITY='1'))
        formal_name = 'FC_C_' + name
        if formal_name in self.cells:
            raise ValueError('DUT Cover 与属性编辑器目标标识冲突 · ' + name)
        self.cells[formal_name] = dict(cell, hide_name=0, connections=dict(A=remembered, EN=quiet))


def prepare_targets(initial, lowered, rows, targeted, source_folder, supported):
    """Use the same eight-cycle completion criterion as editor-authored Cover."""
    initial_module = initial['modules'][TOP]
    lowered_module = lowered['modules'][TOP]
    if not targeted:
        lowered_module['cells'] = {name: cell for name, cell in lowered_module['cells'].items()
                                   if cell['type'] != '$cover'}
        return lowered
    cells, matched = match_netlist(initial_module, rows, source_folder)
    netlist = Netlist(lowered_module)
    complete = netlist.cells.pop('FC_CMDBUF_REPLAY_COMPLETE', None)
    if complete is None:
        raise ValueError('Formal 模型缺少回放收尾条件')
    signals = complete['connections']
    quiet = netlist.logic('fc_rtl_completion', '$and', signals['A'], signals['EN'])
    for row in rows:
        goal, cell_name = row['name'], matched.get(row['name'])
        if goal not in targeted or goal in supported:
            continue
        if row.get('unsupported'):
            supported[goal] = row['unsupported']
            continue
        if not cell_name or cell_name not in netlist.cells:
            supported[goal] = '当前 Formal 模型中无法定位该 Cover 实例'
            continue
        cell = cells[cell_name]
        trigger = cell['connections']['TRG']
        params = cell['parameters']
        if trigger and (trigger != initial_module['ports']['clk']['bits'] or params['TRG_POLARITY'] != '1'):
            supported[goal] = '当前回放模型只支持 DUT 顶层 clk 上升沿'
            continue
        netlist.witness(goal, netlist.cells[cell_name], quiet)
    for name in cells:
        source = cells[name].get('attributes', {}).get('src', '')
        if not name.startswith('FC_C_') or not source.startswith('formal_top.sv:'):
            netlist.cells.pop(name, None)
    return lowered


def load_current(dut=None):
    import dut_config

    dut = dut or dut_config.load()
    return dut, {path.relative_to(dut_config.ROOT).as_posix(): path.read_bytes()
                 for path in dut_config.source_files(dut)}


def resolve_targets(properties, requested):
    names = {row['name'] for row in properties if row['kind'] == 'cover'}
    scopes = {row['hierarchy']: row['name'] for row in properties if row.get('origin') == 'rtl'}
    for label in requested:
        yield scopes.get(label, label) if label not in names else label


def probe(command, directory, log, env):
    import subprocess

    with log.open('wb') as stream:
        return subprocess.run(command, cwd=str(directory), env=env, stdout=stream,
                              stderr=subprocess.STDOUT).returncode


def latest_inventory(dut=None, sources=None, run=probe, covers_only=True):
    """Reuse the exact same elaborated inventory across GUI, VCS and Formal."""
    if sources is None:
        dut, sources = load_current(dut)
    if covers_only and not may_declare_cover(dut.get('instance', '')) and not any(
            may_declare_cover(raw.decode('utf-8-sig', errors='replace')) for raw in sources.values()):
        return []
    import eda
    key = inventory_key(dut, sources, eda.digest(Path(__file__)))
    cache = eda.OUT / 'dut_property_inventory' / (key + '.json')
    if cache.is_file():
        rows = eda.read_json(cache)
    else:
        rows = native_rows(dut, sources, run)
        eda.write_json(cache, rows)
    return [row for row in rows if row['kind'] == 'cover'] if covers_only else rows


def inventory_key(dut, sources, *adapters):
    from fsdb_formal import toolchain

    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    return hashlib.sha256(json.dumps([dut, hashes, adapters, toolchain.runtime_identity()],
                                    sort_keys=True).encode()).hexdigest()


def native_hits(log, properties, require_complete):
    rows = [p for p in properties if p.get('origin') == 'rtl' and p['kind'] == 'cover']
    if not rows:
        return set()
    pattern = re.compile(r'^FUNCTIONAL_RTL_COVER\t([^\t\n]+)\t([^\t\n]+)\t(\d+)\t(\d+)$', re.M)
    native = {name: (file.replace('\\', '/'), int(line), int(count))
              for name, file, line, count in pattern.findall(log)}
    complete = re.findall(r'^FUNCTIONAL_RTL_COMPLETE\s*$', log, re.M)
    if require_complete and len(complete) != 1:
        raise ValueError('DUT Cover 缺少完整的 VCS 命中记录')
    hits = set()
    for row in rows:
        if row.get('ambiguous'):
            continue
        scope = 'tb_cmdbuf_rd.' + row['scope'] + '.'
        if row['label']:
            matches = [native[scope + row['label']]] if scope + row['label'] in native else []
        else:
            matches = [item for name, item in native.items()
                       if name.startswith(scope) and name.rsplit('.', 1)[-1].startswith('unnamed$')
                       and (item[0] == row['file'] or item[0].endswith('/' + row['file']) or
                            Path(item[0]).name == Path(row['file']).name)
                       and item[1] == row['line']]
        if len(matches) == 1:
            if matches[0][2] > 0:
                hits.add(row['name'])
        elif require_complete:
            raise ValueError('VCS 无法唯一定位 DUT Cover · ' + row['hierarchy'])
    return hits
