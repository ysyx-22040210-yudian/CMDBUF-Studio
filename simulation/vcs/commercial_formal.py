"""Run native SystemVerilog properties through a workstation-local Formal adapter."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time

import dut_config
import eda
import execution
import functional_properties as props
from fsdb_formal import core, proof
from fsdb_formal.engine import package_rtl
from sva_source import mask_sv


SCHEMA = 'test_mode.commercial_formal.v1'
TOP = 'test_mode_commercial_formal_top'
CHECKS = {'cover': ('cover',), 'bmc': ('assert',), 'prove': ('assert', 'cover')}
STATES = {
    'assert': {'PROVEN', 'BOUNDED_PASS', 'COUNTEREXAMPLE', 'VACUOUS', 'UNKNOWN', 'TIMEOUT', 'ERROR'},
    'cover': {'REACHED', 'UNREACHED_IN_BOUND', 'UNREACHABLE', 'UNKNOWN', 'TIMEOUT', 'ERROR'},
}
EXIT = {'PASS': 0, 'PREPARED': 0, 'FAIL': 1, 'ERROR': 2, 'INCOMPLETE': 3, 'CANCELLED': 130}
DEFINE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_'.:-]+)?")


def contained(folder, name, directory=False):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or
            any(ord(ch) < 32 for ch in name)):
        raise ValueError('无效的相对路径: ' + repr(name))
    relative = PurePosixPath(name)
    path = (folder / name).resolve()
    if (relative.is_absolute() or '..' in relative.parts or
            (folder not in path.parents and not (directory and path == folder)) or
            not (path.is_dir() if directory else path.is_file())):
        raise ValueError('路径不存在或超出任务目录: ' + name)
    return path


def compile_inputs(source, extra_defines):
    """Decode Studio's native file list into arguments without shell or Tcl expansion."""
    files, include_dirs, defines = [], [], []
    filelist = source / 'files.f'
    text = filelist.read_text(encoding='utf-8-sig')
    for line in text.splitlines():
        word = line.strip()
        if not word:
            continue
        if word.startswith('"'):
            word = json.loads(word)
        if word.startswith('+incdir+'):
            include = word[len('+incdir+'):]
            contained(source, include, directory=True)
            include_dirs.append(include)
        elif word.startswith('+define+'):
            defines.append(word[len('+define+'):])
        else:
            path = contained(source, word)
            if path.suffix.lower() not in ('.sv', '.v') or word.startswith(('-', '+')):
                raise ValueError('无效的 RTL 或原生 SVA 文件项: ' + word)
            files.append(word)
    if not files or len(files) != len(set(files)):
        raise ValueError('原生编译清单为空或包含重复文件')
    names = {define.split('=', 1)[0] for define in defines}
    for define in extra_defines:
        name = define.split('=', 1)[0]
        if name in names:
            raise ValueError('附加编译宏与已有定义重复: ' + name)
        names.add(name)
    defines += list(extra_defines)
    if any(not DEFINE.fullmatch(define) for define in defines):
        raise ValueError('编译宏须为 NAME 或 NAME=value')
    if extra_defines:
        filelist.write_text(''.join('+define+' + define + '\n' for define in extra_defines) + text,
                            encoding='utf-8')
    return dict(directory='source', filelist='files.f', files=files,
                include_dirs=include_dirs, defines=defines,
                sha256={path.relative_to(source).as_posix(): eda.digest(path)
                        for path in sorted(source.rglob('*')) if path.is_file()})


