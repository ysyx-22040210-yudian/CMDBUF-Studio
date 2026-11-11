"""Make-driven real VCS checks. Run only on an isolated development checkout."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root/'simulation/vcs'))
import eda
checks=[]
def check(name,value,detail=None):
    checks.append(dict(name=name,passed=bool(value),detail=detail))
    if not value:raise AssertionError(name+': '+str(detail))
    print('PASS '+name,flush=True)
def command(name,argv):
    with (out/(name+'.log')).open('w') as log:
        code=subprocess.call(argv,cwd=str(root),stdout=log,stderr=subprocess.STDOUT)
    check(name,code==0)
def make(name,*args):command(name,['make']+list(args))
def latest():
    return eda.read_json(eda.OUT/'regressions'/eda.read_json(eda.OUT/'last_command.json')['id']/'summary.json')
def save_batch(label,summary):
    eda.write_json(out/(label+'_summary.json'),summary)
    source=eda.OUT/'regressions'/summary['id'];dest=out/label;dest.mkdir()
    for path in source.rglob('*'):
        if path.is_file() and not any(p.endswith('.vdb') for p in path.parts) and path.suffix in ('.json','.jsonl','.log','.csv','.sv','.txt'):
            target=dest/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(str(path),str(target))

command('unit_linux',[sys.executable,'-m','unittest','discover','-s','simulation/vcs/tests','-v'])
make('check_suite','check-test','JOBS=2')
receipt=eda.read_json(eda.OUT/'last_check_test.json')
acceptance=eda.read_json(Path(receipt['directory'])/'acceptance.json')
eda.write_json(out/'checker_acceptance.json',acceptance)
check('76 checker and fatal checks',acceptance['status']=='PASS' and len(acceptance['checks'])==76)
fixture=Path(receipt['directory'])/'fixture'
fixture_cmd=eda.read_json(fixture/'compile.command.json')
check('fixture compilation queues licenses and uses lca',all(f in fixture_cmd['argv'] for f in ('-lca','-licqueue','+vcs+lic+wait')) and fixture_cmd['timeout_seconds'] is None)
eda.write_json(out/'fixture_compile_command.json',fixture_cmd)
make('tc_suite','tc-test','JOBS=2')
tc_pointer=eda.read_json(eda.OUT/'last_command.json')
tc=eda.read_json(eda.OUT/'regressions'/tc_pointer['id']/'main_phase_acceptance.json')
eda.write_json(out/'tc_acceptance.json',tc)
check('TC startup and input constraints',tc['status']=='PASS')
make('regression','regress','CASES=tc_directed,tc_retry','SEEDS=17,42,123','JOBS=2','WAVES=1','TIMEOUT=1','CHECK_MODE=transaction','ASSERT_FATAL=0')
batch=latest();save_batch('regression',batch)
check('five real cases with one-second execution limit',batch['status']=='PASS' and len(batch['results'])==5)
build=Path(batch['results'][0]['build']);build_receipt=eda.read_json(build/'build.json')
for name in ('build.json','compile.command.json','compile.process.json','execution_start_compile.command.json'):
    shutil.copy2(str(build/name),str(out/name))
check('main compile lca and queue',all(f in build_receipt['argv'] for f in ('-lca','-licqueue','+vcs+lic+wait')))
check('no compile process deadline',eda.read_json(build/'compile.command.json')['timeout_seconds'] is None)
for result in batch['results']:
    directory=Path(result['directory']);log=(directory/'sim.log').read_text(errors='replace')
    check(result['id']+' starts with native callback',log.count('EDA_SIMULATION_STARTED')==1 and log.index('EDA_SIMULATION_STARTED')<log.index('TEST_PASS'))
    check(result['id']+' queue and execution timing', '+vcs+lic+wait' in result['argv'] and result['execution']['timeout_scope']=='execution_only' and result['execution']['execution_timeout_seconds']==1 and result['execution']['startup_wait_seconds'] is not None)
cov=eda.read_json(eda.OUT/'regressions'/batch['id']/'coverage'/'urg.command.json')
check('URG queues with no deadline','+urg+lic+wait' in cov['argv'] and cov['timeout_seconds'] is None and batch['coverage']['status']=='PASS')
make('zero_timeout','run','CASE=tc_directed','SEED=17','WAVES=0','TIMEOUT=0','CHECK_MODE=strict')
zero=latest();save_batch('zero_timeout',zero)
check('zero disables execution deadline',zero['status']=='PASS' and zero['results'][0]['execution']['execution_timeout_seconds'] is None)
make('new_replay','replay','REGRESSION='+batch['id'],'RUN_ID=tc_random_seed_42','TIMEOUT=1')
replay=latest();save_batch('new_replay',replay)
check('new replay identical inputs clock reset events',replay['status']=='PASS' and replay['results'][0]['events_match_original'] and all(replay['results'][0]['exact_replay'].values()))
original=next(r for r in batch['results'] if r['id']=='tc_random_seed_42')
check('new replay identical complete edge snapshots',eda.digest(Path(original['directory'])/'check_frames.csv')==eda.digest(Path(replay['results'][0]['directory'])/'check_frames.csv'))
make('legacy_replay','replay','REGRESSION=20261111_210839_regress_4211bc','RUN_ID=tc_random_seed_42','TIMEOUT=1')
legacy=latest();save_batch('legacy_replay',legacy)
check('legacy exact binary and trace',legacy['status']=='PASS' and legacy['results'][0]['events_match_original'] and all(legacy['results'][0]['exact_replay'].values()))
check('legacy no wall deadline while queueing',legacy['results'][0]['execution']['timeout_scope']=='unlimited' and legacy['results'][0]['execution']['execution_timeout_seconds'] is None)
check('legacy exception clearly logged','LEGACY_LICENSE_WAIT' in (out/'legacy_replay.log').read_text())
eda.write_json(out/'acceptance.json',dict(status='PASS',checks=checks,sources=eda.manifest(),
    scope='Real available-license VCS/URG; delayed startup and cancellation are controlled process tests, not license-server exhaustion.'))
