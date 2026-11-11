"""Display the actual first-cycle FSDB conversion in the native waveform window."""
import json
import os
from pathlib import Path
import sys
import time
from PIL import ImageGrab

project=Path(sys.argv[1]);evidence=Path(sys.argv[2]);evidence.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(project/'simulation/vcs/gui'))
from desktop import tk,Studio,eda,WaveWindow
import vcd
receipt=eda.read_json(Path(sys.argv[3])/'tc_acceptance.json')
summary=eda.read_json(eda.OUT/'regressions'/receipt['id']/'summary.json')
run=next(r for r in summary['results'] if r['case']=='main_first_cycle')
root=tk.Tk();app=Studio(root)
try:
    app.error=lambda message: (_ for _ in ()).throw(RuntimeError(message))
    root.update()
    database=vcd.index(Path(run['directory'])/'waves.vcd',eda.OUT/'startup_gui_wave_cache')
    window=WaveWindow(root,database,'main_first_cycle  启动时序')
    wanted=['clk','rst_n','arvalid','arready','req','rvalid']
    selected=[]
    for name in wanted:
        selected.append(next(s['id'] for s in window.meta['signals'] if
            s['name'].split('[')[0]=='tb_cmdbuf_rd.'+name))
    window.ids=selected;window.start=0;window.end=40000;window.cursor=1000;window.cursor_b=20000
    window.geometry('1440x720+10+10');window.format.set('bin');window.draw()
    end=time.time()+1
    while time.time()<end:root.update();time.sleep(.02)
    window.lift();root.update()
    x,y=window.winfo_rootx(),window.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+window.winfo_width(),y+window.winfo_height()),xdisplay=os.environ['DISPLAY']).save(str(evidence/'startup_wave.png'))
    eda.write_json(evidence/'acceptance.json',dict(status='PASS',run=run['id'],batch=summary['id'],
        waveform=str(Path(run['directory'])/'waves.fsdb'),displayed_signals=wanted,
        time_range_ps=[0,40000],cursor_a_ps=1000,cursor_b_ps=20000))
finally:root.destroy()
