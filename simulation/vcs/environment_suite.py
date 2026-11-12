"""Real VCS acceptance: stop/continue, independent verdict, waveform and coverage."""
import argparse
import concurrent.futures
import csv
from pathlib import Path
import uuid
import eda


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args(argv)
    env = eda.tool_env()
    build = eda.build(env)
    rid = eda.stamp()+'_environment_'+uuid.uuid4().hex[:6]
    root = eda.OUT/'regressions'/rid
    root.mkdir(parents=True)
    fixtures = eda.HERE/'tests/main_phase'
    specs = []
    def add(case, seed=1, env_fatal=False, assert_fatal=True, failure='', completed=True,
            cycles=16, waves=False, injection='', hard=False, mode='transaction'):
        path = str(fixtures/(case+'.sv')) if case.startswith('main_') else case
        specs.append(dict(case=path, seed=seed, env_fatal=env_fatal, assert_fatal=assert_fatal,
                          failure=failure, completed=completed, cycles=cycles, waves=waves,
                          injection=injection, hard=hard, mode=mode))
    add('tc_directed', 17, cycles=64, waves=True)
    for seed in (17, 42): add('tc_random', seed, cycles=64)
    add('main_bad_src', env_fatal=True, failure='MAIN_E03_SRC', completed=False, waves=True)
    add('main_bad_src', 2, failure='MAIN_E03_SRC', waves=True)
    add('main_bad_src', 3, env_fatal=True, assert_fatal=False, failure='MAIN_E03_SRC', completed=False)
    # The reference DUT has its own $stop for an accepted illegal arresp.
    # It is deliberately outside ENV_FATAL; verify that this stays an explicit failure.
    add('main_bad_resp', failure='MAIN_E04_ARRESP', completed=False)
    add('main_env_multiple', failure='MAIN_E04_ARRESP')
    for suffix, label in [('busy','MAIN_E06_SUCCESS_BUSY'),
                          ('mem','MAIN_T_MEM4'), ('unknown','MAIN_P_KNOWN'),
                          ('missing','MAIN_MISSING_ASSIGNMENT'), ('duplicate','MAIN_DRIVE_ORDER'),
                          ('race','MAIN_ONCE_AFTER_POSEDGE'), ('stale_context','MAIN_CONTEXT_TIMING'),
                          ('reset','MAIN_RESET_FIXED_HIGH_AFTER_1NS'), ('zero_ready','MAIN_E05_ZERO_READY')]:
        add('main_bad_'+suffix, failure=label)
    add('main_bad_control', failure='MAIN_CONTROL_INVALID', completed=False, hard=True)
    add('tc_directed', 1, injection='rid', failure='T_R_ID_ORDER', cycles=64)
    add('tc_directed', 2, env_fatal=True, assert_fatal=False, injection='rid', failure='T_R_ID_ORDER', cycles=64)
    add('main_bad_mem', 2, failure='MAIN_T_MEM4', mode='strict')

    def run(spec):
        return eda.run_case(build, root, spec['case'], spec['seed'], spec['cycles'], env, 120,
                            injection=spec['injection'], waves=spec['waves'], check_mode=spec['mode'],
                            assert_fatal=spec['assert_fatal'], env_fatal=spec['env_fatal'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, specs))
    checks = []
    def require(name, good, detail=None):
        checks.append(dict(name=name, passed=bool(good), detail=detail))
    for spec, result in zip(specs, results):
        log = (Path(result['directory'])/'sim.log').read_text(errors='replace')
        runtime = result['checks']['environment']['runtime']
        require(result['id']+'_verdict', result['status']==('FAIL' if spec['failure'] else 'PASS'), result['failures'])
        require(result['id']+'_completion', result['completed']==spec['completed'], runtime)
        if spec['failure']: require(result['id']+'_diagnostic', spec['failure'] in log, spec['failure'])
        if spec['completed']:
            # Duplicate/missing assignment fixtures intentionally violate body counts.
            if not spec['case'].endswith(('main_bad_missing.sv', 'main_bad_duplicate.sv')):
                require(result['id']+'_body_cycles', result['metrics'].get('tc_body_cycles')==spec['cycles'], result['metrics'].get('tc_body_cycles'))
            require(result['id']+'_tail', 'MAIN_PHASE_DONE' in log and 'ENV_RUN_COMPLETE' in log)
            require(result['id']+'_archive_integrity', not result['checks']['integrity_errors'], result['checks']['integrity_errors'])
        if spec['hard']: require(result['id']+'_unrecoverable', runtime.get('unrecoverable')==1 and 'ENV_UNRECOVERABLE' in log)
        if spec['waves']:
            require(result['id']+'_waves', set(result['wave_artifacts'])=={'waves.fsdb','waves.vcd'})
        if result['case']=='main_bad_resp':
            require('dut_stop_not_silently_suppressed', '$stop at time' in log and not result['completed'])
        if result['case']=='main_env_multiple':
            labels=[e['label'] for e in result['checks']['environment']['failures']]
            require('all_repeated_environment_errors_archived', labels.count('MAIN_E03_SRC')==3 and labels.count('MAIN_E04_ARRESP')==3)
        if result['case']=='main_bad_src' and not spec['env_fatal']:
            with (Path(result['directory'])/'samples.csv').open() as stream:
                samples=list(csv.DictReader(stream))
            require('invalid_input_not_repaired', any(r['arvalid']=='1' and r['src']=='11' for r in samples))
            require('master_disables_assertion_abort', result['checks']['runtime']['assert_fatal']==1 and
                    result['checks']['runtime']['effective_assert_fatal']==0 and result['checks']['runtime']['failures']>0)
    cov = eda.merge_coverage(root, results, env)
    passing = {r['id'] for r in results if r['status']=='PASS' and not r['injection']}
    require('coverage_includes_only_valid_passes', cov['status']=='PASS' and set(cov['included'])==passing and
            all(r['id'] in cov['excluded'] for r in results if r['status']=='FAIL'), cov)
    receipt = dict(status='PASS' if all(c['passed'] for c in checks) else 'FAIL', checks=checks,
                   scope='Finite VCS testcases; explicit hard stops remain. No arbitrary user code completion guarantee.',
                   specifications=specs, sources=eda.manifest(), directory=str(root))
    eda.write_json(root/'environment_acceptance.json', receipt)
    # Acceptance PASS means the expected negative results were detected, not RTL PASS.
    summary = dict(id=rid, status=receipt['status'], created_at=eda.stamp(), project=eda.PROJECT['name'],
                   timezone='Asia/Shanghai', build=str(build), results=results, coverage=cov,
                   passed=len(passing), acceptance='environment_acceptance.json')
    eda.write_report(root, summary)
    eda.write_json(eda.OUT/'last_environment_test.json', dict(id=rid, directory=str(root), status=receipt['status']))
    print('ENV_FATAL_TEST {} {}/{} {}'.format(receipt['status'], sum(c['passed'] for c in checks), len(checks), root), flush=True)
    return 0 if receipt['status']=='PASS' else 1


if __name__=='__main__':
    eda.execution.install_signal_handlers()
    raise SystemExit(main())
