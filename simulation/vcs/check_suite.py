"""VCS acceptance of snapshot boundaries, transaction identity and fatal policy."""
import argparse
import concurrent.futures
import csv
import json
from pathlib import Path
import uuid
import eda
import check_policy
from dut_config import PORTS


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


def port_wave_check(directory):
    """Check real DUT pin transition times in the VCD converted from FSDB."""
    from gui import vcd

    root = Path(directory)
    database = vcd.index(root/'waves.vcd', root/'wave_index')
    metadata = vcd.describe(database)
    prefix = 'tb_cmdbuf_rd.dut.'
    names = {name for name, _, _ in PORTS if name != 'rd_mem_status'}
    names.update('rd_mem_status[{}]'.format(bank) for bank in range(8))
    pins = {}
    for signal in metadata['signals']:
        if not signal['name'].startswith(prefix):
            continue
        # Remove the bus bit range, retaining the index of each Bank word.
        name = signal['name'][len(prefix):]
        if name.endswith(']') and ':' in name.rsplit('[', 1)[-1]:
            name = name.rsplit('[', 1)[0]
        if name in names:
            pins[name] = signal['id']
    missing = sorted(names - pins.keys())
    if missing or metadata['timescale'].replace(' ', '') != '1ps':
        return dict(passed=False, missing=missing, timescale=metadata['timescale'])
    records = vcd.window(database, list(pins.values()), 0, metadata['end'])['signals']
    waves = {item['id']: item for item in records}
    rises = {time for time, value in waves[pins['clk']]['values'] if time > 0 and value == '1'}
    release = waves[pins['rst_n']]['values'] == [(0, '0'), (1000, '1')]
    changes = {name: [time for time, _ in waves[identifier]['values'] if time > 1000]
               for name, identifier in pins.items() if name not in ('clk', 'rst_n')}
    off_edge = {name: [time for time in times if time not in rises] for name, times in changes.items()}
    off_edge = {name: times for name, times in off_edge.items() if times}
    return dict(passed=release and not off_edge and bool(changes['arvalid']) and
                not any(item['dense'] for item in records),
                scope=prefix[:-1], ports=len(pins), posedges=len(rises), reset_release_ps=1000 if release else None,
                transitions=sum(map(len, changes.values())), counts={name:len(times) for name,times in changes.items()},
                arvalid_transitions_ps=changes['arvalid'], off_edge=off_edge)


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
               ('bad_resp',1,1),('bad_both',0,1),('bad_priority',0,1),('association_conflict',0,1),
               ('legacy_parity',0,1)]
    response_reasons={'not_issued':'NOT_ISSUED', 'early':'MEM_NOT_CAPTURED',
                      'bad_data':'DATA_MISMATCH', 'ambiguous_bad_data':'DATA_MISMATCH',
                      'bad_resp':'RESP_MISMATCH', 'bad_priority':'RESP_MISMATCH',
                      'bad_both':'DATA_AND_RESP_MISMATCH', 'association_conflict':'NO_COMMON_ASSOCIATION',
                      'legacy_parity':'RESP_MISMATCH'}
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
            if name=='legacy_parity':
                require('parity_mismatch_reports_new_expected_value',
                        all(field in detail for field in ('actual_resp=1 ', 'expected_resp=2 ', 'captured_pc_err=1]')),
                        detail)
            if fatal:
                require('fatal_line_contains_response_reason',
                        any('CHECK_STOP T_R_DATA_RESP_OR_EARLY' in line and
                            'reason='+response_reasons[name]+' ' in line for line in log.splitlines()), detail)
    build=eda.build(env)
    batch=root/'integration';batch.mkdir()
    jobs=[('tc_directed',mode,fatal,mutation) for mode in ('transaction','strict') for fatal in (True,False)
          for mutation in ('', 'rid', 'pending_lookahead', 'pending_delayed')]
    jobs.extend(('tc_directed',mode,False,'rvalid_unready') for mode in ('transaction','strict'))
    response_case=eda.HERE/'tests/main_phase/main_response_codes.sv'
    jobs.extend((str(response_case),mode,not mutation,mutation) for mode in ('transaction','strict')
                for mutation in ('','rresp_legacy'))
    def run(spec):
        case,mode,fatal,mutation=spec
        container=batch/(mode+'_'+str(int(fatal)));container.mkdir(exist_ok=True)
        seed=17+(100 if mode=='strict' else 0)+(10 if not fatal else 0)
        result=eda.run_case(build,container,case,seed,None,env,120,injection=mutation,
                            waves=(case=='tc_directed' and mode=='transaction' and fatal and not mutation),
                            check_mode=mode,assert_fatal=fatal)
        expected='FAIL' if mutation else 'PASS'
        return spec,result,expected
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows=list(pool.map(run,jobs))
    for (case,mode,fatal,mutation),result,expected in rows:
        name='{}_fatal{}_{}'.format(mode,int(fatal),mutation or 'baseline')
        if case==str(response_case): name='response_codes_'+name
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
        if case==str(response_case) and not mutation:
            with (Path(result['directory'])/'events.csv').open() as stream:
                events=list(csv.DictReader(stream))
            responses=[(int(e['id_hex'],16),int(e['status'])) for e in events if e['event']=='R']
            captured_errors=[int(e['status']) for e in events if e['event']=='MEM']
            require(name+'_all_response_codes',responses==[(0x20,0),(0x21,2),(0x22,3),(0x23,3)] and
                    captured_errors==[0,1,0,1] and result['metrics'].get('resp2')==1 and result['metrics'].get('resp1')==0,
                    dict(responses=responses,captured_errors=captured_errors))
        if mutation=='rresp_legacy':
            labels={f['label'] for f in result['checks']['failures']}
            required={'T_R_DATA_RESP_OR_EARLY'} if mode=='transaction' else {'A_COMPARE_RRESP','A_XAU_R_RESP_ORDER'}
            require(name+'_rejects_legacy_encoding',required.issubset(labels),sorted(labels))
    results=[row[1] for row in rows]
    waveform = next(r for r in results if r['waves_enabled'])
    wave_check = pending_wave_check(waveform['directory'])
    require('registered_pending_updates_at_transfer_edges', wave_check['passed'], wave_check)
    pins = port_wave_check(waveform['directory'])
    require('dut_ports_change_at_posedge', pins['passed'], pins)
    cov=eda.merge_coverage(batch,results,env)
    require('coverage_excludes_failed_and_injected_runs',cov['status']=='PASS' and
            len(cov['included'])==6 and all(r['id'] in cov['excluded'] for r in results if r['status']!='PASS'),cov)
    receipt=dict(status='PASS' if all(c['passed'] for c in checks) else 'FAIL',checks=checks,coverage=cov,
                 results=results,sources=eda.manifest(),directory=str(root))
    eda.write_json(root/'acceptance.json',receipt)
    eda.write_json(eda.OUT/'last_check_test.json',dict(directory=str(root),status=receipt['status']))
    print('CHECK_TEST {} {}/{} {}'.format(receipt['status'],sum(c['passed'] for c in checks),len(checks),root))
    return 0 if receipt['status']=='PASS' else 1


if __name__=='__main__':
    eda.execution.install_signal_handlers()
    raise SystemExit(main())