def dut_snapshot(source):
    inventory = eda.contracts.audit(allow_rtl_covers=True)
    config = eda.HERE / 'dut.json'
    config_hash = eda.digest(config)
    dut = dut_config.load()
    dut['files'] = [dut_config.project_path(p).relative_to(eda.ROOT).as_posix() for p in dut['files']]
    dut['include_dirs'] = [dut_config.project_path(p, True).relative_to(eda.ROOT).as_posix()
                           for p in dut['include_dirs']]
    rtl = {p.relative_to(eda.ROOT).as_posix(): p.read_bytes() for p in dut_config.source_files(dut)}
    files, includes = package_rtl(source, dut, rtl)
    for include in includes:
        (source / include).mkdir(parents=True, exist_ok=True)
    checker = eda.PROJECT['checker']
    for relative in [checker] + eda.PROJECT['checker_includes']:
        data = (eda.ROOT / relative).read_bytes()
        if eda.hashlib.sha256(data).hexdigest() != inventory['sources'][relative]:
            raise ValueError('取快照时环境检查器发生改变: ' + relative)
        (source / Path(relative).name).write_bytes(data)
    if any(eda.hashlib.sha256(rtl[name]).hexdigest() != expected
           for name, expected in inventory['sources'].items() if name in rtl):
        raise ValueError('取快照时基准 RTL 发生改变')
    if eda.digest(config) != config_hash:
        raise ValueError('取快照时 DUT 配置发生改变')
    checker_name = Path(checker).name
    module = re.search(r'\bmodule\s+([A-Za-z_][\w$]*)',
                       mask_sv((source / checker_name).read_text(encoding='utf-8-sig')))
    if not module:
        raise ValueError('未找到环境检查器 module')
    (source / 'user_properties.sv').write_bytes(props.USER_FILE.read_bytes())
    top = ['`default_nettype none', 'module {} ('.format(TOP),
           ',\n'.join(core.declarations(True)), ');', dut['instance'],
           '{} port_checks (.*);'.format(module[1])]
    top += props.helpers(1) + ['`include "user_properties.sv"', 'endmodule', '`default_nettype wire', '']
    (source / 'formal_top.sv').write_text('\n'.join(top), encoding='utf-8')
    args = ['+incdir+.'] + ['+incdir+' + path for path in includes] + [
        '+define+' + define for define in dut['defines']] + files + [checker_name, 'formal_top.sv']
    args = [json.dumps(arg, ensure_ascii=False) if re.search(r'\s', arg) else arg for arg in args]
    (source / 'files.f').write_text('\n'.join(args) + '\n',
                                    encoding='utf-8')
    return dict(input_kind='dut', design=dut['name'], top=TOP,
                clock='clk', reset='rst_n', selectors=[], contract_revision=inventory['revision'])


def package_snapshot(source, package):
    package = Path(package).expanduser().resolve()
    manifest = eda.read_json(package / 'SHA256SUMS.json')
    proof.copy_inputs(package, source, manifest, files=(
        'files.f', 'integration.json', 'summary.json', 'trace_properties.sv', 'formal_top.sv'))
    summary = eda.read_json(source / 'summary.json')
    integration = eda.read_json(source / 'integration.json')
    covers = proof.terminal_covers(summary, source, checker='trace_properties.sv')
    sva = mask_sv((source / 'trace_properties.sv').read_text(encoding='utf-8'))
    asserts = set(re.findall(r'\b([A-Za-z_][\w$]*)\s*:\s*assert\s+property\b', sva))
    if not asserts or integration.get('filelist') != 'files.f' or integration.get('native_checker') != 'trace_properties.sv':
        raise ValueError('FSDB 包缺少可识别的原生验证入口')
    return dict(input_kind='fsdb_package', design=summary['dut_name'], export_id=summary['id'],
                top=integration['top'], clock=integration['clock'], reset=integration['active_low_reset'],
                selectors=integration['selectors'], package_depth=integration['depth'],
                required={'cover': sorted(covers), 'assert': sorted(asserts)})


def target_list(raw):
    names = [name.strip() for name in raw.split(',')] if raw.strip() else []
    if (len(names) != len(set(names)) or any(not name or any(ord(ch) < 32 for ch in name) for name in names)):
        raise ValueError('目标须为不重复的属性标签或完整实例名，使用逗号分隔')
    return names


def build_request(directory, mode, depth, timeout, targets, package, defines=()):
    source = (directory / 'source').resolve()
    source.mkdir()
    design = package_snapshot(source, package) if mode == 'prove' else dut_snapshot(source)
    required = design.pop('required', {})
    kinds = CHECKS[mode]
    depth = core.number(depth if depth is not None else design.get('package_depth', 48), 'Formal 深度', 1, 1000000)
    for field in ('top', 'clock', 'reset'):
        if not isinstance(design[field], str) or not re.fullmatch(r'[A-Za-z_][\w$]*', design[field]):
            raise ValueError('无效的 Formal {} 名称'.format(field))
    return dict(schema=SCHEMA, run_id=directory.name, mode=mode, inputs=compile_inputs(source, defines),
                design=dict(design, clock_edge='posedge', reset_active=0, initialize_reset=True),
                depth=depth, solve_timeout_seconds=timeout, kinds=list(kinds), targets=targets,
                required_labels={'cover': required.get('cover', []), 'assert': required.get('assert', [])})


