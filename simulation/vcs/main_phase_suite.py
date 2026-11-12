"""Executable main_phase acceptance for procedural legal inputs and posedge timing."""
import concurrent.futures
import copy
import csv
import json
import os
from pathlib import Path
import tempfile
import uuid
import eda


def mutation_checks(directory):
    from main_phase_audit import verify_execution
    root=Path(directory)
    with (root/'samples.csv').open() as f: samples=list(csv.DictReader(f))
    with (root/'assigned.csv').open() as f: solutions=list(csv.DictReader(f))
    def find(predicate):return next(i for i,s in enumerate(samples) if predicate(s))
    active=find(lambda s:s['rst_n']=='1' and s['arvalid']=='1')
    busy=find(lambda s:int(s['req'],16)&int(s['ar_rd_gnt'],2)&int(s['rw_rd_gnt'],2)!=0)
    response=find(lambda s:int(s['rvalid'],16)&int(s['rready'],2)!=0)
    idle=find(lambda s:s['arvalid']=='0' and int(s['req'],16)==0)
    issue_edge=int(samples[busy]['edge'])
    mem=find(lambda s:int(s['edge'])==issue_edge+4)
    bank=(int(samples[busy]['req'],16).bit_length()-1)
    half=int(samples[busy]['arb_addr'],16)&1
    checks=[]
    mutations=[('pin_drift',active,'arid','00000001',False,'MAIN_PIN_MISMATCH'),
      ('illegal_src',active,'src','11',True,'E03_SRC'),
      ('illegal_resp',active,'arresp','01',True,'E04_ARRESP'),
      ('blocked_idle_src',idle,'src','00',True,'E07_SRC_READY'),
      ('spurious_grant',idle,'ar_rd_gnt','10000000',True,'E08_AR_GRANT_REQ'),
      ('unready_r',response,'rready','000',True,'A_R_READY'),
      ('busy_clear',busy,'rd_mem_status[{}]'.format(bank),format(int(samples[busy]['rd_mem_status[{}]'.format(bank)],2)&~(1<<half),'02b'),True,'E06_SUCCESS_BUSY'),
      ('mem_corrupt',mem,'mem_rdata',format(int(samples[mem]['mem_rdata'],2)^1,'0128b'),True,'T_MEM4'),
      ('fifo_id',response,'rid',format(int(samples[response]['rid'],16)^1,'02x'),False,'T_ASSOCIATION_FIFO_DATA'),
      ('clock_time',active,'time_ps',str(int(samples[active]['time_ps'])+1),False,'MAIN_CLOCK_RISING_TRACE'),
      ('context',busy,'req','00',False,'MAIN_CONTEXT_CHANGED')]
    for name,index,column,value,both,marker in mutations:
        s=copy.deepcopy(samples);q=copy.deepcopy(solutions)
        s[index][column]=value
        if both:q[index][column]=value
        if name=='blocked_idle_src':
            s[index]['arready']=q[index]['arready']='6'
        with tempfile.TemporaryDirectory(prefix='sv_audit_') as tmp:
            dest=Path(tmp)
            for filename,rows in [('samples.csv',s),('assigned.csv',q)]:
                with (dest/filename).open('w',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            (dest/'tc_config.json').write_bytes((root/'tc_config.json').read_bytes())
            (dest/'clock_reset.csv').write_bytes((root/'clock_reset.csv').read_bytes())
            report=verify_execution(dest,(root/'stimulus.txt').read_text())
            checks.append(dict(name=name,expected=marker,detected=report['status']=='FAIL' and marker in ' '.join(report['failures']),failures=report['failures']))
    return checks


def run_suite(args,env):
    build=eda.build(env)
    rid=eda.stamp()+'_main_phase_'+uuid.uuid4().hex[:6]
    root=eda.OUT/'regressions'/rid;root.mkdir(parents=True)
    fixtures=eda.HERE/'tests'/'main_phase'
    tests=[(name,1,None,'',None) for name in ['tc_directed','tc_retry','tc_backpressure','tc_same_id','tc_banks','tc_reset','tc_user_example']]
    tests += [('tc_random',seed,2000,'',None) for seed in (1,42)]
    tests += [(str(fixtures/'main_user_values.sv'),17,500,'',None)]
    tests += [(str(fixtures/'main_first_cycle.sv'),17,None,'',None)]
    interface = str(fixtures/'main_interface_rules.sv')
    tests.append((interface, 17, None, '', None))
    interface_failures = {'src_ready_idle': 'E07_SRC_READY', 'grant_without_req': 'E08_AR_GRANT_REQ'}
    tests.extend((interface, 17, None, injection, 'MAIN_'+label) for injection,label in interface_failures.items())
    negatives={'src':'MAIN_E03_SRC','resp':'MAIN_E04_ARRESP','busy':'MAIN_E06_SUCCESS_BUSY',
      'mem':'MAIN_T_MEM4','zero_ready':'MAIN_E05_ZERO_READY','control':'MAIN_CONTROL_INVALID',
      'unknown':'MAIN_P_KNOWN','missing':'MAIN_MISSING_ASSIGNMENT','duplicate':'MAIN_DRIVE_ORDER',
      'race':'MAIN_ONCE_AFTER_POSEDGE','stale_context':'MAIN_CONTEXT_TIMING',
      'reset':'MAIN_RESET_FIXED_HIGH_AFTER_1NS'}
    for name,marker in sorted(negatives.items()):
        tests.append((str(fixtures/('main_bad_'+name+'.sv')),1,None,'',marker))
    tests += [('tc_random',1,20,'main_post','MAIN_PIN_MODIFICATION')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures=[pool.submit(eda.run_case,build,root,name,seed,cycles,env,args.timeout,inj,
                             env_fatal=inj not in interface_failures) for name,seed,cycles,inj,_ in tests]
        results=[f.result() for f in futures]
    for test,result in zip(tests,results):
        marker=test[-1]
        log=(Path(result['directory'])/'sim.log').read_text(errors='replace')
        result['expected_failure']=marker
        result['acceptance_pass']=(result['status']=='FAIL' and marker in log) if marker else result['status']=='PASS'
        if result['injection'] in interface_failures:
            label = interface_failures[result['injection']]
            result['acceptance_pass'] &= result['completed'] and any(
                failure['label']==label for failure in result['checks']['failures'])
        eda.write_json(Path(result['directory'])/'result.json',result)
    positive=[r for r in results if not r['expected_failure']]
    bycase={r['case']:r for r in positive}
    mutations=mutation_checks(bycase['tc_directed']['directory'])
    interface_result = bycase['main_interface_rules']
    with (Path(interface_result['directory'])/'samples.csv').open() as stream:
        interface_rows = {int(row['cycle']): row for row in csv.DictReader(stream) if row['phase']=='0'}
    full_idle = any(row['arready']=='0' and row['arvalid']=='0' and row['src']=='11' for row in interface_rows.values())
    partial_idle = interface_rows.get(7, {})
    lane_select = interface_rows.get(24, {})
    return_before_drop = interface_rows.get(27, {}).get('rvalid')
    return_after_drop = interface_rows.get(28, {}).get('rvalid')
    return_after_resume = interface_rows.get(29, {}).get('rvalid')
    hits=bycase['tc_banks']['drive_audit']['coverage']['E06_SUCCESS_BUSY']
    reset=bycase['tc_reset']['drive_audit']['coverage']
    with (Path(bycase['main_user_values']['directory'])/'samples.csv').open() as f:
        rows=[s for s in csv.DictReader(f) if s['phase']=='0' and s['rst_n']=='1' and s['arvalid']=='1']
    user_ok=bool(rows) and all(int(s['src'],2) in (0,2) and 0x12000<=int(s['araddr'],2)<=0x121ff and
        int(s['arid'],2)==0x5a and int(s['ar_port_id'],2)==17 and int(s['arresp'],2)==3 for s in rows)
    cov=eda.merge_coverage(root,positive,env)
    checks={'all_cases_match_expectation':all(r['acceptance_pass'] for r in results),
            'new_case_template_edits_sent_three_requests':bycase['tc_user_example']['metrics']['accepted']==3 and bycase['tc_user_example']['metrics']['retired']==3,
            'all_bank_half_contracts_exercised':all(hits),
            'startup_reset_only_and_paused_clock_preserves_requests':reset['paused_intervals']==4 and reset['cancelled']==0 and reset['reset_assertions']==1,
            'first_procedural_cycle_can_send':bycase['main_first_cycle']['metrics']['accepted']==1 and bycase['main_first_cycle']['metrics']['retired']==1,
            'user_assignments_and_ranges_preserved':user_ok,
            'independent_audit_detects_mutations':all(m['detected'] for m in mutations),
            'nine_entries_and_full_idle_encoding':full_idle and interface_result['metrics'].get('accepted')==9 and interface_result['metrics'].get('retired')==9,
            'idle_may_name_a_ready_lane':partial_idle.get('arvalid')=='0' and partial_idle.get('src')=='01' and partial_idle.get('arready')=='6',
            'rready_masks_only_blocked_lanes':lane_select.get('rready')=='010' and lane_select.get('rvalid')=='2',
            'rvalid_tracks_rready_drop_and_resume':return_before_drop not in (None,'0') and return_after_drop=='0' and return_after_resume not in (None,'0'),
            'coverage_contains_positive_only':cov['status']=='PASS' and len(cov['included'])==len(positive)}
    acceptance=dict(status='PASS' if all(checks.values()) else 'FAIL',id=rid,checks=checks,
        positive=len(positive),negative=len(tests)-len(positive),bank_half_hits=hits,
        user_constrained_valid_samples=len(rows),reset=reset,mutations=mutations,
        formal_proof='Not performed; execution evidence for the current versioned project contracts.')
    eda.write_json(root/'main_phase_acceptance.json',acceptance)
    summary=dict(id=rid,status=acceptance['status'],created_at=eda.stamp(),timezone='Asia/Shanghai',
        project=eda.PROJECT['name'],build=str(build),passed=len(positive),results=results,coverage=cov)
    eda.write_report(root,summary)
    eda.write_json(eda.OUT/'last_command.json',{'id':rid})
    print('MAIN PHASE ACCEPTANCE {}: {} positive / {} expected-negative; {}'.format(acceptance['status'],len(positive),len(tests)-len(positive),root/'main_phase_acceptance.json'))
    return 0 if acceptance['status']=='PASS' else 1
