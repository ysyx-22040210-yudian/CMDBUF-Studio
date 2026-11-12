import os, sys, json, time, subprocess
from pathlib import Path

project=Path('/root/cmdbuf-environment-fatal-20261112')
base=project/'gui_acceptance';base.mkdir(exist_ok=True)
os.environ['EDA_TOOL_SETUP']='/root/.config/cmdbuf-studio/toolchain.sh'
sys.path.insert(0,str(project/'simulation/vcs'))
import platform_config
env=platform_config.tool_env()
env['PATH']='/usr/local/bin:/usr/bin:/bin:'+env['PATH']
for p in Path('/proc').glob('[0-9]*'):
    try:
        if b'gui/desktop.py' not in (p/'cmdline').read_bytes(): continue
        values=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
        if b'DISPLAY' in values and b'XAUTHORITY' in values:
            env.update({k:values[k.encode()].decode() for k in ('DISPLAY','XAUTHORITY')});break
    except OSError: pass
os.environ.update(env)
source=(project/'simulation/vcs/tests/main_phase/main_env_multiple.sv').read_text(encoding='utf-8')
(project/'simulation/vcs/tc/tc_env_gui.sv').write_text(source.replace('class main_env_multiple','class tc_env_gui'),encoding='utf-8')
sys.path.insert(0,str(project/'simulation/vcs/gui'))
import desktop,model,backend,formal_model
import tkinter as tk
from PIL import ImageGrab
for module in (model,backend,formal_model,desktop):module.STATE=base/'state'
formal_model.IMPORTS=base/'state/fsdb_imports';formal_model.EXPORTS=base/'state/formal_exports'
errors=[]
root=tk.Tk();app=desktop.Studio(root);root.after_cancel(app.detect_timer);app.error=errors.append
def find(widget,title):
    for child in widget.winfo_children():
        if child.winfo_class() in ('TButton','TCheckbutton') and str(child.cget('text'))==title:return child
        found=find(child,title)
        if found is not None:return found
def capture(name):
    root.deiconify();root.lift();root.update();time.sleep(.3)
    box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
    ImageGrab.grab(xdisplay=env['DISPLAY']).crop(box).save(str(base/name))
def wait_job():
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        root.update();job=app.jobs.snapshot()
        if job and job['state']!='RUNNING' and app.last_finished==job['id']:
            root.update();return job
        time.sleep(.05)
    raise RuntimeError('GUI test still running; not cancelled')
app.plan_rows=[dict(case='tc_env_gui',enabled=True,runs=2,seed=901,cycles=16),
               dict(case='tc_directed',enabled=True,runs=1,seed=902,cycles=64)]
app.draw_plan();root.update();app.plan_name.set('环境 fatal 继续运行验收');app.timeout_var.set('120')
app.jobs_var.set('2');app.waves_var.set(True);app.show('regression');root.update()
app.env_fatal_var.set(True);app.assert_fatal_var.set(True);app.update_check_note()
master=find(app.pages['regression'],'环境错误立即停止（$fatal）')
assert master is not None
master.invoke();root.update()
assert not app.env_fatal_var.get() and app.assert_fatal_var.get() and app.assert_fatal_check.instate(['disabled'])
find(app.pages['regression'],'保存计划').invoke();assert not errors,errors
saved=model.state_read()['plan'];assert saved['env_fatal'] is False and saved['assert_fatal'] is True
master.invoke();assert app.env_fatal_var.get() and not app.assert_fatal_check.instate(['disabled']) and app.assert_fatal_var.get()
app.settings=model.state_read();app.load_plan();root.update()
assert not app.env_fatal_var.get() and app.assert_fatal_check.instate(['disabled'])
capture('gui_continue_plan.png')
find(app.pages['regression'],'开始回归').invoke()
job=wait_job();assert not errors,errors
(base/'gui_regression.log').write_text(job['log'],encoding='utf-8')
assert job['state']=='ERROR' and job['verdict']=='FAIL',job
assert 'ENV_FATAL=0' in job['log'] and 'ASSERT_FATAL=1' in job['log']
batch=backend.batch_path(job['batch']);summary=json.loads((batch/'summary.json').read_text(encoding='utf-8'))
assert len(summary['results'])==3 and all(r['completed'] for r in summary['results'])
negative=[r for r in summary['results'] if r['case']=='tc_env_gui']
assert {r['seed'] for r in negative}=={901,902}
assert all(r['status']=='FAIL' and r['metrics']['tc_body_cycles']==16 and len(r['checks']['environment']['failures'])==6 for r in negative)
assert summary['coverage']['status']=='PASS' and len(summary['coverage']['included'])==1 and len(summary['coverage']['excluded'])==2
assert all(len(r['wave_artifacts'])==2 for r in summary['results'])
if app.job_window and app.job_window.winfo_exists():app.job_window.destroy()
app.refresh_results(job['batch']);root.update();app.run_tree.selection_set(negative[0]['id']);root.update();app.artifact_var.set('environment_errors.jsonl');app.read_artifact()
app.show('results');root.update()
assert '环境错误 6 笔' in app.scope_note.get() and '执行完成' in app.scope_note.get(),app.scope_note.get()
capture('gui_completed_failure.png')
# Open the completed failing run's real waveform in the native canvas.
find(app.pages['results'],'内置波形').invoke();wavejob=wait_job()
assert wavejob['state']=='DONE',wavejob
(base/'gui_wave.log').write_text(wavejob['log'],encoding='utf-8')
result=dict(status='PASS',gui='native Tk on Linux Python 3.6.8',batch=str(batch),
            saved_policy=saved,make_arguments=job['commands'],total=3,completed=3,
            failed_cases=[dict(case=r['id'],cycles=r['metrics']['tc_body_cycles'],environment_errors=len(r['checks']['environment']['failures']),assertions=len(r['checks']['failures'])) for r in negative],
            coverage=summary['coverage'],scope_note=app.scope_note.get(),native_wave=wavejob['state'])
(base/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True,indent=2),flush=True)
root.after_cancel(app.poll_id);root.destroy()