def normalize(directory, request, request_sha256, answer):
    if (not isinstance(answer, dict) or answer.get('schema') != SCHEMA or
            answer.get('run_id') != request['run_id'] or answer.get('request_sha256') != request_sha256):
        raise ValueError('适配器结果的协议版本、运行编号或请求 SHA-256 不匹配')
    engine = answer.get('engine')
    complete = answer.get('complete')
    rows = answer.get('properties')
    if (not isinstance(engine, dict) or not all(isinstance(engine.get(k), str) and engine[k].strip()
                                                for k in ('name', 'version')) or
            not isinstance(complete, bool) or not isinstance(rows, list)):
        raise ValueError('适配器结果必须包含工具名称、版本、完成标记和逐属性结果')
    reports = answer.get('reports', [])
    if not isinstance(reports, list):
        raise ValueError('reports 必须是任务目录内的文件列表')
    reports = [contained(directory, name).relative_to(directory).as_posix() for name in reports]
    properties, names = [], set()
    labels = {kind: set() for kind in request['kinds']}
    targets = set(request['targets'])
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('无效的逐属性结果')
        name, label, kind, status = (row.get(k) for k in ('name', 'label', 'kind', 'status'))
        if (not all(isinstance(x, str) and x.strip() for x in (name, label, kind, status)) or
                (kind, name) in names or kind not in labels or status not in STATES[kind]):
            raise ValueError('属性名称重复，或类型 / 状态与任务不匹配: ' + repr(name))
        if targets and name not in targets and label not in targets:
            raise ValueError('适配器返回了未选中的属性: ' + name)
        names.add((kind, name))
        labels[kind].add(label)
        depth = row.get('depth')
        if depth is not None and (type(depth) is not int or depth < 0):
            raise ValueError('无效的属性深度: ' + name)
        if status in ('REACHED', 'BOUNDED_PASS', 'UNREACHED_IN_BOUND') and depth is None:
            raise ValueError('有界结果需要报告实际检查深度: ' + name)
        if status == 'REACHED' and depth is not None and depth > request['depth']:
            raise ValueError('Cover 命中超出请求深度: ' + name)
        trace = row.get('trace')
        if trace is not None:
            path = contained(directory, trace)
            if path.suffix.lower() != '.vcd':
                raise ValueError('供 Studio 查看时请将逐属性轨迹导出为 VCD: ' + name)
            trace = path.relative_to(directory).as_posix()
        properties.append(dict(name=name, label=label, kind=kind, status=status, depth=depth, trace=trace))
    missing = sorted(targets - {item for row in properties for item in (row['name'], row['label'])})
    for kind in request['kinds']:
        missing.extend(sorted(set(request['required_labels'][kind]) - labels[kind]))
        if not labels[kind]:
            missing.append('无 ' + kind + ' 结果')
    states = {row['status'] for row in properties}
    all_met = all(row['status'] in ('REACHED', 'PROVEN') or
                  row['status'] == 'BOUNDED_PASS' and row['depth'] >= request['depth']
                  for row in properties)
    verdict = ('ERROR' if 'ERROR' in states else 'FAIL' if 'COUNTEREXAMPLE' in states else
               'PASS' if complete and not missing and all_met else 'INCOMPLETE')
    detail = answer.get('message', '')
    if not isinstance(detail, str):
        raise ValueError('适配器说明须为文本')
    return dict(status=verdict, engine={k: engine[k] for k in ('name', 'version')}, complete=complete, properties=properties,
                missing=missing, reports=reports, message=detail)


