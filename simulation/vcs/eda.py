#!/usr/bin/env python3
"""VCS/Verdi runner. Python >= 3.6, stdlib only; commands execute on the local Linux host.

The DUT-specific stimulus adapter is case_adapter.py and tb/. Project sources
and tool selection live in project.json/toolchain.sh, not in this runner.
"""
from __future__ import print_function
import argparse
import concurrent.futures
import csv
import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from case_adapter import load_case, make_program
import sv_cases
import platform_config


def load_input(path):
    return sv_cases.load(path) if Path(path).suffix == '.sv' else load_case(path)


def input_program(cfg, seed, cycles=None):
    return sv_cases.program(cfg, seed, cycles) if cfg.get('format') == 'sv_main_phase' else make_program(cfg, seed, cycles)

PROJECT = json.loads((HERE / 'project.json').read_text(encoding='utf-8'))
OUT = platform_config.result_root()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(path))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp():
    # Stable report timezone, independent of the workstation timezone.
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')


def sources():
    import dut_config
    files = [ROOT / PROJECT['rtl_filelist']]
    for line in files[0].read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            files.append(ROOT / line.strip())
    files.extend(ROOT / p for p in [PROJECT['checker']] + PROJECT['checker_includes'])
    files.extend(dut_config.source_files())
    files.extend(ROOT / p for p in ('Makefile', 'make.cmd'))
    files.append(ROOT / 'docs/timing/protocol_corrections_20261021.json')
    files.extend(p for p in HERE.rglob('*') if p.is_file() and
                 '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.log'))
    return sorted(set(files))


def manifest():
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sources()}


def build_manifest():
    # GUI assets and per-run plans are transported/archived, but do not change
    # the compiled HDL. Changing seed counts must reuse an identical simulator.
    return {p: h for p, h in manifest().items()
            if not p.startswith(('simulation/vcs/gui/', 'simulation/vcs/plans/'))
            and p != 'simulation/vcs/regression.json'}


def tool_env():
    return platform_config.tool_env()


def execute(cmd, cwd, log, env, timeout):
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    write_json(log.with_suffix('.command.json'), {'argv': cmd, 'cwd': str(cwd)})
    start = time.time()
    with log.open('w', encoding='utf-8') as out:
        process = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=out,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            out.write('\nEDA_RUNNER_TIMEOUT\n')
            rc = 124
    return rc, round(time.time() - start, 3)


def instrument_checker(destination):
    """Same property expressions, fatal action blocks, assume -> runtime assert.

    Canonical files are never rewritten. Record every converted property and
    require exact conversion counts so unsupported syntax cannot slip through.
    """
    destination.mkdir(parents=True, exist_ok=True)
    changes = []
    pattern = re.compile(r'(\b(\w+)\s*:\s*)(assert|assume)(\s+property\s*\(.*?\))\s*;', re.S)
    for rel in [PROJECT['checker']] + PROJECT['checker_includes']:
        path = ROOT / rel
        code = path.read_text(encoding='utf-8')
        expected = len(re.findall(r'\b(?:assert|assume)\s+property\s*\(', re.sub(r'//[^\n]*', '', code)))
        labels = []
        def replace(match):
            label = match.group(2)
            labels.append({'label': label, 'original_kind': match.group(3)})
            return match.group(1) + 'assert' + match.group(4) + ' else $fatal(1, "SVA_FAIL ' + label + '");'
        rewritten = pattern.sub(replace, code)
        if len(labels) != expected:
            raise RuntimeError('Unsupported property syntax in ' + rel)
        (destination / path.name).write_text(rewritten, encoding='utf-8')
        changes.append({'source': rel, 'sha256': digest(path), 'properties': labels})
    write_json(destination / 'instrumentation.json', changes)


