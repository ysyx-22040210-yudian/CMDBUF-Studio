"""Validate the delivered archive on Linux, including a real make gui window."""
import hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root/'simulation/vcs'))
import eda
checks=[]
def check(name,passed,detail=None):
    checks.append(dict(name=name,passed=bool(passed),detail=detail))
    if not passed:raise AssertionError(name+': '+str(detail))
    print('PASS '+name,flush=True)
def make(name,*args):
    with (out/(name+'.log')).open('w') as log:
        code=subprocess.call(['make']+list(args),cwd=str(root),stdout=log,stderr=subprocess.STDOUT)
    check('make '+name,code==0)
def latest():return eda.read_json(eda.OUT/'regressions'/eda.read_json(eda.OUT/'last_command.json')['id']/'summary.json')

subprocess.check_call([sys.executable,'simulation/vcs/portable.py','--verify'],cwd=str(root))
check('unicode and space project path',' ' in str(root) and any(ord(c)>127 for c in str(root)),str(root))
manifest=eda.read_json(root/'PORTABLE_MANIFEST.json')
check('new monitor profile and manuals included',all(rel in manifest['files'] for rel in (
    'simulation/vcs/tb/cmdbuf_check_monitor.sv','simulation/vcs/check_profiles.json',
    'documents/37_cmdbuf_check_modes_20261111.docx','documents/37_cmdbuf_check_modes_20261111.pdf')))
check('no device configuration or cached results',not (root/'eda.local.json').exists() and
      not any(rel.startswith(('build/','verification/')) or rel.endswith('.local.json') for rel in manifest['files']))
make('doctor','doctor')
make('smoke','smoke','JOBS=2','WAVES=1','CHECK_MODE=transaction','ASSERT_FATAL=0')
generic=latest();eda.write_json(out/'generic_summary.json',generic)
check('fresh package three cases pass',generic['status']=='PASS' and len(generic['results'])==3)
check('generic scope never claims full timing',all([g['status'] for g in r['checks']['groups']]==['PASS','PASS','NOT_CHECKED'] for r in generic['results']))
make('strict','run','CASE=tc_directed','WAVES=0','CHECK_MODE=strict','ASSERT_FATAL=1')
strict=latest();eda.write_json(out/'strict_summary.json',strict)
check('fresh package strict passes',strict['status']=='PASS' and all(g['status']=='PASS' for g in strict['results'][0]['checks']['groups']))
make('replay','replay','REGRESSION='+generic['id'],'RUN_ID=tc_random_seed_42','CHECK_MODE=strict','ASSERT_FATAL=1')
replay=latest();eda.write_json(out/'replay_summary.json',replay)
check('replay preserves archived scope and fatal',replay['check_policy']==dict(check_mode='transaction',assert_fatal=False))
check('replay exact events',replay['status']=='PASS' and replay['results'][0]['events_match_original'])
original=next(r for r in generic['results'] if r['id']=='tc_random_seed_42')
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
check('replay exact complete snapshots',digest(Path(original['directory'])/'check_frames.csv')==digest(Path(replay['results'][0]['directory'])/'check_frames.csv'))
with (out/'gui.log').open('w') as log:
    process=subprocess.Popen(['make','gui'],cwd=str(root),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        found=False;end=time.time()+20
        while time.time()<end and process.poll() is None:
            windows=subprocess.check_output(['xwininfo','-root','-tree']).decode('utf-8',errors='replace')
            if 'CMDBUF Studio' in windows:found=True;break
            time.sleep(.2)
        check('make gui creates native window',found)
    finally:
        if process.poll() is None:os.killpg(process.pid,signal.SIGTERM)
        process.wait()
eda.write_json(out/'acceptance.json',dict(status='PASS',checks=checks,manifest_sha256=digest(root/'PORTABLE_MANIFEST.json')))
