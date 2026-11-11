"""Run inside an isolated extracted Linux package with a real X display and EDA tools.
Uses the actual Tk application callbacks; screenshots are X server pixels.
"""
import collections
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from PIL import ImageGrab

project=Path(sys.argv[1]).resolve(); evidence=Path(sys.argv[2]).resolve();evidence.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(project/'simulation/vcs/gui'))
import desktop
from desktop import tk,Studio,model,eda,backend,formal_model
from widgets import WaveWindow

checks=[];jobs=[];screens=[];heartbeat=[0];heartbeat_timer=[None]
def check(name,value,detail=None):
    checks.append(dict(name=name,pass_=bool(value),detail=detail))
    if not value: raise AssertionError(name+': '+str(detail))
    print('PASS '+name,flush=True)

root=tk.Tk();app=Studio(root);root.geometry('1520x1000+20+20');root.update()
def fail_dialog(text):raise RuntimeError(text)
app.error=fail_dialog
def tick():
    heartbeat[0]+=1;heartbeat_timer[0]=root.after(25,tick)
tick()
def spin(seconds=.3):
    end=time.time()+seconds
    while time.time()<end:root.update();time.sleep(.01)
def capture(name):
    root.lift();spin(.2)
    output=evidence/(name+'.png')
    x,y=root.winfo_rootx(),root.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),xdisplay=os.environ['DISPLAY']).save(str(output))
    screens.append(output.name)
def finish(timeout=240):
    before=heartbeat[0];end=time.time()+timeout
    while time.time()<end:
        root.update();job=app.jobs.snapshot()
        if job and job['state']!='RUNNING':
            spin(.35);jobs.append(app.jobs.snapshot());check('job '+job['kind'],job['state']=='DONE',job.get('error'))
            return job
        time.sleep(.025)
    raise RuntimeError('Job timeout: '+str(app.jobs.snapshot()))
def launch(kind,payload=None):app.launch(kind,payload);return finish()

