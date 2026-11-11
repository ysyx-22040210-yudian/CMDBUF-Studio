"""Actual vs intended WaveDrom view, reconstructed from archived drive frames."""
import csv
import copy
from pathlib import Path
from wavedrom_adapter import PORTS, write_preview
from protocol_adapter import read_program


def write_comparison(cfg, directory, program):
    root=Path(directory); p=read_program(program)
    if not (root/'driven.csv').exists(): return
    with (root/'driven.csv').open() as f: actual=list(csv.DictReader(f))
    if not actual or len(actual)!=len(p['frames']): return
    cols=max(1,p['frames'][-1]['time_ps']//p['tick_ps'])
    frames=[]; j=0
    for c in range(cols):
        t=c*p['tick_ps']+p['clock_skew_ps']
        while j+1<len(actual) and int(actual[j+1]['time_ps'])<=t: j+=1
        frames.append(actual[j])
    lanes=[]; symbols={}
    for n,w in PORTS:
        if n=='clk':
            from wavedrom_adapter import flatten
            lanes.append(copy.deepcopy(next(l for l in flatten(cfg['signal']) if l['name']=='clk'))); continue
        values=[f[n] for f in frames]; wave=''; data=[]; previous=None
        for value in values:
            if value==previous: wave+='.'
            elif w==1: wave+=value
            else:
                wave+='='
                label=('0x'+format(int(value,2),'x')) if not (set(value)-{'0','1'}) else value
                if n=='mem_rdata' and len(label)>10:
                    key='M'+str(len(symbols)+1); symbols[key]=label; label=key
                data.append(label)
            previous=value
        lanes.append(dict(name=n,wave=wave,**({'data':data} if data else {})))
    if (root/'samples.csv').exists():
        with (root/'samples.csv').open() as f: samples=list(csv.DictReader(f))
        for name in ['arready','req','pending_cnt']:
            wave=''; data=[]; old=None; index=0; value='0'
            for col in range(cols):
                while index<len(samples) and int(samples[index]['column'])<=col:
                    value=samples[index][name]; index+=1
                if value==old: wave+='.'
                else: wave+='='; data.append('0x'+value)
                old=value
            lanes.append(dict(name=name+' sampled',wave=wave,data=data))
    rendered={'head':{'text':'Actual driven inputs (column snapshots; full timing in FSDB)','tick':0},
              'config':{'hscale':2},'signal':[['Planned controls']+copy.deepcopy(cfg['signal']),['Actual input snapshots']+lanes],
              'sim':{'values':symbols},
              'description':'Observed values at column boundaries + clock skew. This comparison is a report, not an executable case.'}
    write_preview(rendered,root/'actual_vs_plan.html',validate=False)
    path=root/'actual_vs_plan.html'
    text=path.read_text(encoding='utf-8').replace('<h1>WaveDrom 定向用例</h1>','<h1>计划与实际采样对照</h1>')
    text=text.replace('下载文件并放入 cases 目录后，使用 make validate 和 make run 执行。','此页是运行报告，包含 DUT 输出，不能作为输入用例执行。')
    path.write_text(text,encoding='utf-8')
