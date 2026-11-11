"""End-to-end contract acceptance: real VCS positive, negative, interaction runs."""
import concurrent.futures
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import uuid
import eda
import contracts
from protocol_cases import basic,interactions,lane,replace,fixed_mem,traffic


def audit_negative_checks(source):
    from protocol_adapter import verify_execution
    source=Path(source); program=(source/'stimulus.txt').read_text(encoding='utf-8')
    cases=[]
    for name in ('fixed','queued','busy','mem','response','clock'):
        with tempfile.TemporaryDirectory(prefix='protocol_audit_') as temp:
            root=Path(temp)
            for file in ('driven.csv','samples.csv','metrics.json'): shutil.copyfile(str(source/file),str(root/file))
            filename='driven.csv' if name=='fixed' else 'samples.csv'
            with (root/filename).open() as f: rows=list(csv.DictReader(f))
            if name=='fixed': rows[0]['rready']='000'
            elif name=='clock': rows[0]['time_ps']=str(int(rows[0]['time_ps'])+1)
            elif name=='queued': next(r for r in rows if r['arvalid']=='1')['arid']='00000000'
            elif name=='busy': next(r for r in rows if int(r['req'],16)!=0)['rd_mem_status[0]']='00'
            elif name=='mem': next(r for r in rows if int(r['mem_rdata'],2)!=0)['mem_rdata']='0'*128
            elif name=='response': next(r for r in rows if int(r['rvalid'],16)!=0)['rid']='00'
            with (root/filename).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            result=verify_execution({},root,program)  # deliberately no live cfg/compiler
            cases.append(dict(mutation=name,detected=result['status']=='FAIL',failures=result['failures']))
    return cases


def random_audit_negative_checks(source):
    from random_audit import verify_execution
    source=Path(source);program=(source/'stimulus.txt').read_text(encoding='utf-8')
    with (source/'events.csv').open() as f:
        mem_cycle=next(int(r['cycle']) for r in csv.DictReader(f) if r['event']=='MEM')
    checks=[]
    for kind in ('pin_drift','src','arresp','zero_ready','busy','mem','response','context'):
        with tempfile.TemporaryDirectory(prefix='random_audit_') as temp:
            root=Path(temp);tables={}
            for name in ('samples.csv','random_solutions.csv'):
                with (source/name).open() as f:tables[name]=list(csv.DictReader(f))
            samples=tables['samples.csv'];solved=tables['random_solutions.csv']
            i=next(i for i,r in enumerate(samples) if r['arvalid']=='1')
            if kind=='pin_drift':samples[i]['arid']=format(int(samples[i]['arid'],2)^1,'08b')
            if kind=='src':samples[i]['src']=solved[i]['src']='11'
            if kind=='arresp':samples[i]['arresp']=solved[i]['arresp']='01'
            if kind=='zero_ready':samples[i]['arready']=solved[i]['arready']='0'
            if kind=='busy':
                i=next(i for i,r in enumerate(samples) if int(r['req'],16)&int(r['ar_rd_gnt'],2)&int(r['rw_rd_gnt'],2))
                r=samples[i];b=int(r['req'],16).bit_length()-1;half=int(r['arb_addr'],16)&1
                key='rd_mem_status[{}]'.format(b);value=int(r[key],2)&~(1<<half)
                samples[i][key]=solved[i][key]=format(value,'02b')
            if kind=='mem':
                samples[mem_cycle]['mem_rdata']=solved[mem_cycle]['mem_rdata']=format(int(samples[mem_cycle]['mem_rdata'],2)^1,'0128b')
            if kind=='response':
                i=next(i for i,r in enumerate(samples) if int(r['rvalid'],16))
                samples[i]['rid']=format(int(samples[i]['rid'],16)^1,'02x')
            if kind=='context':samples[i]['arready']=format(int(samples[i]['arready'],16)^1,'x')
            for name,rows in tables.items():
                with (root/name).open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
            result=verify_execution(root,program)
            checks.append(dict(mutation=kind,detected=result['status']=='FAIL',failures=result['failures']))
    return checks


