"""VCS acceptance of snapshot boundaries, transaction identity and fatal policy."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import uuid
import eda
import check_policy


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jobs',type=int,default=2)
    args=p.parse_args(argv)
    env=eda.tool_env()
    root=eda.OUT/'check_tests'/(eda.stamp()+'_'+uuid.uuid4().hex[:6]);root.mkdir(parents=True)
    checks=[]
    def require(name, condition, detail=None):
        checks.append(dict(name=name,passed=bool(condition),detail=detail))
    fixture=root/'fixture';fixture.mkdir()
    rc,_=eda.execute(eda.vcs_options(fixture,env)+['-sverilog','-top','monitor_fixture',
        str(eda.HERE/'tb/cmdbuf_check_monitor.sv'),str(eda.HERE/'tests/checks/monitor_fixture.sv'),
        '-o',str(fixture/'simv')],fixture,fixture/'compile.log',env,None,phase='compile')
    if rc: raise RuntimeError('Fixture compile failed: '+str(fixture/'compile.log'))
    scenarios=[('snapshot',0,0),('late',0,0),('ambiguous',0,0),('ambiguous_bad_data',0,1),
               ('unfinished',0,0),('reset_cancel',0,0),('last_bad',0,1),('reset_masks_error',0,1),
               ('two_errors',0,2),('two_errors',1,1),('duplicate',0,1),('ambiguous_limit',0,0),
               ('last_x',0,1),('same_id_order',0,2),('reuse',0,0)]
    for name,fatal,minimum in scenarios:
        directory=fixture/(name+'_'+str(fatal));directory.mkdir()
        cmd=[str(fixture/'simv'),'+vcs+lic+wait','+SCENARIO='+('ambiguous' if name=='ambiguous_limit' else name),
             '+CHECK_MODE=transaction','+ASSERT_FATAL='+str(fatal)]
        if name=='ambiguous_limit':cmd.append('+CHECK_STATE_LIMIT=1')
        rc,_=eda.execute(cmd,directory,directory/'sim.log',env,60,wait_for_start=True,phase='simulation')
        scope=check_policy.report(directory,dict(check_mode='transaction',assert_fatal=bool(fatal)))
        eda.write_json(directory/'check_scope.json',scope)
        runtime=scope['runtime'];failure_count=runtime.get('failures',-1)
        require(name+'_failure_count_fatal_'+str(fatal),failure_count>=minimum if minimum else failure_count==0,runtime)
        require(name+'_archive_integrity_fatal_'+str(fatal),not scope['integrity_errors'],scope['integrity_errors'])
        if fatal:
            # Some VCS versions return zero even for $fatal. Check the evidence,
            # not the process exit code, exactly as the production runner does.
            log=(directory/'sim.log').read_text(errors='replace')
            require('fatal_stops_after_first',failure_count==1 and not runtime.get('finished') and 'FIXTURE_DONE' not in log)
        else:
            require(name+'_flushes_last_frame',runtime.get('finished') and runtime['sampled_frames']==runtime['checked_frames'])
        if name in ('unfinished','ambiguous_limit'):
            require(name+'_not_pass',scope['groups'][1]['status']=='INCOMPLETE')
        elif not minimum: require(name+'_complete',scope['groups'][1]['status']=='PASS')
        if name=='ambiguous':require('retains_both_issue_identities',runtime['peak_hypotheses']>=2)
        if name=='reset_cancel':require('async_reset_cancels_pending',runtime['cancelled']==1)
    build=eda.build(env)
    batch=root/'integration';batch.mkdir()
    jobs=[(mode,fatal,mutation) for mode in ('transaction','strict') for fatal in (True,False)
          for mutation in ('','rid','pending_registered')]
    def run(spec):
        mode,fatal,mutation=spec
        container=batch/(mode+'_'+str(int(fatal)));container.mkdir(exist_ok=True)
        seed=17+(100 if mode=='strict' else 0)+(10 if not fatal else 0)
        result=eda.run_case(build,container,'tc_directed',seed,None,env,120,injection=mutation,
                            waves=(mode=='transaction' and fatal and not mutation),check_mode=mode,assert_fatal=fatal)
        expected='FAIL' if mutation=='rid' or (mutation=='pending_registered' and mode=='strict') else 'PASS'
        return spec,result,expected
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows=list(pool.map(run,jobs))
    for (mode,fatal,mutation),result,expected in rows:
        name='{}_fatal{}_{}'.format(mode,int(fatal),mutation or 'baseline')
        require(name,result['status']==expected,dict(status=result['status'],expected=expected,failures=result['failures']))
        runtime=result['checks']['runtime']
        if expected=='FAIL' and not fatal:
            require(name+'_continued_to_end',runtime.get('finished')==1 and runtime.get('failures',0)>0,runtime)
        if expected=='PASS':
            require(name+'_scope',result['checks']['groups'][2]['status']==('PASS' if mode=='strict' else 'NOT_CHECKED'))
    results=[row[1] for row in rows]
    cov=eda.merge_coverage(batch,results,env)
    require('coverage_excludes_failed_and_injected_runs',cov['status']=='PASS' and
            len(cov['included'])==4 and all(r['id'] in cov['excluded'] for r in results if r['status']!='PASS'),cov)
    receipt=dict(status='PASS' if all(c['passed'] for c in checks) else 'FAIL',checks=checks,coverage=cov,
                 results=results,sources=eda.manifest(),directory=str(root))
    eda.write_json(root/'acceptance.json',receipt)
    eda.write_json(eda.OUT/'last_check_test.json',dict(directory=str(root),status=receipt['status']))
    print('CHECK_TEST {} {}/{} {}'.format(receipt['status'],sum(c['passed'] for c in checks),len(checks),root))
    return 0 if receipt['status']=='PASS' else 1


if __name__=='__main__':
    eda.execution.install_signal_handlers()
    raise SystemExit(main())
