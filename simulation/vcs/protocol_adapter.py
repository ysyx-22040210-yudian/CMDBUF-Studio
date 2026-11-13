"""WaveDrom fixed controls + explicitly owned reactive protocol groups."""
import copy
import csv
import json
from pathlib import Path
from wavedrom_adapter import PORTS, WIDTHS, bits, flatten, positive
import contracts

AR = ['arvalid', 'src', 'araddr', 'arid', 'ar_port_id', 'arresp']
BANK = ['rd_mem_status[{}]'.format(i) for i in range(8)]
MEM = ['mem_rdata', 'pc_err']


def num(v, width, label):
    b = bits(v, width, label)
    if set(b) - {'0', '1'}:
        raise ValueError('P_KNOWN: ' + label)
    return int(b, 2)


def compile_protocol(cfg):
    contracts.audit()
    from wavedrom_adapter import compile_wave
    sim = cfg.get('sim', {})
    permitted = {'mode', 'tick_ps', 'clock_skew_ps', 'finish', 'values', 'drivers',
                 'requests', 'queue_limit', 'wait_cycles'}
    if set(sim) - permitted:
        raise ValueError('P_OWNERSHIP: unknown protocol option')
    drivers = sim.get('drivers', {})
    required = {'ar', 'bank', 'mem'}
    if not required <= set(drivers) <= required | {'ar_grant'} or any(drivers[k] not in modes for k, modes in
            [('ar', ('queued', 'fixed')), ('bank', ('busy_on_issue', 'fixed')), ('mem', ('on_issue', 'fixed'))]):
        raise ValueError('P_OWNERSHIP: declare ar=queued|fixed, bank=busy_on_issue|fixed, mem=on_issue|fixed')
    grant = drivers.get('ar_grant', 'fixed')
    if grant not in ('fixed', 'on_req'):
        raise ValueError('P_OWNERSHIP: ar_grant must be fixed or on_req')
    flags = [int(drivers['ar']=='queued'), int(drivers['bank']=='busy_on_issue'), int(drivers['mem']=='on_issue'),
             int(grant=='on_req')]
    owned = (AR if flags[0] else []) + (BANK if flags[1] else []) + (MEM if flags[2] else [])
    if flags[3]:
        owned.append('ar_rd_gnt')
    lanes = flatten(cfg.get('signal'))
    names = [l.get('name') for l in lanes]
    if len(names) != len(set(names)) or set(names) & set(owned):
        raise ValueError('P_OWNERSHIP: duplicate lane or fixed lane also owned by automatic driver')
    virtual = (['ar_offer', 'ar_send'] if flags[0] else ['mem_error'])
    virtual += ['bank_busy[{}]'.format(i) for i in range(8)] if flags[1] else []
    if flags[3]:
        virtual.append('ar_grant_enable')
    expected = (set(WIDTHS) - set(owned)) | set(virtual)
    if set(names) != expected:
        raise ValueError('P_OWNERSHIP: missing={} unknown={}'.format(sorted(expected-set(names)), sorted(set(names)-expected)))
    byname = {l['name']: l for l in lanes}
    if 'clk' not in byname:
        raise ValueError('Missing clk')
    length = len(byname['clk']['wave']) * byname['clk'].get('period', 1)
    if length < 1 or length > 100000:
        raise ValueError('Invalid protocol timeline length')
    # Compile fixed lanes with the exact-mode compiler, substituting harmless
    # placeholders only for explicitly owned ports. There is no implicit owner.
    raw = copy.deepcopy(cfg)
    raw['sim'] = {k:v for k,v in sim.items() if k in {'tick_ps','clock_skew_ps','finish','values'}}
    raw['signal'] = [copy.deepcopy(l) for l in lanes if l['name'] in WIDTHS]
    for name in owned:
        lane = {'name':name, 'wave':'0'+'.'*(length-1)}
        if name in BANK:
            lane = copy.deepcopy(byname['bank_busy[{}]'.format(BANK.index(name))]); lane['name']=name
        if name == 'ar_rd_gnt':
            # The archived grant slot stores permission bits for this owner.
            lane = dict(byname['ar_grant_enable'], name=name)
        raw['signal'].append(lane)
    plan = compile_wave(raw)
    requests, releases = [], {}
    request_cfg = sim.get('requests', {})
    if not isinstance(request_cfg, dict) or len(request_cfg)>1024:
        raise ValueError('P_QUEUE: requests must be an object with at most 1024 entries')
    control_values = {}
    for control in (['ar_send'] if flags[0] else ['mem_error']):
        control_raw = copy.deepcopy(raw)
        lane = copy.deepcopy(byname[control]); lane['name']='pc_err'
        control_raw['signal'] = [l for l in control_raw['signal'] if l['name']!='pc_err']+[lane]
        cp = compile_wave(control_raw)
        control_values = {f['time_ps']:f['pc_err'] for f in cp['frames']}
        if any(v not in ('0','1') for v in control_values.values()):
            raise ValueError('P_KNOWN: '+control)
    if flags[0]:
        offer = byname['ar_offer']
        if set(offer)-{'name','wave','data','period','node','phase'} or offer.get('phase',0)!=0:
            raise ValueError('P_OWNERSHIP: invalid ar_offer lane')
        wave, period, data = offer['wave'], offer.get('period',1), offer.get('data',[])
        positive(period,'ar_offer.period',100000)
        if isinstance(data,str): data=data.split()
        if len(wave)*period != plan['columns'] or not isinstance(data,list):
            raise ValueError('ar_offer must span the whole diagram')
        used, seen = 0, set()
        for i,ch in enumerate(wave):
            if ch in '=23456789':
                if used>=len(data) or data[used] not in request_cfg or data[used] in seen:
                    raise ValueError('P_QUEUE: offer must name a unique declared request')
                name=data[used]; seen.add(name); used+=1
                r=request_cfg[name]
                if not isinstance(r,dict) or set(r)-{'src','addr','id','port','resp','data','pc_err','deadline_cycle'} or not {'src','addr'}<=set(r):
                    raise ValueError('Invalid request '+str(name))
                values={k:num(r.get(k,default),w,name+'.'+k) for k,w,default in
                        [('src',2,0),('addr',18,0),('id',8,0),('port',5,0),('resp',2,0),('data',128,0),('pc_err',1,0)]}
                if values['src']>2: raise ValueError('E03_SRC: '+name)
                if values['resp'] not in (0,3): raise ValueError('E04_ARRESP: '+name)
                deadline=r.get('deadline_cycle',-1)
                if deadline != -1: positive(deadline,name+'.deadline_cycle',1000000,0)
                values.update(name=name,token=len(requests),deadline_cycle=deadline)
                releases[i*period*plan['tick_ps']]=len(requests)
                requests.append(values)
            elif ch not in '0x.':
                raise ValueError('ar_offer: use =/2..9 for one request, . for no new request, 0/x for idle')
        if used != len(data) or seen != set(request_cfg):
            raise ValueError('P_QUEUE: unused request or offer data')
    elif request_cfg:
        raise ValueError('P_OWNERSHIP: fixed AR cannot also define queued requests')
    # A prepare boundary exists one setup interval before every rising edge,
    # including n clocks and clocks whose cells have period > 1.
    frames={f['time_ps']:dict(f) for f in plan['frames']}
    prep={s['time_ps']-plan['clock_skew_ps'] for s in plan['samples']}
    times=sorted(set(frames)|prep|set(releases))
    original=sorted(frames)
    previous=None; idx=0; control='0'
    cooked=[]
    controls=sorted(control_values); ci=0
    for t in times:
        while idx<len(original) and original[idx]<=t:
            previous=frames[original[idx]]; idx+=1
        while ci<len(controls) and controls[ci]<=t:
            control=control_values[controls[ci]]; ci+=1
        f=dict(previous,time_ps=t,prepare=int(t in prep),release=releases.get(t,-1),
               send=int(control) if flags[0] else 0,mem_error=int(control) if not flags[0] else 0)
        if f['release']>=0 and f['rst_n']!='1':
            raise ValueError('T_CLOCK_RESET: request released during reset at column {}'.format(t//plan['tick_ps']))
        cooked.append(f)
    plan.update(frames=cooked,requests=requests,flags=flags,owned=owned,
                fixed=[n for n,_ in PORTS if n not in owned],
                queue_limit=positive(sim.get('queue_limit',64),'queue_limit',1024),
                wait_cycles=positive(sim.get('wait_cycles',100),'wait_cycles',1000000),mode='protocol')
    return plan


def make_program(cfg):
    p=compile_protocol(cfg)
    fields = [len(p['frames']), p['tick_ps'], p['duration_ps'], p['clock_skew_ps'],
              int(p['finish']=='require_empty'), *p['flags'], len(p['requests']), p['queue_limit'], p['wait_cycles']]
    lines = ['PROTOCOL_V2 ' + ' '.join(map(str, fields))]
    for r in p['requests']:
        lines.append('{} {} {:x} {:x} {:x} {:x} {:032x} {} {}'.format(r['token'],r['src'],r['addr'],
            r['id'],r['port'],r['resp'],r['data'],r['pc_err'],r['deadline_cycle']))
    for f in p['frames']:
        lines.append('{} {} {} {} {} {}'.format(f['time_ps'],f['prepare'],f['release'],f['send'],f['mem_error'],
                     ' '.join(f[n] for n,_ in PORTS)))
    return '\n'.join(lines)+'\n'


def read_program(program):
    lines=program.splitlines(); h=lines[0].split()
    legacy = len(h)==12 and h[0]=='PROTOCOL_V1'
    if not legacy and not (len(h)==13 and h[0]=='PROTOCOL_V2'):
        raise ValueError('Unknown protocol archive version')
    fields = list(map(int, h[1:]))
    if legacy:
        fields.insert(8, 0)
    count,tick,duration,skew,empty,ar,bank,mem,grant,nreq,limit,wait=fields
    reqs=[]
    for line in lines[1:1+nreq]:
        x=line.split(); reqs.append(dict(token=int(x[0]),src=int(x[1]),addr=int(x[2],16),id=int(x[3],16),
            port=int(x[4],16),resp=int(x[5],16),data=int(x[6],16),pc_err=int(x[7]),deadline_cycle=int(x[8])))
    frames=[]
    for line in lines[1+nreq:]:
        x=line.split()
        if len(x)!=26: raise ValueError('Malformed protocol frame')
        f=dict(zip([n for n,_ in PORTS],x[5:])); f.update(zip(['time_ps','prepare','release','send','mem_error'],map(int,x[:5]))); frames.append(f)
    if len(frames)!=count: raise ValueError('Protocol archive frame count mismatch')
    owned=(AR if ar else [])+(BANK if bank else [])+(MEM if mem else [])
    if grant:
        owned.append('ar_rd_gnt')
    return dict(frames=frames,requests=reqs,flags=[ar,bank,mem,grant],owned=owned,legacy=legacy,
                fixed=[n for n,_ in PORTS if n not in owned],tick_ps=tick,clock_skew_ps=skew,
                finish='require_empty' if empty else 'allow_pending',queue_limit=limit,wait_cycles=wait)


def memory_pattern(token,addr,seed):
    a=(0x81000000 ^ token*0x9e3779b9 ^ seed)&0xffffffff
    b=(0x1234abcd ^ token*0x7f4a7c15 ^ addr)&0xffffffff
    c=(0xfedc9876 ^ token*0x6c8e9cf5 ^ (seed<<7))&0xffffffff
    d=(0x55aa00ff ^ token*0x85ebca6b ^ (seed>>3))&0xffffffff
    return (d<<96)|(c<<64)|(b<<32)|a


def verify_execution(cfg,directory,program,contract_revision=contracts.REVISION):
    """Independent Python queue/calendar/FIFO monitor of the archived inputs."""
    p=read_program(program); root=Path(directory); errors=[]
    def read(name):
        with (root/name).open() as s: return list(csv.DictReader(s))
    try: driven,samples=read('driven.csv'),read('samples.csv')
    except OSError as e: return dict(status='FAIL',failures=[str(e)])
    cov={k:0 for k in ['E03_SRC','E04_ARRESP','E05_ZERO_READY','T_MEM4','T_ASSOCIATION','T_CLOCK_RESET','P_KNOWN','P_OWNERSHIP','P_QUEUE']}
    cov['E06_SUCCESS_BUSY']=[0]*16
    if len(driven)!=len(p['frames']): errors.append('driven frame count mismatch')
    for i,(actual,planned) in enumerate(zip(driven,p['frames'])):
        if int(actual['time_ps'])!=planned['time_ps'] or any(actual[n]!=planned[n] for n in p['fixed']):
            errors.append('P_OWNERSHIP fixed input changed at frame {}'.format(i)); break
        cov['P_OWNERSHIP']+=1
    edges=[]; last='0'
    for f in p['frames']:
        if f['clk']=='1' and last=='0': edges.append(f['time_ps'])
        last=f['clk']
    if edges != [int(s['time_ps']) for s in samples]: errors.append('T_CLOCK_RESET clock edge mismatch')
    q=[]; live=[]; due={}; heads=[[],[],[]]; frameidx=0; previous_reset='x'; submitted=0; cancelled_q=0; blocked=0
    flags=p['flags']; seed=json.loads((root/'metrics.json').read_text())['seed'] if (root/'metrics.json').exists() else 1
    latest=None; release_edge={}; sample_index=0
    def frames_until(t):
        nonlocal frameidx,previous_reset,latest,cancelled_q,submitted
        while frameidx<len(p['frames']) and p['frames'][frameidx]['time_ps']<=t:
            f=p['frames'][frameidx]; latest=f; frameidx+=1
            if f['rst_n']=='0' and previous_reset!='0':
                cancelled_q+=len(q); q[:]=[]; live[:]=[]; due.clear()
                for h in heads: h[:]=[]
                cov['T_CLOCK_RESET']+=1
            previous_reset=f['rst_n']
            if f['release']>=0:
                q.append(f['release']); release_edge[f['release']]=sample_index; submitted+=1
    try:
        for s in samples:
            cycle=int(s['cycle']); sample_index=cycle
            frames_until(int(s['time_ps']))
            if cycle!=cov['P_QUEUE']: raise ValueError('cycle index mismatch')
            cov['P_QUEUE']+=1
            if s['rst_n']=='0': continue
            if any(set(s[n])-{'0','1'} for n,_ in PORTS): raise ValueError('P_KNOWN')
            v={n:int(s[n],2) for n,_ in PORTS}
            out={n:int(s[n],16) for n in ['arready','req','arb_addr','rvalid','rdata','rid','r_port_id','raddr','rresp']}
            cov['P_KNOWN']+=1
            if v['arvalid']:
                if v['src']>2: raise ValueError('E03_SRC')
                if v['arresp'] not in (0,3): raise ValueError('E04_ARRESP')
                cov['E03_SRC']+=1; cov['E04_ARRESP']+=1
            if out['arready']==0:
                cov['E05_ZERO_READY']+=1
                if v['arvalid']: raise ValueError('E05_ZERO_READY')
            if not p['legacy']:
                contracts.check_interface(v, out)
                cov['E07_SRC_READY'] = cov.get('E07_SRC_READY', 0) + int(v['src'] < 3)
                cov['E08_AR_GRANT_REQ'] = cov.get('E08_AR_GRANT_REQ', 0) + int(v['ar_rd_gnt'] != 0)
                if flags[3] and v['ar_rd_gnt'] != (int(latest['ar_rd_gnt'], 2) & out['req']):
                    raise ValueError('auto AR grant differs from request/permission')
            if flags[0]:
                intent=bool(q and latest['send'])
                ready = out['arready']
                if intent and not p['legacy']:
                    ready &= 1 << p['requests'][q[0]]['src']
                want=int(intent and bool(ready))
                if v['arvalid']!=want: raise ValueError('AR gate differs from queue/window/ready')
                if intent and not ready: blocked+=1
                if want or (p['legacy'] and intent):
                    r=p['requests'][q[0]]
                    for port,key in [('src','src'),('araddr','addr'),('arid','id'),('ar_port_id','port'),('arresp','resp')]:
                        if v[port]!=r[key]: raise ValueError('queued payload changed: '+port)
                elif not p['legacy'] and v['src']!=3:
                    raise ValueError('queued AR idle source must be 3')
                for token in q:
                    deadline=p['requests'][token]['deadline_cycle']
                    if cycle-release_edge[token]>p['wait_cycles'] or (deadline>=0 and cycle>deadline): raise ValueError('P_QUEUE deadline')
            success=out['req'] & v['ar_rd_gnt'] & v['rw_rd_gnt']
            for b in range(8):
                base=int(latest[BANK[b]],2)
                busy=(success>>b)&1; half=out['arb_addr']&1
                if busy:
                    cov['E06_SUCCESS_BUSY'][b+8*half]+=1
                    if not (v[BANK[b]]>>half)&1: raise ValueError('E06_SUCCESS_BUSY bank='+str(b))
                if flags[1] and v[BANK[b]]!=(base | ((1<<half) if busy else 0)):
                    raise ValueError('auto bank differs from baseline/issue')
            arriving=due.pop(cycle,None)
            if arriving is not None:
                if v['mem_rdata']!=arriving['data'] or v['pc_err']!=arriving['pc_err']: raise ValueError('T_MEM4 payload/association')
                arriving['done']=True; cov['T_MEM4']+=1
            elif flags[2] and (v['mem_rdata'] or v['pc_err']): raise ValueError('T_MEM4 non-due auto payload')
            # Output FIFO is independent of the native reference model.
            fired=out['rvalid'] & v['rready']
            if fired:
                if fired & (fired-1): raise ValueError('multiple R sources')
                x=fired.bit_length()-1
                if not heads[x]: raise ValueError('T_ASSOCIATION unexpected R')
                r=heads[x].pop(0)
                if not r.get('done') or r is arriving: raise ValueError('T_ASSOCIATION premature R')
                expected=(r['id'],r['port'],r['addr']&7,r['data'],
                          contracts.expected_rresp(r['resp'], r['pc_err'], contract_revision))
                actual=tuple(out[n] for n in ['rid','r_port_id','raddr','rdata','rresp'])
                if actual!=expected: raise ValueError('T_ASSOCIATION R payload/order')
                live.remove(r); cov['T_ASSOCIATION']+=1
            if success:
                if success & (success-1): raise ValueError('multiple successful banks')
                bank=success.bit_length()-1
                candidates=[r for r in live if not r.get('issued') and (r['addr']>>6)==out['arb_addr'] and ((r['addr']>>3)&7)==bank]
                if not candidates: raise ValueError('T_ASSOCIATION issue without accepted request')
                r=candidates[0]; r['issued']=True
                if cycle+4 in due: raise ValueError('T_MEM4 calendar collision')
                due[cycle+4]=r
            if v['arvalid'] and ((out['arready']>>v['src'])&1):
                if flags[0]: r=dict(p['requests'][q.pop(0)])
                else:
                    token=submitted; submitted+=1
                    r=dict(token=token,src=v['src'],addr=v['araddr'],id=v['arid'],port=v['ar_port_id'],resp=v['arresp'],
                           data=memory_pattern(token,v['araddr']>>6,seed),pc_err=latest['mem_error'])
                live.append(r); heads[r['src']].append(r)
        sample_index=len(samples); frames_until(p['frames'][-1]['time_ps'])
        if p['finish']=='require_empty' and (q or live or due): raise ValueError('P_QUEUE/T_MEM4 pending at end')
    except (ValueError,KeyError,IndexError) as e:
        errors.append('protocol audit cycle {}: {}'.format(sample_index,e))
    result=dict(status='PASS' if not errors else 'FAIL',failures=errors,frames=len(driven),clock_edges=len(samples),
                fixed_inputs=p['fixed'],automatic_inputs=p['owned'],coverage=cov,submitted=submitted,
                queued_cancelled=cancelled_q,blocked_cycles=blocked,queue_pending=len(q),finish=p['finish'])
    (root/'constraint_coverage.json').write_text(json.dumps(cov,indent=2)+'\n')
    return result
