"""Run Make acceptance and verify real FSDB-derived clock/reset transitions."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT=Path(sys.argv[1]).resolve()
OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(ROOT/'simulation/vcs'))
import eda
from gui import vcd

checks=[];batches=[]

def check(name,ok,detail=None):
    checks.append(dict(name=name,passed=bool(ok),detail=detail))
    print(('PASS ' if ok else 'FAIL ')+name,flush=True)
    if not ok:raise AssertionError(name+': '+str(detail))

def make(name,*args,**kwargs):
    with (OUT/(name+'.log')).open('w',encoding='utf-8') as log:
        rc=subprocess.call(['make']+list(args),cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT)
    check('make '+name,rc!=0 if kwargs.get('expect_fail') else rc==0,rc)

def latest():
    value=eda.read_json(eda.OUT/'regressions'/eda.read_json(eda.OUT/'last_command.json')['id']/'summary.json')
    batches.append(value)
    return value

make('tc_suite','tc-test','JOBS=2')
tc=latest();tc_receipt=eda.read_json(eda.OUT/'regressions'/tc['id']/'main_phase_acceptance.json')
eda.write_json(OUT/'tc_acceptance.json',tc_receipt)
check('procedural positive and negative acceptance',tc_receipt['status']=='PASS' and tc_receipt['positive']==11 and tc_receipt['negative']==13)
first=next(r for r in tc['results'] if r['case']=='main_first_cycle')
first_dir=Path(first['directory'])
with (first_dir/'assigned.csv').open() as f: assignments=list(csv.DictReader(f))
with (first_dir/'samples.csv').open() as f: samples=list(csv.DictReader(f))
check('cycle zero drives at 12ns and is accepted at 20ns',
      assignments[0]['time_ps']=='12000' and samples[0]['time_ps']=='20000' and samples[0]['arvalid']=='1' and first['metrics']['accepted']==1)

make('checker_suite','check-test','JOBS=2')
receipt=eda.read_json(Path(eda.read_json(eda.OUT/'last_check_test.json')['directory'])/'acceptance.json')
check('all snapshot scope and fatal checks pass',receipt['status']=='PASS' and len(receipt['checks'])==76)
eda.write_json(OUT/'checker_acceptance.json',receipt)

make('generic_regression','regress','CHECK_MODE=transaction','ASSERT_FATAL=0','WAVES=1','SEEDS=1,42,101','CYCLES=500','JOBS=2')
generic=latest()
check('nine generic regressions pass',generic['status']=='PASS' and len(generic['results'])==9)
check('every procedural run audits fixed startup',all(r['drive_audit'].get('startup_policy')=='clock_high_reset_1ns' for r in generic['results']))

# Compare actual FSDB-derived values with the independently recorded pin trace.
wave_checks=[]
for result in generic['results']+[first]:
    directory=Path(result['directory'])
    database=vcd.index(directory/'waves.vcd',OUT/'vcd_cache')
    names=['clk','rst_n','arvalid','arready']
    with sqlite3.connect(str(database)) as db:
        signals={}
        for name in names:
            full_name='tb_cmdbuf_rd.'+name
            row=db.execute('SELECT code FROM signals WHERE name=? OR name LIKE ?',
                           (full_name,full_name+'[%')).fetchone()
            assert row is not None,name
            signals[name]=list(db.execute('SELECT t,v FROM changes WHERE code=? ORDER BY t,rowid',(row[0],)))
    check(result['id']+' FSDB initial levels',signals['clk'][:3]==[(0,'1'),(5000,'0'),(10000,'1')] and signals['rst_n']==[(0,'0'),(1000,'1')])
    with (directory/'clock_reset.csv').open() as f: transitions=list(csv.DictReader(f))
    for name in ('clk','rst_n'):
        changes=[]
        for row in transitions:
            if not changes or row[name]!=changes[-1][1]:changes.append((int(row['time_ps']),row[name]))
        check(result['id']+' '+name+' FSDB matches pin trace',changes==signals[name])
    item=dict(run=result['id'],fsdb_sha256=eda.digest(directory/'waves.fsdb'),vcd_sha256=eda.digest(directory/'waves.vcd'),
              timescale='1 ps',signals={n:[list(x) for x in values if x[0]<=40000] for n,values in signals.items()})
    wave_checks.append(item)
eda.write_json(OUT/'waveform_evidence.json',wave_checks)

make('reject_reset_without_fatal','run','CASE=simulation/vcs/tests/main_phase/main_bad_reset.sv',
     'CHECK_MODE=transaction','ASSERT_FATAL=0','WAVES=0',expect_fail=True)
bad=latest()['results'][0]
check('illegal reset remains a driver error with fatal disabled',bad['status']=='FAIL' and
      'MAIN_RESET_FIXED_HIGH_AFTER_1NS' in (Path(bad['directory'])/'sim.log').read_text(encoding='utf-8'))

make('new_replay','replay','REGRESSION='+generic['id'],'RUN_ID=tc_random_seed_42')
replay=latest()
check('new replay retains exact startup trace',replay['status']=='PASS' and replay['results'][0]['exact_replay']['clock_reset.csv'])
make('legacy_replay','replay','REGRESSION=20261111_210839_regress_4211bc','RUN_ID=tc_random_seed_42')
old=latest()
check('legacy simulator replay keeps archived timing',old['status']=='PASS' and
      old['results'][0]['drive_audit']['startup_policy']=='legacy_archived_tc' and all(old['results'][0]['exact_replay'].values()))

export_id='startup_'+eda.stamp()
request=dict(id=export_id,max_edges=10000,sources=[dict(kind='file',path=str(first_dir/'waves.fsdb'),
    pattern=7,name='first_cycle_after_1ns_reset',scope='tb_cmdbuf_rd.dut')])
eda.write_json(OUT/'npi_request.json',request)
make('npi_export','fsdb-formal','REQUEST='+str(OUT/'npi_request.json'))
formal=eda.OUT/'fsdb_formal'/export_id
trace=eda.read_json(formal/'trace.json')[0]
segment=trace['segments'][0]
check('NPI recognizes the 1ns reset pulse and first working edge',len(trace['segments'])==1 and
      segment['reset_asserted_at']==0 and segment['reset_released_at']==1000 and segment['rows'][0]['time']==10000)
for name in ('summary.json','trace.json','trace_properties.sv','samples.csv','pattern_7.ndjson'):
    dest=OUT/'npi'/name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(str(formal/name),str(dest))

with (OUT/'unit_linux.log').open('w',encoding='utf-8') as log:
    rc=subprocess.call([sys.executable,'-m','unittest','discover','-s','simulation/vcs/tests'],cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT)
check('Python unit tests',rc==0)

keep=('result.json','metrics.json','sim.log','case.sv','tc_config.json','clock_reset.csv','assigned.csv','samples.csv',
      'events.csv','check_runtime.json','check_scope.json','assertions.jsonl','check_frames.csv','drive_audit.json')
for batch in batches:
    dest=OUT/'runs'/batch['id'];dest.mkdir(parents=True)
    eda.write_json(dest/'summary.json',batch)
    for result in batch['results']:
        src=Path(result['directory']);target=dest/result['id'];target.mkdir()
        for name in keep:
            if (src/name).is_file():shutil.copyfile(str(src/name),str(target/name))
eda.write_json(OUT/'acceptance.json',dict(status='PASS',checks=checks,source_sha256=eda.manifest(),
    batches=[b['id'] for b in batches],checker_directory=receipt['directory'],npi_export=export_id,
    note='Real VCS/Verdi/NPI execution. Generated formal code was not proved in this acceptance.'))
print('STARTUP ACCEPTANCE PASS: %d checks'%len(checks),flush=True)
