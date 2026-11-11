"""Exercise the real Tk callbacks and Make jobs on an isolated Linux checkout."""
import copy
import json
import os
from pathlib import Path
import sys
import time
from PIL import ImageGrab

project=Path(sys.argv[1]).resolve(); evidence=Path(sys.argv[2]).resolve();evidence.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(project/'simulation/vcs/gui'))
from desktop import tk, Studio, eda, backend, model

checks=[]; jobs=[]
def check(name,value,detail=None):
    checks.append(dict(name=name,passed=bool(value),detail=detail))
    if not value: raise AssertionError(name+': '+str(detail))
    print('PASS '+name,flush=True)

root=tk.Tk(); app=Studio(root); root.geometry('1520x1000+10+10'); root.update()
def error(text): raise RuntimeError(text)
app.error=error
def spin(seconds=.2):
    end=time.time()+seconds
    while time.time()<end: root.update();time.sleep(.01)
def finish():
    end=time.time()+300
    while time.time()<end:
        root.update(); j=app.jobs.snapshot()
        if j and j['state']!='RUNNING':
            spin();jobs.append(j);check('job '+j['kind'],j['state']=='DONE',j.get('error'));return j
        time.sleep(.03)
    raise RuntimeError('GUI job timeout')
def capture(name):
    root.lift();spin()
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),xdisplay=os.environ['DISPLAY']).save(str(evidence/(name+'.png')))

try:
    spin(.3);finish()
    check('new GUI plan defaults transaction',app.check_modes[app.check_mode_var.get()]=='transaction')
    old=copy.deepcopy(app.plan_value());old.pop('check_mode');old.pop('assert_fatal')
    app.settings['plan']=old;app.load_plan()
    check('old plan keeps strict and fatal',app.check_modes[app.check_mode_var.get()]=='strict' and app.assert_fatal_var.get())
    app.check_mode_var.set('通用事务检查');app.assert_fatal_var.set(False);app.update_check_note()
    for row in app.plan_rows:
        row.update(enabled=row['case'] in ('tc_directed','tc_random','tc_reset'),runs=3 if row['case']=='tc_random' else 1,
                   seed=41,cycles=400 if row['case']=='tc_random' else 128)
    app.plan_name.set('通用事务与非 fatal 验收');app.jobs_var.set('2');app.waves_var.set(True)
    app.draw_plan();spin();app.show('regression');app.save_plan()
    saved=model.state_read()['plan'];check('mode and fatal persist',saved['check_mode']=='transaction' and saved['assert_fatal'] is False)
    capture('01_regression_transaction')
    app.run_regression();job=finish();batch=job['batch']
    summary=eda.read_json(backend.batch_path(batch)/'summary.json')
    check('five GUI regression cases pass',summary['status']=='PASS' and len(summary['results'])==5,summary['status'])
    check('Make job contains policy','CHECK_MODE=transaction' in job['log'] and 'ASSERT_FATAL=0' in job['log'])
    check('every simulator receives policy',all('+CHECK_MODE=transaction' in r['argv'] and '+ASSERT_FATAL=0' in r['argv'] for r in summary['results']))
    check('scope separates unchecked timing',all([g['status'] for g in r['checks']['groups']]==['PASS','PASS','NOT_CHECKED'] for r in summary['results']))
    check('waves and coverage retained',summary['coverage']['status']=='PASS' and all((Path(r['directory'])/'waves.fsdb').exists() for r in summary['results']))
    app.show('results');app.artifact_var.set('check_scope.json');app.read_artifact();capture('02_results_scope')
    check('GUI shows unchecked timing','完整接口时序：未检查' in app.scope_note.get())
    app.open_coverage();finish();check('coverage uses Make','coverage' in jobs[-1]['log'])
    app.show('regression');app.check_mode_var.set('完整时序检查 20261021');app.assert_fatal_var.set(True);app.update_check_note()
    for row in app.plan_rows:row.update(enabled=row['case']=='tc_directed',runs=1,seed=88,cycles=64)
    app.waves_var.set(False);app.draw_plan();spin();app.save_plan();capture('03_regression_strict')
    app.run_regression();strict_job=finish()
    strict=eda.read_json(backend.batch_path(strict_job['batch'])/'summary.json')
    check('strict GUI mode checks all groups',strict['status']=='PASS' and all(g['status']=='PASS' for g in strict['results'][0]['checks']['groups']))
    check('strict fatal propagated',strict['check_policy']==dict(check_mode='strict',assert_fatal=True))
    check('wave off retains coverage',strict['coverage']['status']=='PASS' and not (Path(strict['results'][0]['directory'])/'waves.fsdb').exists())
    # Display one actual injected failure, preserving its original logs/results.
    receipt=eda.read_json(eda.OUT/'last_check_test.json')
    acceptance=eda.read_json(Path(receipt['directory'])/'acceptance.json')
    bad=next(r for r in acceptance['results'] if r['check_mode']=='strict' and not r['assert_fatal'] and r['injection']=='pending_registered')
    failed_id='check_modes_failure_'+eda.stamp();failed=eda.OUT/'regressions'/failed_id
    (failed/'cases').mkdir(parents=True);(failed/'cases'/bad['id']).symlink_to(Path(bad['directory']),target_is_directory=True)
    actual=dict(id=failed_id,status='FAIL',results=[bad],passed=0,coverage=dict(status='NO_PASSING_CASES'),
                check_policy=dict(check_mode='strict',assert_fatal=False),created_at=eda.stamp(),waves_enabled=False)
    eda.write_report(failed,actual)
    app.refresh_results(failed_id);app.show('results');app.artifact_var.set('assertions.jsonl');app.read_artifact();capture('04_nonfatal_failure')
    check('nonfatal errors visible as failure','完整接口时序：失败' in app.scope_note.get() and '$fatal 关' in app.scope_note.get())
    check('nonfatal run finished but failed',bad['checks']['runtime']['finished']==1 and bad['status']=='FAIL')
    eda.write_json(evidence/'acceptance.json',dict(status='PASS',checks=checks,jobs=jobs,batches=[batch,strict_job['batch'],failed_id]))
finally:
    root.destroy()
