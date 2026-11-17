"""Convert archived commercial Cover witnesses into Studio VCS pin replay cases."""
from collections import defaultdict
from contextlib import closing
from itertools import groupby
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory

import commercial_formal as commercial
import eda
import execution
import functional_properties as props
from fsdb_formal.engine import file_hash
from fsdb_formal.proof import identifier
from gui import vcd
from wavedrom_adapter import bits


STIMULUS_SCHEMA = 'test_mode.cover_stimulus.v1'
MAX_FRAMES = 1000000


def witness_field(row):
    if row['kind'] == 'cover' and row['status'] == 'REACHED':
        return next((field for field in ('stimulus', 'trace') if row.get(field)), None)
    return None


def json_frames(path):
    data = eda.read_json(path)
    frames = data.get('frames') if isinstance(data, dict) and data.get('schema') == STIMULUS_SCHEMA else None
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('逐拍 JSON 须提供 {} 格式的 1–{} 拍输入'.format(STIMULUS_SCHEMA, MAX_FRAMES))
    ports = dict(props.INPUTS)
    result = []
    for number, frame in enumerate(frames, 1):
        if not isinstance(frame, dict) or frame.keys() != ports.keys():
            raise ValueError('逐拍 JSON 第 {} 拍须提供全部 DUT 输入端口'.format(number))
        inputs = {}
        for name, width in ports.items():
            value = bits(frame[name], width, '第 {} 拍 {}'.format(number, name))
            if 'x' in value or 'z' in value:
                raise ValueError('第 {} 拍 {} 含 X/Z，VCS 回放需要确定的输入'.format(number, name))
            inputs[name] = int(value, 2)
        result.append(props.packed(inputs))
    return result


def vcd_ports(db, design):
    widths = dict(props.INPUTS + [(design['clock'], 1), (design['reset'], 1)])
    scopes = defaultdict(lambda: defaultdict(set))
    for code, signal, width in db.execute('SELECT code,name,width FROM signals'):
        scope, _, reference = signal.rpartition('.')
        if scope.rsplit('.', 1)[-1] != design['top']:
            continue
        name = re.sub(r'\[-?\d+:-?\d+\]$', '', reference.lstrip('\\'))
        if widths.get(name) == width:
            scopes[scope][name].add(code)
    complete = [mapping for mapping in scopes.values() if mapping.keys() == widths.keys()
                and all(len(codes) == 1 for codes in mapping.values())]
    if not complete:
        closest = max(scopes.values(), key=len, default={})
        missing = [name for name in widths if len(closest.get(name, ())) != 1]
        raise ValueError('VCD 须在 {} 顶层包含时钟、复位和全部 DUT 输入。缺少或歧义：{}'.format(
            design['top'], '、'.join(missing)))
    if len(complete) != 1:
        raise ValueError('VCD 有多个匹配的 Formal 顶层，请通过逐拍 JSON 提供确定的端口映射')
    return {name: next(iter(codes)) for name, codes in complete[0].items()}


def vcd_frames(path, design, stop_file):
    """Use values strictly before each rising edge, independently of VCD timescale."""
    with TemporaryDirectory(prefix='studio-commercial-vcd-') as cache:
        database = vcd.index(path, cache)
        with closing(sqlite3.connect(str(database))) as db:
            ports = vcd_ports(db, design)
            clock, reset = ports[design['clock']], ports[design['reset']]
            codes = list(set(ports.values()))
            events = db.execute('SELECT t,code,v FROM changes WHERE code IN ({}) ORDER BY t,rowid'.format(
                ','.join('?' for _ in codes)), codes)
            values, frames = {}, []
            for timestamp, group in groupby(events, key=lambda event: event[0]):
                execution.check_stop(stop_file)
                updates = {}
                for _, code, value in group:
                    if code in (clock, reset) and code in updates:
                        raise ValueError('VCD 同一时刻有多个时钟或复位跳变: {}'.format(timestamp))
                    updates[code] = value
                if values.get(clock) == '0' and updates.get(clock) == '1':
                    reset_value = values.get(reset)
                    if reset_value == '1':
                        inputs = {}
                        for name, width in props.INPUTS:
                            raw = values.get(ports[name], '')
                            if not raw or len(raw) > width or set(raw) - {'0', '1'}:
                                raise ValueError('VCD 时刻 {} 的沿前输入 {} 缺失或含 X/Z'.format(timestamp, name))
                            inputs[name] = int(raw, 2)
                        frames.append(props.packed(inputs))
                    elif reset_value != '0':
                        raise ValueError('VCD 时刻 {} 的沿前复位值不确定'.format(timestamp))
                if frames and (updates.get(reset, '1') != '1' or updates.get(clock, '0') not in ('0', '1')):
                    raise ValueError('VCS Cover 回放需要复位释放后的连续单时钟轨迹')
                if len(frames) > MAX_FRAMES:
                    raise ValueError('VCD 轨迹超过 VCS 回放的 {} 拍上限'.format(MAX_FRAMES))
                values.update(updates)
    if not frames:
        raise ValueError('VCD 缺少复位释放后的上升沿输入采样')
    return frames


