"""Source navigation for the active DUT and the environment port checker."""
from pathlib import Path
import re
import tempfile

from fsdb_formal import toolchain
from check_policy import catalog
import functional_properties as props
import rtl_cover
import sva_source


SCAN_ERRORS = (OSError, ValueError, RuntimeError)
CHECKER_TOP = 'cmdbuf_rd_port_sva'
STATEMENT = re.compile(r'(?<![\w$\\])(?:(?P<label>[A-Za-z_$][\w$]*)\s*:\s*)?'
                       r'(?P<kind>assert|assume|cover)\s*(?:property\b|sequence\b|final\b|#\s*0)?\s*\(')


def mark_formal_use(rows, source, path):
    included = set()
    if source is not None:
        try:
            included = {(row['name'], row['kind']) for row in props.builtin_properties(source.decode('utf-8-sig'))}
        except ValueError:
            pass
    strict = set(catalog()['strict_native'])
    for row in rows:
        if row['file'] == path and (row['label'], row['kind']) in included:
            row['formal_use'] = True
        if row['label'] in strict:
            row['strict'] = True


def scan_checker(dut, port_file, sources, run):
    import eda

    includes = list(dict.fromkeys([str(Path(port_file).parent)] + dut['include_dirs']))
    config = dict(dut, files=[port_file], include_dirs=includes)
    key = rtl_cover.inventory_key(config, sources, eda.digest(__file__), eda.digest(rtl_cover.__file__),
                                  eda.digest(props.__file__), catalog()['strict_native'])
    cache = eda.OUT / 'checker_property_inventory' / (key + '.json')
    if cache.is_file():
        return eda.read_json(cache)
    with toolchain.environment() as env, tempfile.TemporaryDirectory(prefix='cmdbuf-checker-') as name:
        directory = Path(name).resolve()
        rtl_cover.write_sources(directory, config, sources)
        args = rtl_cover.source_arguments(dut, [port_file], includes)
        ast = rtl_cover.elaborate(directory, CHECKER_TOP, args, env, run)
        rows = rtl_cover.collect(ast, directory, sources, CHECKER_TOP, None, origin='checker')
    mark_formal_use(rows, sources.get(port_file), port_file)
    eda.write_json(cache, rows)
    return rows


def source_inventory(sources, origin):
    """Show declarations from source when the frontend cannot elaborate them."""
    rows = []
    for path, data in sources.items():
        text = data.decode('utf-8-sig', errors='replace')
        lines = text.splitlines()
        clean = sva_source.mask_sv(text)
        clean = rtl_cover.MACRO_DEFINITION.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), clean)
        for match in STATEMENT.finditer(clean):
            kind = match['kind']
            if origin == 'checker' and kind == 'cover':
                continue
            end = sva_source.after_paren(clean, match.end()-1)
            if end is None:
                continue
            tail = sva_source.next_token(clean, end)
            following = sva_source.action_end(clean, tail)
            end = following or end
            tail = sva_source.next_token(clean, end)
            failure = re.match(r'else\b\s*', clean[tail:])
            if failure:
                end = sva_source.action_end(clean, tail + failure.end()) or end
            line = clean.count('\n', 0, match.start()) + 1
            last = clean.count('\n', 0, end) + 1
            label = match['label'] or '{}@{}'.format(kind.capitalize(), line)
            rows.append(dict(name='SOURCE:{}:{}:{}'.format(origin, path, match.start()), kind=kind,
                             origin=origin, label=label, hierarchy=label, file=path, line=line,
                             end_line=last, code='\n'.join(lines[line-1:last]), source_only=True))
    return rows


def inventory(root, project, run=rtl_cover.probe, expand=True):
    rows, issues = [], {}
    checker_sources = {}
    for name in [project['checker']] + project['checker_includes']:
        try:
            checker_sources[name] = (root / name).read_bytes()
        except OSError as exc:
            issues['checker'] = str(exc)
    dut = dict(defines=[], include_dirs=[])
    sources = {}
    try:
        dut, sources = rtl_cover.load_current()
        if expand:
            rows.extend(rtl_cover.latest_inventory(dut, sources, run, covers_only=False))
        else:
            rows.extend(source_inventory(sources, 'rtl'))
    except SCAN_ERRORS as exc:
        issues['rtl'] = str(exc)
        rows.extend(source_inventory(sources, 'rtl'))

    if expand and 'checker' not in issues:
        try:
            rows.extend(scan_checker(dut, project['checker'], {**sources, **checker_sources}, run))
        except SCAN_ERRORS as exc:
            issues['checker'] = str(exc)
            rows.extend(source_inventory(checker_sources, 'checker'))
    else:
        rows.extend(source_inventory(checker_sources, 'checker'))
    return dict(properties=rows, issues=issues)


def usage(row):
    if row.get('source_only'):
        return '源码声明 · 实例待确认'
    if row.get('origin') != 'checker':
        return ''
    if row.get('formal_use'):
        return '用于开源 Formal 输入约束' if row['kind'] == 'assume' else '纳入「检查 Assert」'
    return '完整时序检查模式' if row.get('strict') else '环境仿真检查器'
