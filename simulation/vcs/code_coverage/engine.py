"""Read historical VDBs with Verdi NPI and verify exact minimum coverage sets."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import eda
import execution
from code_coverage.set_cover import bits, minimum_cover, popcount, union
from functional_properties import cover_names


VDBS_PER_READER = 100


def result_file(identifier):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier or ''):
        raise ValueError('无效的覆盖率分析编号')
    path = eda.OUT / 'coverage_analysis' / identifier / 'result.json'
    if not path.is_file():
        raise ValueError('找不到覆盖率分析 ' + identifier)
    return path


def json_lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def read_build(summary):
    name = summary.get('build')
    build = Path(name) if name else None
    if build is None:
        return None, {}
    if not (build / 'build.json').is_file():
        build = eda.OUT / 'builds' / build.name
    return build, eda.read_json(build / 'build.json') if (build / 'build.json').is_file() else {}


def archived_sources(database_items, summary):
    build, receipt = read_build(summary)
    hashes = receipt.get('sources', summary.get('execution_sources', {}))
    original_root = Path(receipt.get('root') or eda.ROOT)
    resolved = {}
    for name in sorted({row['file'] for row in json_lines(database_items) if row.get('file')}):
        recorded = Path(name)
        relative = None
        for prefix in (original_root, eda.ROOT):
            try:
                relative = recorded.resolve().relative_to(prefix.resolve()).as_posix()
                break
            except ValueError:
                pass
        if relative not in hashes:
            # VCS paths in a space-containing checkout can use its ASCII alias.
            match = re.match(r'^/tmp/cmdbuf-studio-\d+/source-[0-9a-f]+/(.+)$', name)
            relative = match[1] if match else relative
        if relative not in hashes:
            relative = max((p for p in hashes if name.endswith('/'+p)), key=len, default=None)
        if relative not in hashes:
            continue
        candidates = [eda.ROOT / relative]
        if build:
            candidates.insert(0, build / 'rtl_sources' / relative)
        for path in candidates:
            if path.is_file() and eda.digest(path) == hashes[relative]:
                resolved[name] = dict(path=str(path), sha256=hashes[relative], relative=relative)
                break
    return resolved


def design_identity(summary):
    _, receipt = read_build(summary)
    dut = receipt.get('dut', summary.get('dut', {}))
    sources = receipt.get('sources', summary.get('execution_sources', {}))
    filenames = set(dut.get('files', []))
    include_dirs = tuple(path.rstrip('/')+'/' for path in dut.get('include_dirs', []))
    shape = {name: digest for name, digest in sources.items()
             if name in filenames or (name.endswith(('.v', '.sv', '.vh', '.svh')) and name.startswith(include_dirs))}
    hierarchy = sources.get('simulation/vcs/coverage.hier')
    if not filenames or not filenames.issubset(shape) or not hierarchy:
        raise ValueError('该历史批次缺少可比较的 DUT 构建记录，请单独分析')
    return dut, shape, hierarchy


def case_inputs(batch, summary, require_vdb):
    results = {row['id']: row for row in summary['results']
               if row['status'] == 'PASS' and not row.get('injection')}
    included = summary['coverage'].get('included', sorted(results))
    if not included or len(included) != len(set(included)) or not set(included).issubset(results):
        raise ValueError('历史批次的覆盖率用例清单无效: ' + batch.name)
    for identifier in included:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise ValueError('无效的历史用例编号: ' + identifier)
        row = results[identifier]
        database = batch / 'cases' / identifier / 'simv.vdb'
        if require_vdb and not database.is_dir():
            raise ValueError('缺少逐用例 VDB: {} / {}。已有合并库仍可查看明细。'.format(batch.name, identifier))
        yield dict(key=batch.name+'/'+identifier, batch=batch.name, id=identifier, case=row['case'],
                   seed=row['seed'], seconds=row.get('seconds'), database=str(database),
                   **({'formal_origin': row['formal_origin']} if 'formal_origin' in row else {}))


def functional_coverage(batches, summaries, cases):
    """Match functional definitions and assemble one bitmap per admitted VDB."""
    reference = summaries[0].get('functional', {}).get('manifest')
    if reference is None or any(s.get('functional', {}).get('manifest') != reference for s in summaries):
        raise ValueError('联合选集需要相同版本的功能属性。旧批次或属性已修改的用例请重新回归。')
    contract_paths = [eda.PROJECT['checker']] + eda.PROJECT['checker_includes']

    def contract(summary):
        sources = summary.get('execution_sources', {})
        return [summary.get('contract_revision')] + [sources.get(name) for name in contract_paths]

    contracts = [contract(summary) for summary in summaries]
    if not all(contracts[0]) or any(value != contracts[0] for value in contracts[1:]):
        raise ValueError('联合选集需要同一版本的端口约束和检查器')
    names = cover_names(reference)
    bits = {name: 1 << index for index, name in enumerate(names)}
    by_key = {batch.name+'/'+row['id']: row for batch, summary in zip(batches, summaries)
              for row in summary['results'] if row['status'] == 'PASS' and not row.get('injection')}
    counts = dict.fromkeys(names, 0)
    masks = []
    for case in cases:
        result = by_key[case['key']]
        row = result.get('functional', {})
        receipt = Path(case['database']).with_name('functional_hits.json')
        hits = row.get('hits')
        if (row.get('sha256') != reference['sha256'] or row.get('version') != reference['version'] or
                row.get('adapter_sha256') != reference['adapter_sha256'] or
                not row.get('complete') or not isinstance(hits, list) or
                len(hits) != len(set(hits)) or any(name not in bits for name in hits) or
                not receipt.is_file() or eda.digest(receipt) != result.get('functional_receipt_sha256') or
                eda.read_json(receipt) != row):
            raise ValueError('功能覆盖采集版本不符或缺少完整记录 ' + case['key'])
        masks.append(sum(bits[name] for name in hits))
        for name in hits:
            counts[name] += 1
    return reference, counts, masks


def reader(env):
    import fcntl
    inc, lib = eda.platform_config.npi_directories(env)
    header = inc / 'npi_cov.h'
    if not header.is_file():
        raise RuntimeError('当前 Verdi 安装缺少 NPI Coverage 头文件 npi_cov.h')
    source = HERE / 'npi_reader.cpp'
    shared = lib / 'libNPI.so'
    identity = ':'.join((eda.digest(source), eda.digest(header), str(shared), str(shared.stat().st_mtime_ns),
                         env.get('CXX', 'g++')))
    directory = eda.OUT / 'npi_readers' / ('coverage-' + hashlib.sha256(identity.encode()).hexdigest()[:16])
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / 'coverage_reader'
    with (directory / 'compile.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not executable.is_file():
            temporary = directory / 'coverage_reader.tmp'
            cmd = [env.get('CXX', 'g++'), '-std=c++11', '-O2', '-I'+str(inc), str(source),
                   '-L'+str(lib), '-Wl,-rpath,'+str(lib), '-lNPI', '-ldl', '-lpthread', '-lrt', '-o', str(temporary)]
            checked_run(cmd, directory, 'npi_compile.log', env, 180)
            temporary.replace(executable)
    env['LD_LIBRARY_PATH'] = str(lib) + ':' + env.get('LD_LIBRARY_PATH', '')
    return executable


def checked_run(command, directory, log_name, env, timeout=None):
    log = directory / log_name
    rc, _ = eda.execute(command, directory, log, env, timeout, phase='coverage_npi')
    if rc:
        detail = log.read_text(encoding='utf-8', errors='replace')[-6000:]
        raise RuntimeError('Verdi NPI 覆盖率处理失败 ({})\n{}'.format(rc, detail))


def extract(directory, executable, env, requests, details=True):
    directory.mkdir(parents=True, exist_ok=True)
    fields = [(key, test, str(database)) for key, test, database in requests]
    if any(any(ch in value for ch in ('\n', '\r', '\t', '\0')) for field in fields for value in field):
        raise ValueError('VDB 路径和用例名不能含制表符、换行或空字节')
    items = directory / 'items.jsonl' if details else Path(os.devnull)
    tests = directory / 'hits.jsonl'
    candidates = fields[1:]
    hits, eligible = [], None
    chunk_tests = directory / 'chunk_hits.jsonl'
    request = directory / 'chunk_vdbs.tsv'
    # Older Verdi NPI can crash when opening multiple long VDB paths in one
    # process. Use private short aliases without copying or modifying the VDBs.
    # Bound each NPI process and verify every chunk against the same reference.
    with tempfile.TemporaryDirectory(prefix='cmdbuf-cov-', dir='/tmp') as folder, \
            tests.open('w', encoding='utf-8') as output:
        aliases = Path(folder)
        for offset in range(0, max(1, len(candidates)), VDBS_PER_READER):
            chunk = fields[:1] + candidates[offset:offset+VDBS_PER_READER]
            link_rows = []
            for index, (key, test, database) in enumerate(chunk):
                alias = aliases / ('{}.vdb'.format(index))
                if alias.is_symlink():
                    alias.unlink()
                alias.symlink_to(Path(database).absolute(), target_is_directory=True)
                link_rows.append((key, test, str(alias)))
            request.write_text(''.join('\t'.join(row)+'\n' for row in link_rows), encoding='utf-8')
            destination = items if offset == 0 else Path(os.devnull)
            log = 'npi_read_{:04d}.log'.format(offset//VDBS_PER_READER+1)
            checked_run([str(executable), str(request), str(destination), str(chunk_tests)], directory, log, env)
            rows = list(json_lines(chunk_tests))
            if [row['key'] for row in rows] != [row[0] for row in chunk]:
                raise RuntimeError('VDB 用例顺序不一致')
            for index, row in enumerate(rows):
                mask, total = int(row['covered'], 16), int(row['eligible'], 16)
                if eligible is None:
                    eligible = total
                if total != eligible:
                    raise ValueError('覆盖率分母或排除项不同，不能合并比较: ' + row['key'])
                if offset and index == 0:
                    if mask != hits[0]:
                        raise RuntimeError('扫描期间基准 VDB 发生变化')
                    continue
                hits.append(mask)
                output.write(json.dumps(row, separators=(',', ':'))+'\n')
            if len(candidates) > VDBS_PER_READER:
                print('COVERAGE_PROGRESS {}/{}'.format(len(hits)-1, len(candidates)), flush=True)
    chunk_tests.unlink()
    request.unlink()
    if len(hits) != len(fields) or not eligible or any(mask & ~eligible for mask in hits):
        raise RuntimeError('覆盖率项读取不完整，或该库没有可测量的代码覆盖率项')
    return items, tests, hits, eligible


def stage(message):
    print('COVERAGE_STAGE ' + message, flush=True)


def export_selected(directory, selected):
    path = directory / 'selected_tests.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('batch', 'run_id', 'case', 'seed', 'seconds', 'replay', 'formal_campaign'))
        for row in selected:
            writer.writerow((row['batch'], row['id'], row['case'], row['seed'], row['seconds'],
                             'make replay REGRESSION={} RUN_ID={}'.format(row['batch'], row['id']),
                             row.get('formal_origin', {}).get('campaign', '')))
    return path


def analyze(args, env):
    names = args.regressions.split(',') if args.regressions else [args.regression]
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', name.strip()) for name in names):
        raise ValueError('历史批次编号格式无效')
    batches = [eda.select_regression(name.strip()) for name in names]
    if len({p.name for p in batches}) != len(batches):
        raise ValueError('历史批次不能重复选择')
    summaries = [eda.read_json(batch / 'summary.json') for batch in batches]
    if any(s.get('coverage', {}).get('status') != 'PASS' for s in summaries):
        raise ValueError('请选择已成功合并覆盖率的历史批次')
    reference = summaries[0]['coverage']
    metrics = reference.get('metrics_requested', eda.PROJECT['coverage'])
    if len(batches) > 1:
        identity = design_identity(summaries[0])
        if any(design_identity(s) != identity or s['coverage'].get('metrics_requested', eda.PROJECT['coverage']) != metrics
               for s in summaries[1:]):
            raise ValueError('所选批次的 DUT 源码、例化或覆盖率配置不同，请分别分析')
    cases = [case for batch, summary in zip(batches, summaries)
             for case in case_inputs(batch, summary, args.action == 'minimize' or len(batches) > 1)]
    functional = functional_coverage(batches, summaries, cases) if getattr(args, 'joint', False) else None
    identifier = eda.stamp() + '_' + eda.uuid.uuid4().hex[:8]
    directory = eda.OUT / 'coverage_analysis' / identifier
    directory.mkdir(parents=True)
    if len(batches) > 1:
        stage('合并所选历史批次的基准覆盖率')
        reference = eda.merge_coverage_databases(directory / 'baseline', [r['database'] for r in cases], env, metrics)
        if reference['status'] != 'PASS':
            raise RuntimeError('历史批次基准 VDB 合并失败: ' + str(directory / 'baseline' / 'urg.log'))
    else:
        archive = batches[0] / 'coverage' / 'merged.vdb'
        if archive.is_dir():
            reference = dict(reference, database=str(archive),
                             report=str(archive.parent / 'urgReport' / 'dashboard.html'))
    baseline = Path(reference['database'])
    if not baseline.is_dir():
        raise ValueError('历史回归的合并 VDB 不存在: ' + str(baseline))
    executable = reader(env)
    stage('读取 Verdi NPI 覆盖率' + (' · {} 个历史用例'.format(len(cases)) if args.action == 'minimize' else ''))
    requests = [('baseline', '', baseline)]
    if args.action == 'minimize':
        requests.extend((r['key'], r['id'], r['database']) for r in cases)
    items, hits_path, hits, eligible = extract(directory / 'npi', executable, env, requests)
    record = dict(version=1, id=identifier, status='PASS', batches=[p.name for p in batches],
                  baseline=reference, items=str(items), hits=str(hits_path),
                  sources=archived_sources(items, summaries[0]), coverable=popcount(eligible),
                  covered=popcount(hits[0]), case_count=len(cases),
                  candidates=[dict(key=row['key'], batch=row['batch'], id=row['id'],
                                   case=row['case'], seed=row['seed']) for row in cases])
    if args.action == 'minimize':
        if union(hits[1:]) != hits[0]:
            raise ValueError('逐用例 VDB 的命中并集与历史合并库不同。请检查用例 VDB 是否被替换或使用了不同合并规则。')
        search = hits[1:]
        if functional is not None:
            _, _, func_masks = functional
            # VDB IDs need not be consecutive. Allocate above every eligible ID.
            shift = eligible.bit_length()
            search = [code | (coverage << shift) for code, coverage in zip(search, func_masks)]
        stage('证明代码与功能覆盖的联合最小用例集' if functional is not None else '证明最小用例集')
        solver = minimum_cover(search, args.seconds,
                               lambda nodes, low, high: stage('精确搜索 · 已检查 {} 个节点 · {} ≤ 最优值 ≤ {}'.format(nodes, low, high)))
        selected = [cases[index] for index in solver['selected']]
        stage('用 URG 重新合并并核对逐项覆盖率')
        merged = eda.merge_coverage_databases(directory / 'selected',
                                              [row['database'] for row in selected] or [baseline],
                                              env, metrics, reset=not selected)
        if merged['status'] != 'PASS':
            raise RuntimeError('所选用例集 VDB 合并失败: ' + str(directory / 'selected' / 'urg.log'))
        _, verification, scores, counts = extract(
            directory / 'verification', executable, env,
            [('baseline', '', baseline), ('selected', '', merged['database'])], details=False)
        if scores != [hits[0], hits[0]] or counts != eligible or merged['metrics_percent'] != reference['metrics_percent']:
            raise RuntimeError('重新合并的 VDB 与原覆盖项或 URG 分数不一致，验证失败')
        if functional is not None:
            manifest, func_counts, func_masks = functional
            selected_mask = union(func_masks[index] for index in solver['selected'])
            if selected_mask != union(func_masks):
                raise RuntimeError('选集遗漏原回归已命中的功能覆盖点')
            names = list(func_counts)
            selected_counts = dict.fromkeys(func_counts, 0)
            for index in solver['selected']:
                for bit in bits(func_masks[index]):
                    selected_counts[names[bit.bit_length()-1]] += 1
            points = [dict(name=name, baseline_hits=count, selected_hits=selected_counts[name])
                      for name, count in func_counts.items()]
            record['functional'] = dict(manifest=manifest, points=points, total=len(points),
                                        covered=popcount(selected_mask), verified=True)
        record.update(verified=True, solver=solver, selected=selected, selected_coverage=merged,
                      selected_csv=str(export_selected(directory, selected)), verification=str(verification))
    path = directory / 'result.json'
    eda.write_json(path, record)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('details', 'minimize'), required=True)
    parser.add_argument('--regression', default='latest')
    parser.add_argument('--regressions')
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--joint', action='store_true')
    parser.add_argument('--existing')
    args = parser.parse_args(argv)
    if args.seconds < 0:
        parser.error('求解时间必须为非负整数，0 表示不限时')
    if args.joint and args.action != 'minimize':
        parser.error('--joint 适用于最小用例集')
    execution.install_signal_handlers()
    try:
        env = None
        if args.existing:
            path = result_file(args.existing)
        else:
            env = eda.platform_config.tool_env(required=('verdi', 'urg'))
            path = analyze(args, env)
        print('COVERAGE_RESULT ' + str(path), flush=True)
        if not os.environ.get('EDA_NO_OPEN') and (env or os.environ).get('DISPLAY'):
            with (path.parent / 'coverage_tk.log').open('w', encoding='utf-8') as log:
                subprocess.Popen([sys.executable, str(eda.HERE / 'gui' / 'desktop.py'),
                                  '--coverage-record', str(path)], env=env,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        return 0
    except KeyboardInterrupt:
        print('覆盖率任务已取消', file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print('COVERAGE: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