def run(mode, run_id, depth, timeout, targets, package, adapter, prepare, defines=()):
    if bool(package) != (mode == 'prove') or (targets and mode == 'prove'):
        raise ValueError('验证包任务须提供解压目录，DUT 任务才接受目标选择')
    if timeout < 0:
        raise ValueError('求解超时须为非负整数，0 表示不限时')
    directory = (eda.OUT / 'commercial_formal' / proof.identifier(run_id or (
        eda.stamp() + '_' + eda.uuid.uuid4().hex[:8]))).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    record = dict(id=directory.name, mode=mode, status='RUNNING', started_at=time.time())
    result_path = directory / 'result.json'
    eda.write_json(result_path, record)
    try:
        print('FORMAL_STAGE 准备原生 RTL 与 SVA 快照', flush=True)
        request = build_request(directory, mode, depth, timeout, targets, package, defines)
        request_path = directory / 'request.json'
        eda.write_json(request_path, request)
        request_sha256 = eda.digest(request_path)
        record.update(request='request.json', request_sha256=request_sha256, design=request['design'],
                      depth=request['depth'])
        if prepare:
            record['status'] = 'PREPARED'
        else:
            if sys.platform != 'linux':
                raise RuntimeError('请在装有商业 Formal 的 Linux 设备上运行适配器')
            site = eda.platform_config.load()
            adapter = adapter or site['formal_adapter']
            env = eda.platform_config.tool_env(required=()) if site['tool_setup'] else os.environ.copy()
            program = shutil.which(os.path.expanduser(adapter), path=env.get('PATH')) if adapter else None
            if not program:
                raise ValueError('请配置可执行适配器路径（商业 Formal 或 EDA_COMMERCIAL_FORMAL_ADAPTER）')
            program = str(Path(program).resolve())
            print('FORMAL_STAGE 运行商业 Formal 适配器', flush=True)
            raw_path = directory / 'adapter-result.json'
            record.update(adapter=program, log='adapter.log')
            # License checkout may wait. The adapter owns the solver-only limit.
            rc, _ = eda.execute([program, str(request_path), str(raw_path)], directory,
                                directory / record['log'], env, None, phase='commercial_formal')
            if rc:
                raise RuntimeError('适配器退出码 {}，请查看 adapter.log'.format(rc))
            print('FORMAL_STAGE 核对原生输入与逐属性结果', flush=True)
            source = directory / 'source'
            if (eda.digest(request_path) != request_sha256 or any(
                    eda.digest(contained(source, name)) != sha for name, sha in request['inputs']['sha256'].items())):
                raise ValueError('适配器运行期间修改了请求或原生输入，请在任务工作目录的副本上操作')
            record.update(normalize(directory, request, request_sha256,
                                    eda.read_json(contained(directory, raw_path.name))))
    except KeyboardInterrupt:
        record['status'] = 'CANCELLED'
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        record.update(status='ERROR', error=str(exc))
        print('COMMERCIAL_FORMAL_ERROR ' + str(exc), file=sys.stderr, flush=True)
    record['finished_at'] = time.time()
    eda.write_json(result_path, record)
    print('COMMERCIAL_FORMAL_RESULT ' + str(result_path), flush=True)
    return EXIT[record['status']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=CHECKS, default=os.environ.get('COMMERCIAL_MODE') or 'cover')
    parser.add_argument('--run-id', default=os.environ.get('COMMERCIAL_RUN_ID'))
    parser.add_argument('--depth', type=int, default=os.environ.get('COMMERCIAL_DEPTH') or None)
    parser.add_argument('--timeout', type=int, default=os.environ.get('COMMERCIAL_TIMEOUT') or '600')
    parser.add_argument('--targets', default=os.environ.get('COMMERCIAL_TARGETS', ''))
    parser.add_argument('--defines', default=os.environ.get('COMMERCIAL_DEFINES', ''))
    parser.add_argument('--package', default=os.environ.get('COMMERCIAL_PACKAGE'))
    parser.add_argument('--adapter', default=os.environ.get('COMMERCIAL_ADAPTER'))
    parser.add_argument('--prepare', action='store_true', default=os.environ.get('COMMERCIAL_PREPARE') == '1')
    args = parser.parse_args()
    execution.install_signal_handlers()
    try:
        if args.mode not in CHECKS:
            raise ValueError('COMMERCIAL_MODE 须为 cover、bmc 或 prove')
        defines = [item.strip() for item in args.defines.split(',')] if args.defines.strip() else []
        return run(args.mode, args.run_id, args.depth, args.timeout, target_list(args.targets),
                   args.package, args.adapter, args.prepare, defines)
    except (OSError, ValueError) as exc:
        print('COMMERCIAL_FORMAL_ERROR ' + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