def build(env):
    from contracts import audit
    audit()
    source_hashes = build_manifest()
    import dut_config
    identity = {'sources': source_hashes, 'root': str(ROOT), 'dut': dut_config.load(),
                'vcs_home': env.get('VCS_HOME'), 'verdi_home': env.get('VERDI_HOME'),
                'coverage': PROJECT['coverage']}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    target = OUT / 'builds' / key
    target.mkdir(parents=True, exist_ok=True)
    # Independent CLI invocations may request the same source snapshot.
    import fcntl
    with (target / '.build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return build_at(target, identity, env)


def build_at(target, identity, env):
    key = target.name
    receipt = target / 'build.json'
    if receipt.exists() and (target / 'simv').exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    generated = target / 'checker'
    instrument_checker(generated)
    sv_cases.generate_factory(generated)
    import dut_config
    dut = identity['dut']
    (generated / 'dut_instance.svh').write_text(dut['instance'], encoding='utf-8')
    pli = platform_config.pli_directory(env)
    rtl = [str(dut_config.project_path(f)) for f in dut['files']]
    filelist = target / 'files.f'
    filelist.write_text('\n'.join('"' + platform_config.tool_path(p) + '"' for p in rtl + [str(generated / Path(PROJECT['checker']).name),
                           str(ROOT / PROJECT['testbench'])]) + '\n', encoding='utf-8')
    cmd = ['vcs', '-full64', '-sverilog', '-timescale=1ns/1ps', '-debug_access+all', '-kdb',
           '+v2k', '-top', PROJECT['top'], '+incdir+' + str(generated), '+incdir+' + str(HERE / 'tb'),
           '-P', str(pli / 'novas.tab'), str(pli / 'pli.a'),
           '-cm', PROJECT['coverage'], '-cm_tgl', 'mda', '-cm_hier', str(HERE / 'coverage.hier'),
           '-cm_dir', str(target / 'simv.vdb'), '-f', str(filelist), '-o', str(target / 'simv')]
    cmd += ['+incdir+' + str(dut_config.project_path(d, True)) for d in dut['include_dirs']]
    cmd += ['+define+' + d for d in dut['defines']]
    cmd = [platform_config.tool_path(arg) for arg in cmd]
    rc, elapsed = execute(cmd, target, target / 'compile.log', env, 600)
    if rc or not (target / 'simv').is_file():
        raise RuntimeError('VCS compile failed ({}): {}'.format(rc, target / 'compile.log'))
    write_json(receipt, dict(identity, build_id=key, argv=cmd, seconds=elapsed,
                             simv_sha256=digest(target / 'simv'),
                             simulator=str(target / 'simv'), built_at=stamp()))
    print('BUILD ' + str(target), flush=True)
    return target


def case_path(name):
    path = Path(name)
    if path.is_file():
        return path.resolve()
    path = HERE / 'tc' / (name if name.endswith('.sv') else name + '.sv')
    if path.is_file():
        return path
    path = HERE / 'cases' / (name if name.endswith('.json') else name + '.json')
    if not path.is_file():
        raise ValueError('Unknown case: ' + name)
    return path


def run_case(build_dir, regression, name, seed, cycles, env, timeout, injection='', replay_program=None,
             saved_config=None, waves=True):
    path = Path(saved_config) if saved_config else case_path(name)
    cfg = read_json(path) if saved_config else load_input(path)
    case_name = name if saved_config else path.stem
    identifier = '{}_seed_{}{}'.format(case_name, seed, '_' + injection if injection else '')
    run = regression / 'cases' / identifier
    run.mkdir(parents=True)  # Never overwrite an earlier seed's evidence.
    program = (Path(replay_program).read_text(encoding='utf-8') if replay_program is not None
               else input_program(cfg, seed, cycles))
    (run / 'stimulus.txt').write_text(program, encoding='utf-8')
    write_json(run / 'case.json', cfg)
    sv_case = cfg.get('format') in ('sv_tc','sv_main_phase')
    if sv_case:
        archived = build_dir / 'checker' / 'tc_sources' / (cfg['name']+'.sv')
        if not archived.is_file() or digest(archived) != cfg['source_sha256']:
            raise RuntimeError('TC source differs from compiled archive: '+cfg['name'])
        (run / 'case.sv').write_text(cfg['source'], encoding='utf-8')
    wave_case = 'signal' in cfg
    from random_audit import has_random
    strict_random = not wave_case and has_random(program)
    if strict_random or sv_case:
        from contracts import audit
        write_json(run / 'contracts.json', audit())
    if wave_case:
        from wavedrom_adapter import compile_wave, sample_csv, write_preview
        plan = compile_wave(cfg)
        (run / ('planned_samples.csv' if plan.get('mode') == 'protocol' else 'expected_samples.csv')).write_text(sample_csv(plan), encoding='utf-8')
        write_preview(cfg, run / 'input_wavedrom.html')
        if plan.get('mode') == 'protocol':
            from contracts import audit
            write_json(run / 'contracts.json', audit())
    # VCS 2018 writes only testdata when runtime -cm_dir differs from compile
    # -cm_dir. Seed each isolated database with its matching static design.
    shutil.copytree(str(build_dir / 'simv.vdb'), str(run / 'simv.vdb'))
    cmd = [str(build_dir / 'simv'), '+ntb_random_seed=' + str(seed), '+SEED=' + str(seed),
           '+PROGRAM=' + str(run / 'stimulus.txt'),
           '-cm', PROJECT['coverage'], '-cm_dir', str(run / 'simv.vdb'), '-cm_name', identifier]
    if waves:
        cmd += ['+FSDB=' + str(run / 'waves.fsdb')]
    if injection:
        cmd.append('+INJECT=' + injection)
    if strict_random:
        cmd.append('+STRICT_RANDOM')
    if sv_case:
        cmd.append('+SV_TC')
    if wave_case:
        cmd.append('+WAVEDROM')
        if program.startswith('PROTOCOL_V1 '): cmd.append('+PROTOCOL')
    rc, elapsed = execute(cmd, run, run / 'sim.log', env, timeout)
    conversion = None
    if waves and (run / 'waves.fsdb').is_file():
        # Verdi's converter preserves unpacked arrays, which VCS $dumpvars
        # omits. Both viewers therefore consume the same captured FSDB data.
        conversion_rc, conversion_time = execute(
            ['fsdb2vcd', str(run / 'waves.fsdb'), '-o', str(run / 'waves.vcd'), '-sv', '-keep_last_time'],
            run, run / 'wave_conversion.log', env, max(120, timeout))
        conversion = dict(exit_code=conversion_rc, seconds=conversion_time, source='waves.fsdb', output='waves.vcd')
    log = (run / 'sim.log').read_text(encoding='utf-8', errors='replace')
    metrics = read_json(run / 'metrics.json') if (run / 'metrics.json').exists() else {}
    failures = []
    if rc:
        failures.append('simulation exit {}'.format(rc))
    if waves and (not conversion or conversion['exit_code'] or not (run / 'waves.vcd').is_file()):
        failures.append('FSDB to VCD conversion failed; see wave_conversion.log')
    if not re.search(r'^TEST_PASS\s', log, re.M):
        failures.append('missing TEST_PASS')
    if re.search(r'^(?:Error(?:-|:)|Fatal(?:-|:))|SVA_FAIL|ENV_[A-Z]+.*(?:cycle=|Error)|EDA_RUNNER_TIMEOUT', log, re.M):
        failures.append('simulator/assertion/environment error')
    for event, minimum in cfg.get('require', {}).items():
        if metrics.get(event, 0) < minimum:
            failures.append('require {} >= {}, got {}'.format(event, minimum, metrics.get(event, 0)))
    # Archived binaries before the GUI only support FSDB. VCD is an additional
    # artifact for the current viewer; do not invalidate historical replays.
    for artifact in (('waves.fsdb', 'simv.vdb') if waves else ('simv.vdb',)):
        if not (run / artifact).exists():
            failures.append('missing ' + artifact)
    drive_audit = None
    if sv_case:
        if cfg.get('format') == 'sv_main_phase':
            from main_phase_audit import verify_execution as verify_sv
        else:
            from sv_audit import verify_execution as verify_sv
        drive_audit = verify_sv(run, program)
        write_json(run / 'drive_audit.json', drive_audit)
        failures.extend(drive_audit['failures'])
        diagnostic=re.search(r'^(?:SV_TC_CONTEXT|MAIN_PHASE_CONTEXT) (.+)$',log,re.M)
        if diagnostic:
            write_json(run/'constraint_failure.json',dict(case=case_name,seed=seed,
                context=diagnostic.group(1),source='case.sv',log='sim.log',
                policy='TC if branches choose inputs; driver validates and copies without repair'))
    if strict_random:
        from random_audit import verify_execution as verify_random
        drive_audit = verify_random(run, program)
        write_json(run / 'drive_audit.json', drive_audit)
        failures.extend(drive_audit['failures'])
    if wave_case:
        from wavedrom_adapter import verify_execution
        drive_audit = verify_execution(cfg, run, program)
        write_json(run / 'drive_audit.json', drive_audit)
        failures.extend(drive_audit['failures'])
        if program.startswith('PROTOCOL_V1 '):
            from protocol_views import write_comparison
            write_comparison(cfg, run, program)
    status = 'PASS' if not failures else ('TIMEOUT' if rc == 124 else 'FAIL')
    result = {'id': identifier, 'case': case_name, 'seed': seed, 'status': status,
              'seconds': elapsed, 'exit_code': rc, 'failures': failures, 'metrics': metrics,
              'directory': str(run), 'build': str(build_dir), 'argv': cmd,
              'stimulus_sha256': digest(run / 'stimulus.txt'), 'case_sha256': digest(run / 'case.json'),
              'events_sha256': digest(run / 'events.csv') if (run / 'events.csv').exists() else None,
              'injection': injection, 'waves_enabled': waves,
              'wave_conversion': conversion,
              'wave_artifacts': [n for n in ('waves.fsdb', 'waves.vcd') if (run / n).is_file()]}
    result['format'] = cfg['format'] if sv_case else 'protocol' if program.startswith('PROTOCOL_V1 ') else ('wavedrom' if wave_case else 'constrained_random' if strict_random else 'sequence')
    result['drive_audit'] = drive_audit
    write_json(run / 'result.json', result)
    print('{} {} ({:.2f}s)'.format(status, identifier, elapsed), flush=True)
    return result


def merge_coverage(regression, results, env):
    # Preserve every failing case's VDB separately. Only valid passing simulations
    # contribute to the accepted regression coverage database.
    accepted = [r for r in results if r['status'] == 'PASS' and not r['injection']]
    if not accepted:
        return {'status': 'NO_PASSING_CASES', 'included': [], 'excluded': [r['id'] for r in results]}
    cov = regression / 'coverage'
    cov.mkdir()
    cmd = ['urg', '-full64', '-dir'] + [str(Path(r['directory']) / 'simv.vdb') for r in accepted]
    cmd += ['-dbname', str(cov / 'merged.vdb'), '-report', str(cov / 'urgReport'),
            '-metric', PROJECT['coverage'], '-format', 'both']
    rc, seconds = execute(cmd, regression, cov / 'urg.log', env, 300)
    report = cov / 'urgReport' / 'dashboard.html'
    database = cov / 'merged.vdb'
    text_report = cov / 'urgReport' / 'dashboard.txt'
    percentages = {}
    if text_report.exists():
        lines = text_report.read_text(errors='replace').splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith('SCORE') and 'LINE' in line:
                percentages = dict(zip(line.split(), lines[i+1].split()))
                break
    code_present = all(key in percentages for key in ('LINE', 'COND', 'BRANCH', 'TOGGLE'))
    return {'status': 'PASS' if rc == 0 and report.exists() and database.exists() and code_present else 'FAIL',
            'exit_code': rc, 'seconds': seconds, 'database': str(database), 'report': str(report),
            'metrics_percent': percentages, 'code_metrics_present': code_present,
            'included': [r['id'] for r in accepted],
            'excluded': [r['id'] for r in results if r not in accepted]}


def write_report(regression, summary):
    write_json(regression / 'summary.json', summary)
    rows = []
    for r in summary['results']:
        e = html.escape
        shown_status='PASS (expected violation)' if r.get('expected_failure') and r.get('acceptance_pass') else r['status']
        extra=''
        for name,label in [('case.sv','SV TC 源码'),('assigned.csv','TC 赋值'),('sv_solutions.csv','VCS 求解值'),('samples.csv','实际输入'),('actual_vs_plan.html','计划与实际'),('drive_audit.json','输入审计'),('constraint_failure.json','约束冲突')]:
            if (regression/'cases'/r['id']/name).exists():
                extra+=' <a href="cases/{}/{}">{}</a>'.format(e(r['id']),name,label)
        rows.append(('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td><a href="cases/{}/sim.log">log</a> '
                    '<a href="cases/{}/events.csv">events</a>'+extra+'</td><td><code>make wave '
                    'REGRESSION={} RUN_ID={}</code></td></tr>').format(
                        e(r['id']), e(shown_status), r['metrics'].get('accepted', 0),
                        e('; '.join(r['failures'])), e(r['id']), e(r['id']), e(summary['id']), e(r['id'])))
    body = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CMDBUF RD VCS 回归</title>
<style>body{font:16px system-ui;margin:36px;color:#182c40}table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #bbc7d0;padding:10px;text-align:left}code{font-size:13px}h1{font-size:26px}</style>
<h1>CMDBUF RD VCS 回归</h1><p>批次 %s　状态 %s　通过 %d / %d</p>
<p><a href="coverage/urgReport/dashboard.html">URG 代码覆盖率</a> · <a href="summary.json">机器可读结果</a></p>
<p>在 Linux 工程目录运行下表 Make 命令。
FSDB 与 VDB 保存在当前设备。覆盖率使用 <code>make coverage REGRESSION=%s</code>。
覆盖率仅合并通过的用例。失败用例的原始数据库和波形单独保留。</p>
<table><tr><th>用例与种子</th><th>状态</th><th>接收数</th><th>原因</th><th>证据</th><th>Verdi 命令</th></tr>%s</table>
</html>''' % (html.escape(summary['id']), summary['status'], sum(r.get('acceptance_pass',r['status']=='PASS') for r in summary['results']), len(summary['results']),
             html.escape(summary['id']), ''.join(rows))
    (regression / 'index.html').write_text(body, encoding='utf-8')


def select_regression(name):
    if name == 'latest':
        name = read_json(OUT / 'latest.json')['id']
    if Path(name).name != name:
        raise ValueError('Use a regression ID, not a filesystem path')
    path = OUT / 'regressions' / name
    if not (path / 'summary.json').is_file():
        raise ValueError('Regression not found: ' + str(path))
    return path


def regress(args, env):
    if args.command == 'run':
        jobs = [(args.case, args.seed, args.cycles, '')]
    elif args.command == 'selftest':
        jobs = [('smoke', 1, None, mode) for mode in ('rid', 'src', 'busy')]
    elif getattr(args, 'plan', None):
        from regression_plan import expand
        plan = read_json(ROOT / args.plan)
        jobs = expand(plan)
    else:
        suite = read_json(HERE / 'regression.json')
        seeds = [int(x) for x in args.seeds.split(',')] if args.seeds else suite['seeds']
        if len(seeds) != len(set(seeds)):
            raise ValueError('Duplicate seeds are not allowed')
        directed = args.cases.split(',') if args.cases else suite['directed']
        jobs = [] if args.random_only else [(name, 1, None, '') for name in directed]
        jobs += [(args.random_case or suite['random_case'], seed, args.cycles if args.cycles is not None else suite['cycles'], '') for seed in seeds]
    for name, seed, cycles, _ in jobs:
        if not 1 <= seed <= 2147483647:
            raise ValueError('Seed must be 1..2147483647')
        input_program(load_input(case_path(name)), seed, cycles)  # Validate before compilation.
    if len({(n, s, i) for n, s, c, i in jobs}) != len(jobs):
        raise ValueError('Duplicate case/seed combinations')
    build_dir = build(env)
    rid = stamp() + '_' + args.command + '_' + uuid.uuid4().hex[:6]
    regression = OUT / 'regressions' / rid
    regression.mkdir(parents=True)
    waves = getattr(args, 'waves', 1) == 1
    if getattr(args, 'plan', None):
        write_json(regression / 'plan.json', plan)
    write_json(regression / 'dut.json', read_json(build_dir / 'build.json').get('dut', {}))
    print('BATCH {} TOTAL {}'.format(rid, len(jobs)), flush=True)
    write_json(regression / 'progress.json', {'id': rid, 'total': len(jobs), 'completed': 0, 'state': 'RUNNING'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, build_dir, regression, name, seed, cycles, env,
                               args.timeout, injection, waves=waves) for name, seed, cycles, injection in jobs]
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            future.result()
            completed += 1
            write_json(regression / 'progress.json', {'id': rid, 'total': len(jobs), 'completed': completed, 'state': 'RUNNING'})
            print('PROGRESS {} {}/{}'.format(rid, completed, len(jobs)), flush=True)
        results = [f.result() for f in futures]
    if args.command == 'selftest':
        # A compile/license/environment crash is not counted as fault detection.
        markers = {'rid': 'SVA_FAIL A_COMPARE_RID', 'src': 'ENV_SRC', 'busy': 'ENV_SUCCESS_BUSY'}
        for r in results:
            log = (Path(r['directory']) / 'sim.log').read_text(errors='replace')
            r['detected_as_expected'] = (r['status'] == 'FAIL' and markers[r['injection']] in log)
        ok = all(r['detected_as_expected'] for r in results)
        cov = {'status': 'NOT_MERGED_NEGATIVE_SELFTEST'}
    else:
        cov = merge_coverage(regression, results, env)
        ok = all(r['status'] == 'PASS' for r in results) and cov['status'] == 'PASS'
    summary = {'id': rid, 'status': 'PASS' if ok else 'FAIL', 'created_at': stamp(),
               'timezone': 'Asia/Shanghai', 'project': PROJECT['name'], 'build': str(build_dir),
               'passed': sum(r['status'] == 'PASS' for r in results), 'results': results, 'coverage': cov,
               'waves_enabled': waves, 'dut': read_json(build_dir / 'build.json').get('dut', {}),
               'execution_sources': manifest()}
    write_report(regression, summary)
    write_json(regression / 'progress.json', {'id': rid, 'total': len(jobs), 'completed': len(jobs), 'state': summary['status']})
    # Negative tests must not replace the user's latest valid regression.
    if args.command != 'selftest':
        write_json(OUT / 'latest.json', {'id': rid})
    write_json(OUT / 'last_command.json', {'id': rid})
    if os.environ.get('EDA_REQUEST_ID'):
        write_json(OUT / 'requests' / (os.environ['EDA_REQUEST_ID'] + '.json'), {'id': rid})
    print('REPORT ' + str(regression / 'index.html'), flush=True)
    return 0 if ok else 1


def replay(args, env):
    original_regression = select_regression(args.regression)
    original = read_json(original_regression / 'summary.json')
    chosen = next((r for r in original['results'] if r['id'] == args.case), None)
    if chosen is None:
        raise ValueError('Case not found in saved regression: ' + args.case)
    old = Path(chosen['directory'])
    build_dir = Path(chosen['build'])
    receipt = read_json(build_dir / 'build.json')
    if receipt.get('simv_sha256') and digest(build_dir / 'simv') != receipt['simv_sha256']:
        raise RuntimeError('Saved simulator hash mismatch')
    for name, key in [('stimulus.txt', 'stimulus_sha256'), ('case.json', 'case_sha256')]:
        if digest(old / name) != chosen[key]:
            raise RuntimeError('Saved input hash mismatch: ' + name)
    rid = stamp() + '_replay_' + uuid.uuid4().hex[:6]
    regression = OUT / 'regressions' / rid
    regression.mkdir(parents=True)
    result = run_case(build_dir, regression, chosen['case'], chosen['seed'], None, env,
                      args.timeout, chosen['injection'], old / 'stimulus.txt', old / 'case.json',
                      waves=chosen.get('waves_enabled', True))
    result['replayed_from'] = str(old)
    result['events_match_original'] = result['events_sha256'] == digest(old / 'events.csv')
    ok = result['status'] == 'PASS' and result['events_match_original']
    if chosen.get('format') in ('sv_tc','sv_main_phase'):
        assignment='assigned.csv' if chosen.get('format')=='sv_main_phase' else 'sv_solutions.csv'
        result['exact_replay']={name:digest(Path(result['directory'])/name)==digest(old/name)
            for name in ['case.sv',assignment,'samples.csv','tc_config.json']}
        ok=ok and all(result['exact_replay'].values())
    cov = merge_coverage(regression, [result], env)
    ok = ok and cov['status'] == 'PASS'
    summary = {'id': rid, 'status': 'PASS' if ok else 'FAIL', 'created_at': stamp(),
               'project': PROJECT['name'], 'build': str(build_dir), 'results': [result],
               'passed': int(result['status'] == 'PASS'), 'coverage': cov, 'timezone': 'Asia/Shanghai'}
    write_report(regression, summary)
    write_json(Path(result['directory']) / 'result.json', result)
    write_json(OUT / 'last_command.json', {'id': rid})
    if os.environ.get('EDA_REQUEST_ID'):
        write_json(OUT / 'requests' / (os.environ['EDA_REQUEST_ID'] + '.json'), {'id': rid})
    print('REPLAY events_match_original={} REPORT {}'.format(result['events_match_original'], regression / 'index.html'))
    return 0 if ok else 1


def launch_verdi(args, env):
    regression = select_regression(args.regression)
    summary = read_json(regression / 'summary.json')
    view_dir = regression / 'views' / (args.command + '_' + stamp() + '_' + uuid.uuid4().hex[:6])
    view_dir.mkdir(parents=True)
    log = view_dir / 'verdi.log'
    if args.command == 'coverage':
        database = summary['coverage'].get('database')
        if not database or not Path(database).exists():
            raise ValueError('No merged VDB exists for this regression')
        cmd = ['verdi', '-full64', '-cov', '-covdir', database]
    else:
        result = next((r for r in summary['results'] if args.case and r['id'] == args.case), None)
        if args.case and result is None:
            raise ValueError('Case not in regression: ' + args.case)
        if result is None:
            result = next((r for r in summary['results'] if r['status'] != 'PASS'), summary['results'][0])
        wave = Path(result['directory']) / 'waves.fsdb'
        build_dir = Path(result['build'])
        if not wave.exists():
            raise ValueError('FSDB is missing: ' + str(wave))
        cmd = ['verdi', '-full64', '-dbdir', str(build_dir / 'simv.daidir'), '-ssf', str(wave),
               '-play', str(HERE / 'waves.tcl')]
    if args.display:
        env['DISPLAY'] = args.display
    if args.xauthority:
        env['XAUTHORITY'] = args.xauthority
    elif not env.get('XAUTHORITY'):
        # GDM creates a new auth file for each desktop login. Reuse the active
        # session's path, never copy/display cookies or disable X access control.
        for proc in sorted(Path('/proc').glob('[0-9]*')):
            try:
                if (proc / 'comm').read_text().strip() not in ('gnome-session-b', 'gnome-shell', 'xfce4-session'):
                    continue
                values = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
                if values.get(b'DISPLAY', b'').decode() == env.get('DISPLAY') and values.get(b'XAUTHORITY'):
                    env['XAUTHORITY'] = values[b'XAUTHORITY'].decode()
                    break
            except (OSError, UnicodeError):
                continue
    if not env.get('DISPLAY'):
        raise RuntimeError('No desktop DISPLAY. Start Studio in the Linux graphical session or set DISPLAY.')
    if shutil.which('xdpyinfo', path=env.get('PATH')):
        probe = subprocess.run(['xdpyinfo'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        if probe.returncode:
            raise RuntimeError('Cannot access Linux desktop {}. Use --display / --xauthority.'.format(env['DISPLAY']))
    write_json(log.with_suffix('.command.json'), {'argv': cmd, 'DISPLAY': env['DISPLAY'], 'cwd': str(view_dir)})
    with log.open('w') as out:
        proc = subprocess.Popen(cmd, cwd=str(view_dir), env=env, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(3)
    if proc.poll() is not None:
        raise RuntimeError('Verdi exited early. Check ' + str(log))
    print('VERDI pid={} display={} log={}'.format(proc.pid, env['DISPLAY'], log))
    return 0


def export_results(args):
    regression = select_regression(args.regression)
    output = OUT / 'exports' / (regression.name + '.tar.gz')
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(str(output), 'w:gz') as archive:
        for path in regression.rglob('*'):
            if path.is_file() and (args.all or (path.suffix != '.fsdb' and
                 not any(part.endswith('.vdb') for part in path.parts))):
                archive.add(str(path), arcname=regression.name + '/' + path.relative_to(regression).as_posix())
    print(str(output))
    return output


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local', action='store_true', help='Compatibility flag; execution is always local')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('doctor'); sub.add_parser('build'); sub.add_parser('list')
    sub.add_parser('constraints')
    s = sub.add_parser('constraint-test'); s.add_argument('--jobs',type=int,default=2); s.add_argument('--timeout',type=int,default=120)
    s = sub.add_parser('tc-test'); s.add_argument('--jobs',type=int,default=2); s.add_argument('--timeout',type=int,default=120)
    for name in ('run', 'regress', 'selftest'):
        s = sub.add_parser(name)
        s.add_argument('--jobs', type=int, default=1)
        s.add_argument('--timeout', type=int, default=120)
        s.add_argument('--waves', type=int, choices=[0, 1], default=1)
        if name == 'run':
            s.add_argument('--case', default='tc_directed'); s.add_argument('--seed', type=int, default=1)
            s.add_argument('--cycles', type=int)
        elif name == 'regress':
            s.add_argument('--plan')
            s.add_argument('--seeds'); s.add_argument('--cycles', type=int); s.add_argument('--cases')
            s.add_argument('--random-case')
            s.add_argument('--random-only', action='store_true')
    for name in ('view', 'coverage', 'report', 'open-report', 'trace', 'export'):
        s = sub.add_parser(name)
        s.add_argument('--regression', default='latest')
        if name in ('view', 'trace'):
            s.add_argument('--case')
        if name == 'trace':
            s.add_argument('--limit', type=int, default=50)
        if name in ('view', 'coverage'):
            s.add_argument('--display', default=os.environ.get('EDA_DISPLAY', os.environ.get('DISPLAY','')))
            s.add_argument('--xauthority', default=os.environ.get('EDA_XAUTHORITY'))
        if name == 'export':
            s.add_argument('--all', action='store_true')
    s = sub.add_parser('new-case'); s.add_argument('name'); s.add_argument('--template', default='tc_directed')
    for name in ('validate', 'explain', 'diagram'):
        s = sub.add_parser(name)
        s.add_argument('--case', default='tc_directed'); s.add_argument('--seed', type=int, default=1)
        s.add_argument('--cycles', type=int)
    s = sub.add_parser('replay')
    s.add_argument('--regression', default='latest'); s.add_argument('--case', required=True)
    s.add_argument('--timeout', type=int, default=120)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help(); return 2
    if args.command == 'open-report':
        subprocess.check_call([sys.executable, str(HERE/'gui/desktop.py'), '--page', 'results', '--batch', args.regression])
        return 0
    if args.command == 'new-case':
        if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*', args.name):
            raise ValueError('Case name must contain only letters, digits and underscore')
        template_path = case_path(args.template)
        if template_path.suffix != '.sv':
            raise ValueError('New cases use SV: TEMPLATE=tc_directed or tc_random')
        path = sv_cases.new_case(args.name, template_path)
        print(str(path))
        print('Next: make explain CASE={0}; make run CASE={0} SEED=17'.format(args.name)); return 0
    if args.command == 'list':
        for path in sorted(sv_cases.TC_DIR.glob('*.sv')):
            cfg=sv_cases.load(path)
            print('{} [SV]: {}'.format(path.stem,cfg['description']))
        print('Legacy JSON cases (explicit compatibility only):')
        for path in sorted((HERE / 'cases').glob('*.json')):
            cfg = load_case(path)
            print('{}: {}'.format(path.stem, cfg.get('description', '')))
        return 0
    if getattr(args, 'jobs', 1) < 1 or getattr(args, 'timeout', 1) < 1:
        raise ValueError('jobs and timeout must be positive')
    if args.command in ('validate', 'explain', 'diagram'):
        cfg = load_input(case_path(args.case))
        if cfg.get('format') == 'sv_main_phase':
            if args.command == 'diagram':
                raise ValueError('SV TC uses make explain/run/wave; no WaveDrom generation')
            input_program(cfg,args.seed,args.cycles)
            if args.command == 'explain':
                print(sv_cases.explain(cfg)); return 0
            print('VCS_COMPILE_VALID '+str(build(tool_env())))
            print('Procedural assignments and runtime contracts are checked by make run CASE='+cfg['name'])
            return 0
        program = make_program(cfg, args.seed, args.cycles)
        print('VALID {}: {} instructions, seed={}'.format(args.case, len(program.splitlines()), args.seed))
        if args.command == 'explain':
            from case_adapter import explain_case
            print(explain_case(cfg, args.cycles))
        if args.command == 'diagram':
            if 'signal' not in cfg:
                raise ValueError('diagram requires a WaveDrom case')
            from wavedrom_adapter import write_preview
            output = OUT / 'previews' / (case_path(args.case).stem + '.html')
            write_preview(cfg, output)
            print('DIAGRAM ' + str(output))
        return 0
    if args.command == 'export':
        export_results(args); return 0
    if args.command == 'constraints':
        from contracts import audit
        receipt=audit(); output=OUT / 'contracts' / 'inventory.json'
        write_json(output,receipt)
        for rule in receipt['rules']:
            print('{} [{} x{}] {}'.format(rule['id'],rule['kind'],rule['instances'],rule['trigger']))
        print('CONTRACTS PASS: 4 families / 11 assumption instances; '+str(output)); return 0
    if args.command == 'trace':
        if args.limit < 1:
            raise ValueError('trace limit must be positive')
        regression = select_regression(args.regression)
        summary = read_json(regression / 'summary.json')
        result = next((r for r in summary['results'] if r['id'] == args.case), None) if args.case else summary['results'][0]
        if result is None:
            raise ValueError('Case not in regression: ' + args.case)
        print('{} {} {}'.format(summary['id'], result['id'], result['status']))
        print(json.dumps(result['metrics'], ensure_ascii=False, indent=2))
        event_path = Path(result['directory']) / 'events.csv'
        with event_path.open() as stream:
            reader = csv.reader(stream)
            for i, row in enumerate(reader):
                if i > args.limit:
                    print('... use LIMIT=N for more events'); break
                print(','.join(row))
        print(str(event_path)); return 0
    if args.command == 'report':
        regression = select_regression(args.regression)
        summary = read_json(regression / 'summary.json')
        print('{} {} {}/{}'.format(summary['id'], summary['status'], summary['passed'], len(summary['results'])))
        for result in summary['results']:
            print('{} {} {}'.format(result['status'], result['id'], '; '.join(result['failures'])))
        print(str(regression / 'index.html')); return 0
    env = tool_env()
    if args.command == 'tc-test':
        from main_phase_suite import run_suite
        return run_suite(args,env)
    if args.command == 'constraint-test':
        from constraint_suite import run_suite
        return run_suite(args,env)
    if args.command == 'doctor':
        return platform_config.doctor(env,OUT)
    if args.command == 'build':
        build(env); return 0
    if args.command in ('run', 'regress', 'selftest'):
        return regress(args, env)
    if args.command == 'replay':
        return replay(args, env)
    if args.command in ('view', 'coverage'):
        return launch_verdi(args, env)
    raise ValueError('Unsupported command: '+args.command)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('EDA ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(2)
