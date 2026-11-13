"""VCS acceptance of snapshot boundaries, transaction identity and fatal policy."""
import argparse
import concurrent.futures
import csv
import json
from pathlib import Path
import uuid
import eda
import check_policy


def pending_wave_check(directory):
    from gui import vcd

    root = Path(directory)
    database = vcd.index(root/'waves.vcd', root/'wave_index')
    metadata = vcd.describe(database)
    signal = next(s for s in metadata['signals'] if
                  s['name'].split('[')[0]=='tb_cmdbuf_rd.dut.pending_cnt')
    series = vcd.window(database, [signal['id']], 0, metadata['end'])['signals'][0]['values']
    actual = [(time, int(value, 2)) for time,value in series]
    expected = [(0, 0)]
    count = 0
    pre_edge_matches = True
    with (root/'samples.csv').open() as stream:
        for sample in csv.DictReader(stream):
            pre_edge_matches &= int(sample['pending_cnt'], 16)==count
            accept = sample['arvalid']=='1' and bool(int(sample['arready'], 16) & (1 << int(sample['src'], 2)))
            issue = bool(int(sample['req'], 16) & int(sample['ar_rd_gnt'], 2) & int(sample['rw_rd_gnt'], 2))
            updated = count+int(accept)-int(issue)
            if updated != count:
                expected.append((int(sample['time_ps']), updated))
            count = updated
    return dict(passed=metadata['timescale'].replace(' ', '')=='1ps' and pre_edge_matches and
                       len(expected)>1 and actual==expected,
                timescale=metadata['timescale'], actual=actual, expected=expected,
                pre_edge_matches=pre_edge_matches, scope=signal['name'])


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
               ('last_x',0,1),('same_id_order',0,2),('reuse',0,0),
               ('capture_forward',0,0),('parity',0,0),('priority',0,0),
               ('not_issued',0,1),('early',0,1),('bad_data',0,1),('bad_resp',0,1),
               ('bad_resp',1,1),('bad_both',0,1),('bad_priority',0,1),('association_conflict',0,1)]
    response_reasons={'not_issued':'NOT_ISSUED', 'early':'MEM_NOT_CAPTURED',
                      'bad_data':'DATA_MISMATCH', 'ambiguous_bad_data':'DATA_MISMATCH',
                      'bad_resp':'RESP_MISMATCH', 'bad_priority':'RESP_MISMATCH',
                      'bad_both':'DATA_AND_RESP_MISMATCH', 'association_conflict':'NO_COMMON_ASSOCIATION'}
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
        if name in response_reasons:
            details=[r['detail'] for r in scope['failures'] if r['label']=='T_R_DATA_RESP_OR_EARLY']
            detail=details[0] if details else ''
            require(name+'_response_reason_fatal_'+str(fatal),
                    'reason='+response_reasons[name]+' ' in detail and 'accepted_edge=2 ' in detail and
                    'actual_data=' in detail and 'actual_resp=' in detail and 'joint_matches=0 ' in detail, detail)
            if name=='early':require('early_expected_capture_edge', 'due_edge=7 ' in detail, detail)
            if name=='association_conflict':
                require('marginal_matches_are_not_joint_matches',
                        'data_matches=1 resp_matches=1 joint_matches=0 ' in detail, detail)
            if fatal:
                require('fatal_line_contains_response_reason',
                        any('CHECK_STOP T_R_DATA_RESP_OR_EARLY' in line and
                            'reason='+response_reasons[name]+' ' in line for line in log.splitlines()), detail)
    build=eda.build(env)
    batch=root/'integration';batch.mkdir()
    jobs=[(mode,fatal,mutation) for mode in ('transaction','strict') for fatal in (True,False)
          for mutation in ('', 'rid', 'pending_lookahead', 'pending_delayed')]
    jobs.extend((mode, False, 'rvalid_unready') for mode in ('transaction', 'strict'))
    def run(spec):
        mode,fatal,mutation=spec
        container=batch/(mode+'_'+str(int(fatal)));container.mkdir(exist_ok=True)
        seed=17+(100 if mode=='strict' else 0)+(10 if not fatal else 0)
        result=eda.run_case(build,container,'tc_directed',seed,None,env,120,injection=mutation,
                            waves=(mode=='transaction' and fatal and not mutation),check_mode=mode,assert_fatal=fatal)
        expected='FAIL' if mutation else 'PASS'
        return spec,result,expected
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows=list(pool.map(run,jobs))
    for (mode,fatal,mutation),result,expected in rows:
        name='{}_fatal{}_{}'.format(mode,int(fatal),mutation or 'baseline')
        require(name,result['status']==expected,dict(status=result['status'],expected=expected,failures=result['failures']))
        runtime=result['checks']['runtime']
        if expected=='FAIL' and not fatal:
            require(name+'_continued_to_end',runtime.get('finished')==1 and runtime.get('failures',0)>0,runtime)
        if mutation in ('pending_lookahead', 'pending_delayed'):
            require(name+'_pending_label', any(f['label']=='A_PENDING_RECURRENCE' or
                (mode=='strict' and fatal and f['label']=='A_COMPARE_PENDING_CNT') for f in result['checks']['failures']))
        if mutation=='rvalid_unready':
            require(name+'_r_ready_label', any(f['label']=='A_R_READY' for f in result['checks']['failures']))
        if expected=='PASS':
            require(name+'_scope',result['checks']['groups'][2]['status']==('PASS' if mode=='strict' else 'NOT_CHECKED'))
    results=[row[1] for row in rows]
    waveform = next(r for r in results if r['waves_enabled'])
    wave_check = pending_wave_check(waveform['directory'])
    require('registered_pending_updates_at_transfer_edges', wave_check['passed'], wave_check)
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