def run_suite(args,env):
    inventory=contracts.audit()
    build=eda.build(env)
    rid=eda.stamp()+'_contracts_'+uuid.uuid4().hex[:6]
    root=eda.OUT/'regressions'/rid; inputs=root/'inputs'; inputs.mkdir(parents=True)
    eda.write_json(root/'inventory.json',inventory)
    tests=[]
    def add(name,cfg,rule=None,injection=''):
        path=inputs/(name+'.json'); eda.write_json(path,cfg)
        tests.append((path,rule,injection))
    for bank in range(8):
        for half in range(2):
            c=basic(bank,half)
            add('bank{}_half{}'.format(bank,half),c)
            add('bad_bank{}_half{}'.format(bank,half),c,'rule=E06_SUCCESS_BUSY','busy')
    for name,cfg in interactions().items(): add(name,cfg)
    for name in ('smoke','retry','backpressure','ooo_same_xau','wavedrom_read','wavedrom_reset'):
        add('legacy_'+name,eda.read_json(eda.HERE/'cases'/(name+'.json')))
    random_cfg={'sequence':[{'random':2000}],'require':{'accepted':10,'retired':10,'random_cycles':2000}}
    add('strict_random',random_cfg)
    mixed={'sequence':[{'send':{'src':0,'addr':'0x12000'}},{'random':100},
                       {'send':{'src':1,'addr':'0x12043'}},{'wait':8}],
           'require':{'accepted':2,'retired':2,'random_cycles':100}}
    add('strict_random_mixed',mixed)
    add('random_unsatisfiable',random_cfg,'RND_SOLVE_FAILED','random_unsat')
    add('random_post_solve_change',random_cfg,'RND_POST_SOLVE_MODIFICATION','random_post')
    # Partial grants and fixed ownership remain legal and independently audited.
    c=basic(); c['sim']['drivers']['bank']='fixed'
    for b in range(8):
        replace(c,'bank_busy[{}]'.format(b),lane('rd_mem_status[{}]'.format(b),
                    [0]*4+[1]+[0]*19 if b==0 else [0]*24,True))
    add('fixed_bank_legal',c)
    bad=copy.deepcopy(c); replace(bad,'rd_mem_status[0]',lane('rd_mem_status[0]',[0]*24,True))
    add('fixed_bank_conflict',bad,'rule=E06_SUCCESS_BUSY')
    add('bad_src',basic(),'rule=E03_SRC','src')
    add('bad_arresp',basic(),'rule=E04_ARRESP','arresp')
    for kind in ('mem_early','mem_late','mem_corrupt'):
        add('bad_'+kind,basic(),'rule=T_MEM4_VALUE',kind)
    add('bad_rid',basic(),'SVA_FAIL A_COMPARE_RID','rid')
    c=fixed_mem(); replace(c,'mem_rdata',lane('mem_rdata',[0]*24,True))
    add('fixed_mem_conflict',c,'rule=T_MEM4_VALUE')
    c=basic(); replace(c,'ar_send',lane('ar_send',[0]*24)); c['sim']['wait_cycles']=3
    add('wait_timeout',c,'rule=P_QUEUE_TIMEOUT')
    c=basic(); replace(c,'ar_send',lane('ar_send',[0]*24)); c['sim']['requests']['A']['deadline_cycle']=4
    add('deadline_timeout',c,'rule=P_QUEUE_TIMEOUT')
    c=traffic(); c['sim']['queue_limit']=1
    add('queue_overflow',c,'rule=P_QUEUE_LIMIT')
    c=basic(length=6); add('pending_tail',c,'rule=P_QUEUE_PENDING')
    c=basic(length=6); c['sim']['finish']='allow_pending'; c['require']={'accepted':1,'issued':1}
    add('allowed_prefix',c)
    c=basic(); replace(c,'rready',lane('rready',['x']*2+[7]*22,True)); add('reset_fourstate',c)
    c=basic(); replace(c,'rready',lane('rready',[7]*3+['x']+[7]*20,True)); add('active_fourstate',c,'rule=P_KNOWN')
    # Fixed AR attempts when all ready bits are zero. This must fail, never gate.
    c=traffic(); c['sim']['drivers']['ar']='fixed'; c['sim']['requests']={}
    c['signal']=[s for s in c['signal'] if s['name'] not in ('ar_offer','ar_send')]
    n=72
    c['signal'] += [lane('arvalid',[0]*2+[1]*(n-2)),lane('src',[0]*9+[1,2]+[0]*(n-11),True),
        lane('araddr',[0x12001]*n,True),lane('arid',[42]*n,True),lane('ar_port_id',[6]*n,True),
        lane('arresp',[0]*n,True),lane('mem_error',[0]*n)]
    add('fixed_zero_ready',c,'rule=E05_ZERO_READY')
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures=[pool.submit(eda.run_case,build,root,str(path),1,None,env,args.timeout,inj) for path,_,inj in tests]
        results=[f.result() for f in futures]
    verdicts=[]
    for (path,marker,inj),r in zip(tests,results):
        log=(Path(r['directory'])/'sim.log').read_text(encoding='utf-8',errors='replace')
        ok=(r['status']=='FAIL' and marker in log) if marker else r['status']=='PASS'
        r['expected_failure']=marker; r['acceptance_pass']=ok
        eda.write_json(Path(r['directory'])/'result.json',r)
        verdicts.append(dict(case=r['id'],expected_marker=marker,passed=ok))
    rc,_=eda.execute([sys.executable,'-m','unittest','discover','-s',str(eda.HERE/'tests'),'-v'],
                      eda.ROOT,root/'unit_tests.log',env,120)
    positive=[r for r in results if not r['expected_failure']]
    cov=eda.merge_coverage(root,positive,env)
    hits=[0]*16; rule_hits={}
    for r in positive:
        report=(r.get('drive_audit') or {}).get('coverage',{})
        for key,value in report.items():
            if key=='E06_SUCCESS_BUSY': hits=[a+b for a,b in zip(hits,value)]
            else: rule_hits[key]=rule_hits.get(key,0)+value
    checks={'all_cases_match_expectations':all(v['passed'] for v in verdicts),'unit_tests':rc==0,
            'all_bank_half_antecedents_observed':all(hits),
            'all_other_assumption_antecedents_observed':all(rule_hits.get(k,0)>0 for k in ['E03_SRC','E04_ARRESP','E05_ZERO_READY']),
            'coverage_merged_from_positive_only':cov['status']=='PASS'}
    source=next(r['directory'] for r in positive if r['case']=='protocol_read')
    monitor_checks=audit_negative_checks(source)
    checks['independent_audit_detects_mutations']=all(x['detected'] for x in monitor_checks)
    random_source=next(r['directory'] for r in positive if r['case']=='strict_random')
    random_checks=random_audit_negative_checks(random_source)
    checks['random_audit_detects_mutations']=all(x['detected'] for x in random_checks)
    bycase={r['case']:r for r in results}
    mapping={
        'E03_SRC':['bank0_half0','bank1_half0','bank2_half0','bad_src'],
        'E04_ARRESP':['protocol_read','protocol_error_priority','bad_arresp'],
        'E05_ZERO_READY':['protocol_demo','protocol_selected_reject','fixed_zero_ready'],
        'E06_SUCCESS_BUSY':['bank{}_half{}'.format(b,h) for b in range(8) for h in range(2)]+
                           ['bad_bank{}_half{}'.format(b,h) for b in range(8) for h in range(2)]+['fixed_bank_conflict'],
        'T_MEM4':['protocol_read','protocol_demo','protocol_nclock','protocol_fixed_mem','fixed_mem_conflict','bad_mem_early','bad_mem_late','bad_mem_corrupt'],
        'T_ASSOCIATION':['protocol_same_address','protocol_demo','protocol_error_priority','bad_rid'],
        'T_CLOCK_RESET':['protocol_reset','protocol_duty_cycle','protocol_nclock','protocol_levels'],
        'P_KNOWN':['reset_fourstate','active_fourstate'],
        'P_OWNERSHIP':['protocol_fixed_ar','protocol_fixed_mem','fixed_bank_legal','fixed_bank_conflict','fixed_mem_conflict'],
        'P_QUEUE':['protocol_window','protocol_selected_reject','wait_timeout','deadline_timeout','queue_overflow','pending_tail','allowed_prefix','protocol_reset'],
        'DUT_CHECKS':['legacy_smoke','legacy_retry','legacy_backpressure','legacy_ooo_same_xau','bad_rid']}
    for key in ('E03_SRC','E04_ARRESP','E05_ZERO_READY','E06_SUCCESS_BUSY','T_MEM4','T_ASSOCIATION','P_KNOWN','P_OWNERSHIP','DUT_CHECKS'):
        mapping[key].extend(['strict_random','strict_random_mixed'])
    mapping['P_OWNERSHIP'].extend(['random_unsatisfiable','random_post_solve_change'])
    per_rule={key:{'status':'PASS' if all(bycase[n]['acceptance_pass'] for n in names) else 'FAIL',
                   'cases':[bycase[n]['id'] for n in names]} for key,names in mapping.items()}
    checks['every_rule_has_execution_evidence']=set(per_rule)==contracts.IMPLEMENTED and all(r['status']=='PASS' for r in per_rule.values())
    ok=all(checks.values())
    acceptance={'status':'PASS' if ok else 'FAIL','id':rid,'checks':checks,'verdicts':verdicts,
                'positive':len(positive),'negative':len(tests)-len(positive),'bank_half_hits':hits,
                'rule_hits':rule_hits,'inventory':inventory,'per_rule':per_rule,'independent_audit_negative_checks':monitor_checks,
                'random_audit_negative_checks':random_checks,
                'formal_proof':'Not performed. This receipt covers enumerated execution tests, not all possible traces.'}
    eda.write_json(root/'constraint_acceptance.json',acceptance)
    summary={'id':rid,'status':acceptance['status'],'created_at':eda.stamp(),'timezone':'Asia/Shanghai',
             'project':eda.PROJECT['name'],'build':str(build),'passed':sum(r['status']=='PASS' for r in results),
             'results':results,'coverage':cov,'constraint_acceptance':str(root/'constraint_acceptance.json')}
    eda.write_report(root,summary)
    eda.write_json(eda.OUT/'last_command.json',{'id':rid})
    if os.environ.get('EDA_REQUEST_ID'):
        eda.write_json(eda.OUT/'requests'/(os.environ['EDA_REQUEST_ID']+'.json'),{'id':rid})
    print('CONSTRAINT ACCEPTANCE {}: {} positive / {} expected-negative; {}'.format(acceptance['status'],len(positive),len(tests)-len(positive),root/'constraint_acceptance.json'))
    for v in verdicts:
        if not v['passed']: print('UNEXPECTED '+v['case'])
    return 0 if ok else 1