def prepare(run_id, selections=(), stop_file=None):
    root = (eda.OUT / 'commercial_formal').resolve()
    folder = commercial.contained(root, identifier(run_id), directory=True)
    record = eda.read_json(commercial.contained(folder, 'result.json'))
    if record.get('id') != run_id or record.get('mode') != 'cover':
        raise ValueError('请选择商业 Formal 的 DUT Cover 任务')
    request_path = commercial.contained(folder, 'request.json')
    request_sha256 = eda.digest(request_path)
    request = eda.read_json(request_path)
    if (request_sha256 != record.get('request_sha256') or request.get('run_id') != run_id or
            request.get('schema') != commercial.SCHEMA or request.get('mode') != 'cover'):
        raise ValueError('商业 Formal 请求已改变或与任务记录不一致')
    design = request['design']
    if (design.get('input_kind'), design.get('top'), design.get('clock'), design.get('reset'),
            design.get('clock_edge'), design.get('reset_active'), design.get('initialize_reset')) != (
            'dut', commercial.TOP, 'clk', 'rst_n', 'posedge', 0, True):
        raise ValueError('商业 Cover 回放需要 Studio DUT 顶层的单时钟和低有效复位')
    available = {'p{}'.format(index): row for index, row in enumerate(record.get('properties', ()))
                 if witness_field(row)}
    if not available:
        raise ValueError('该任务没有带 VCD 或逐拍 JSON 的已命中 Cover')
    if selections and (len(set(selections)) != len(selections) or not set(selections).issubset(available)):
        raise ValueError('选中的 Cover 已失效、重复或缺少可回放轨迹')

    source = commercial.contained(folder, request['inputs']['directory'], directory=True)
    for name, digest in request['inputs']['sha256'].items():
        if eda.digest(commercial.contained(source, name)) != digest:
            raise ValueError('商业 Formal 快照已改变: ' + name)

    cases, witnesses = {}, {}
    origin = dict(run_id=run_id, engine=record['engine'], request_sha256=request_sha256)
    for key in selections or available:
        execution.check_stop(stop_file)
        row = available[key]
        field = witness_field(row)
        relative = row[field]
        path = commercial.contained(folder, relative)
        archived_hash = row.get(field + '_sha256')
        if (field, path) not in witnesses:
            digest = file_hash(path)
            if archived_hash and digest != archived_hash:
                raise ValueError('商业 Formal 轨迹已改变: ' + relative)
            try:
                frames = json_frames(path) if field == 'stimulus' else vcd_frames(path, design, stop_file)
            except (ValueError, OSError, sqlite3.Error) as exc:
                raise ValueError('{} · {}: {}'.format(row['name'], relative, exc)) from exc
            if digest != file_hash(path):
                raise ValueError('读取期间商业 Formal 轨迹发生改变: ' + relative)
            name = 'cf_' + run_id.replace('-', '_') + '_' + key
            info = dict(origin, artifact=relative, artifact_sha256=digest, properties=[])
            case = dict(name=name, format='formal_cover', campaign=run_id, trace=key,
                        frames=['{:x}'.format(value) for value in frames], commercial=info)
            witnesses[field, path] = case
            cases[name] = (name, case)
        case = witnesses[field, path]
        if archived_hash and archived_hash != case['commercial']['artifact_sha256']:
            raise ValueError('同一商业 Formal 轨迹的摘要不一致: ' + relative)
        case['commercial']['properties'].append({k: row[k] for k in ('name', 'label', 'depth')})
    return dict(request=request, source=source, origin=origin, cases=cases)


def check_build(replay, build):
    """Check the frozen RTL, wiring and properties against the simulator we will run."""
    inputs = replay['request']['inputs']
    design = replay['request']['design']
    source = replay['source']
    dut, hashes = build['dut'], build['sources']
    prefix = 'rtl_snapshot/'
    rtl = {name[len(prefix):]: digest for name, digest in inputs['sha256'].items() if name.startswith(prefix)}
    includes = tuple(name.rstrip('/') + '/' for name in dut['include_dirs'])
    sim_rtl = {name: digest for name, digest in hashes.items() if name in dut['files'] or
               (name.startswith(includes) and name.endswith(('.v', '.sv', '.vh', '.svh')))}
    manifest = build.get('functional', {})
    checker = eda.PROJECT['checker']
    checker_paths = [checker] + eda.PROJECT['checker_includes']
    checker_name = Path(checker).name
    # Extra commercial flags precede the generated +incdir+. in this file.
    # The DUT's own defines remain after it, in their original order.
    filelist = (source / inputs['filelist']).read_text(encoding='utf-8-sig').splitlines()
    defines = [line[len('+define+'):] for line in filelist[filelist.index('+incdir+.') + 1:]
               if line.startswith('+define+')]
    wrapper = commercial.dut_wrapper(dut['instance'], (source / checker_name).read_text(encoding='utf-8-sig'))
    if (rtl != sim_rtl or (source / 'formal_top.sv').read_text(encoding='utf-8') != wrapper or
            inputs['files'] != [prefix + path for path in dut['files']] + [checker_name, 'formal_top.sv'] or
            inputs['include_dirs'] != ['.'] + [prefix + path for path in dut['include_dirs']] or
            defines != dut['defines'] or design['design'] != dut['name'] or
            design['contract_revision'] != build['contract_revision'] or
            manifest.get('sha256') != inputs['sha256']['user_properties.sv'] or
            any(hashes.get(path) != inputs['sha256'][Path(path).name] for path in checker_paths)):
        raise ValueError('商业 Formal 快照与 VCS 的 DUT、属性或约束版本不同，请用当前工程重新搜索')

    aliases = {(row.get('hierarchy', row['name']), row.get('label', row['name'])): row['name']
               for row in manifest['properties'] if row['kind'] == 'cover'}
    top = design['top'] + '.'
    for _, case in replay['cases'].values():
        goals = {}
        for row in case['commercial']['properties']:
            name = row['name']
            relative = name[len(top):] if name.startswith(top) else name
            target = aliases.get((relative, row['label']))
            if target is None:
                raise ValueError('VCS 编译版本中找不到同一 Cover 实例: ' + name)
            goals[target] = row['depth']
        case['goals'] = goals