try:
    check('native Tk running',root.tk.call('info','patchlevel').startswith('8.'),root.tk.call('info','patchlevel'))
    for page in app.pages:
        app.show(page);spin(.1)
        check('page '+page+' visible',app.pages[page].winfo_viewable())
    if app.jobs.snapshot()['state']=='RUNNING':finish()
    else:jobs.append(app.jobs.snapshot())
    check('automatic profile without configuration',model.state_read()['profile']=='auto' and not eda.platform_config.CONFIG.exists())
    check('automatic tool locations detected',all(any(row['name']==name and row['status']=='已发现' for row in app.jobs.snapshot()['detected']['rows']) for name in ('vcs','verdi','urg')))
    check('no tool setup path or homes needed',not any(os.environ.get(k) for k in ('EDA_TOOL_SETUP','VCS_HOME','VERDI_HOME')))
    app.show('home');capture('01_home')
    app.show('dut');original=model.dut_config.load()
    imported=launch('import_rtl',dict(paths=[str(project/p) for p in original['files']]))['imported']
    app.dut_name.set('Tk acceptance CMDBUF');app.save_dut()
    check('DUT imported and saved',model.dut_config.load()['files']==imported['files'])
    capture('02_dut')
    template=model.case('tc_user_example')
    new=model.save_case(dict(name='tc_tk_acceptance',phase=template['phase'],create=True,template='tc_user_example'))
    app.refresh_cases(new['name']);app.show('cases');spin()
    before=model.tc_path(new['name']).read_text(encoding='utf-8');a,b=model.phase_span(before)
    changed=new['phase'].replace("arid = 8'h2a", "arid = 8'h35")
    app.case_editor.set(changed);app.case_changed();app.save_case()
    after=model.tc_path(new['name']).read_text(encoding='utf-8');c,d=model.phase_span(after)
    check('only main_phase changed',before[:a]==after[:c] and before[b:]==after[d:] and "arid = 8'h35" in after)
    check('editor only main_phase',app.case_editor.get().strip().endswith('endtask') and 'endclass' not in app.case_editor.get())
    capture('03_main_phase')
    app.reconcile_plan()
    for row in app.plan_rows:
        row.update(enabled=row['case'] in ('tc_directed','tc_random','tc_tk_acceptance'),runs=1,seed=31,cycles=64)
        if row['case']=='tc_directed':row.update(runs=2,seed=17)
        if row['case']=='tc_random':row.update(runs=3,seed=101,cycles=256)
    app.jobs_var.set('2');app.timeout_var.set('120');app.waves_var.set(True);app.draw_plan();spin();app.show('regression');capture('04_regression')
    start_heart=heartbeat[0];app.run_regression();run=finish(300)
    check('Tk event loop alive during EDA',heartbeat[0]-start_heart>10,heartbeat[0]-start_heart)
    batch=run['batch'];summary=eda.read_json(backend.batch_path(batch)/'summary.json')
    check('six regression runs pass',summary['status']=='PASS' and len(summary['results'])==6)
    seeds=collections.defaultdict(list)
    for r in summary['results']:seeds[r['case']].append(r['seed'])
    check('per-case repeat counts and seeds',sorted(seeds['tc_directed'])==[17,18] and sorted(seeds['tc_random'])==[101,102,103] and seeds['tc_tk_acceptance']==[31],dict(seeds))
    check('all FSDB VCD exist',all((backend.result_path(batch,r['id'])/'waves.fsdb').is_file() and (backend.result_path(batch,r['id'])/'waves.vcd').is_file() for r in summary['results']))
    check('coverage merged',summary['coverage']['status']=='PASS',summary['coverage'].get('metrics_percent'))
    app.show('results');capture('05_results')
    check('results show body cycle counts',all(app.run_tree.item(r['id'],'values')[2] in ('64','256') for r in summary['results']))
    first=summary['results'][0]['id'];app.run_tree.selection_set(first);spin();app.open_run_wave();wave_job=finish()
    wave=next(w for w in root.winfo_children() if isinstance(w,WaveWindow));wave.start=20000;wave.end=160000;wave.cursor=65000;wave.cursor_b=85000;wave.draw();wave.lift();spin()
    x,y=wave.winfo_rootx(),wave.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+wave.winfo_width(),y+wave.winfo_height()),xdisplay=os.environ['DISPLAY']).save(str(evidence/'06_waveform.png'));screens.append('06_waveform.png')
    check('native waveform has traces',len(wave.canvas.find_all())>50 and len(wave.ids)>0,len(wave.canvas.find_all()));wave.destroy()
    app.open_verdi();finish();app.open_coverage();finish()
    windows=subprocess.check_output(['xwininfo','-root','-tree'],env=os.environ).decode('utf-8',errors='replace')
    (evidence/'x_windows.txt').write_text(windows,encoding='utf-8')
    check('Verdi windows created','Verdi' in windows or 'verdi' in windows)
    fsdb=backend.result_path(batch,first)/'waves.fsdb'
    imported_fsdb=launch('import_fsdb',dict(paths=[str(fsdb)]))['imported'][0]
    check('import preserves original FSDB',imported_fsdb['sha256']==formal_model.file_hash(fsdb))
    random_run=next(r for r in summary['results'] if r['case']=='tc_random')['id']
    app.add_formal_source(dict(kind='regression',batch=batch,run=random_run,name=random_run))
    app.formal_items[0]['pattern']=7;app.formal_items[1]['pattern']=42;app.draw_formal();spin();app.show('formal')
    app.inspect_fsdb();inspect=finish()
    info=eda.read_json(formal_model.artifact(inspect['formal_id'],'inspection.json'))
    check('NPI hierarchy inspection',len(info['signals'])>0 and len(info['scopes'])>0)
    for window in root.winfo_children():
        if isinstance(window,tk.Toplevel):window.destroy()
    app.generate_formal();formal_job=finish()
    generated=eda.read_json(formal_model.artifact(formal_job['formal_id'],'summary.json'))
    check('two FSDB pattern IDs',sorted(p['pattern'] for p in generated['patterns'])==[7,42],generated['counts'])
    properties=formal_model.artifact(formal_job['formal_id'],'trace_properties.sv').read_text(encoding='utf-8')
    check('generated assume assert and pattern guards','assume property' in properties and 'assert property' in properties and 'pattern ==' in properties)
    app.show('formal');capture('07_formal')
    # Save dialog uses the same native callback with a deterministic test destination.
    desktop.filedialog.asksaveasfilename=lambda **kw:str(evidence/'formal_package.zip')
    app.save_export();check('formal ZIP saved',formal_model.file_hash(evidence/'formal_package.zip')==formal_model.file_hash(formal_model.artifact(formal_job['formal_id'],'package.zip')))
    for row in app.plan_rows:row.update(enabled=row['case']=='tc_directed',runs=1,seed=99,cycles=64)
    app.waves_var.set(False);app.draw_plan();spin();app.run_regression();off=finish()
    off_summary=eda.read_json(backend.batch_path(off['batch'])/'summary.json')
    check('no-wave regression passes',off_summary['status']=='PASS')
    check('no-wave disables files but retains coverage',not list(backend.batch_path(off['batch']).rglob('*.fsdb')) and not list(backend.batch_path(off['batch']).rglob('waves.vcd')) and off_summary['coverage']['status']=='PASS')
    check('all EDA launches use local Make',all(c['argv'][0]=='make' for j in jobs for c in j['commands']))
    check('no remote settings in application',model.state_read()['mode']=='local' and 'host' not in eda.platform_config.load())
    app.show('settings');capture('08_settings')
    root.geometry('1280x700+20+20');spin();shell=app.page_shells['settings'];shell.canvas.yview_moveto(1);spin()
    check('short display can scroll to final settings',shell.canvas.yview()[1]>.99 and shell.canvas.yview()[0]>0,shell.canvas.yview())
    capture('09_short_display')
    root.after_cancel(heartbeat_timer[0]);app.close()
    root=tk.Tk();app=Studio(root);spin();check('reopen persisted plan',app.waves_var.get() is False)
    if app.jobs.snapshot() and app.jobs.snapshot()['state']=='RUNNING':finish()
    check('reopen saved Formal selection',sorted(s['pattern'] for s in app.formal_items)==[7,42]);app.close()
    receipt=dict(status='PASS',checks=checks,screenshots=screens,regression=batch,no_wave_regression=off['batch'],formal_id=formal_job['formal_id'],counts=generated['counts'],python=sys.version,tk=tk.TkVersion,
                 runtime_sha256={str(p.relative_to(project)):eda.digest(p) for p in (project/'simulation/vcs').rglob('*.py') if 'tests' not in p.parts},
                 project=str(project),scope='Native Tk callbacks, local Make, actual VCS/Verdi/NPI. Generated Formal proof semantics covered by earlier sealed evidence; this run verifies generation and integration, not a new exhaustive proof.')
    eda.write_json(evidence/'acceptance.json',receipt)
except Exception:
    import traceback
    (evidence/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
    eda.write_json(evidence/'acceptance.json',dict(status='FAIL',checks=checks))
    raise
finally:
    eda.write_json(evidence/'jobs.json',jobs)
