"""Independent check of solver assignments, sampled pins and transaction history."""
import csv
import json
from pathlib import Path
from wavedrom_adapter import PORTS


def has_random(program):
    return any(line.split()[:1] == ['2'] for line in program.splitlines())


def verify_execution(directory,program):
    root=Path(directory)
    failures=[]
    phases={'directed':0,'random':0,'drain':0,'quiet':0}
    cov={'E03_SRC':0,'E04_ARRESP':0,'E05_ZERO_READY':0,'E06_SUCCESS_BUSY':[0]*16,
         'T_MEM4':0,'T_ASSOCIATION':0,'P_KNOWN':0,'solver_assignment_equal_pins':0,
         'selected_source_rejected':0,'unconstrained_mem_samples':0}
    hist={k:{} for k in ['arvalid','src_when_valid','arresp_when_valid','src_when_idle',
                       'arresp_when_idle','rready','grant_pair_on_req','pc_err_on_return']}
    hist['unconstrained_bank_half']=[[0,0] for _ in range(16)]
    def hit(key,value):
        d=hist[key];value=str(value);d[value]=d.get(value,0)+1
    try:
        with (root/'samples.csv').open() as f:samples=list(csv.DictReader(f))
        with (root/'random_solutions.csv').open() as f:solutions=list(csv.DictReader(f))
        if not samples or len(samples)!=len(solutions):raise ValueError('RND_ARCHIVE sample/solution count')
        expected=sum(int(l.split()[1]) for l in program.splitlines() if l.split()[:1]==['2'])
        names={0:'directed',2:'random',3:'drain',4:'quiet'}
        live=[];heads=[[],[],[]];due={};issued=0;returned=0
        for cycle,(s,q) in enumerate(zip(samples,solutions)):
            if None in s or None in q:raise ValueError('RND_ARCHIVE malformed columns')
            if int(s['cycle'])!=cycle or int(q['cycle'])!=cycle:raise ValueError('RND_ARCHIVE cycle gap')
            phase=int(s['phase'])
            if phase not in names or phase!=int(q['phase']):raise ValueError('RND_PHASE')
            phases[names[phase]]+=1
            if any(set(s[n])-{'0','1'} for n,_ in PORTS):raise ValueError('P_KNOWN')
            if s['clk']!='1' or s['rst_n']!='1':raise ValueError('RND_CLOCK_RESET')
            v={n:int(s[n],2) for n,_ in PORTS}
            o={n:int(s[n],16) for n in ['arready','req','arb_addr','rvalid','rdata','rid','r_port_id','raddr','rresp']}
            cov['P_KNOWN']+=1
            for n,_ in PORTS:
                if s[n]!=q[n]:raise ValueError('RND_POST_SOLVE_MODIFICATION '+n)
            if any(int(q[n],16)!=o[n] for n in ['arready','req','arb_addr']):raise ValueError('RND_CONTEXT_CHANGED')
            if phase!=0:cov['solver_assignment_equal_pins']+=1
            if phase in (3,4) and (v['arvalid'] or v['rready']!=7 or v['ar_rd_gnt']!=255 or v['rw_rd_gnt']!=255):raise ValueError('RND_DRAIN_POLICY')
            if v['arvalid']:
                if v['src']>=3:raise ValueError('E03_SRC')
                if v['arresp'] not in (0,3):raise ValueError('E04_ARRESP')
                cov['E03_SRC']+=1;cov['E04_ARRESP']+=1
            if o['arready']==0:
                cov['E05_ZERO_READY']+=1
                if v['arvalid']:raise ValueError('E05_ZERO_READY')
            success=o['req'] & v['ar_rd_gnt'] & v['rw_rd_gnt']
            if o['req'] & (o['req']-1):raise ValueError('DUT req not onehot0')
            half=o['arb_addr']&1
            for b in range(8):
                if (success>>b)&1:
                    cov['E06_SUCCESS_BUSY'][b+8*half]+=1
                    if not (v['rd_mem_status[{}]'.format(b)]>>half)&1:raise ValueError('E06_SUCCESS_BUSY')
                if phase==2:
                    for h in range(2):
                        if not ((success>>b)&1 and h==half):
                            hist['unconstrained_bank_half'][b+8*h][(v['rd_mem_status[{}]'.format(b)]>>h)&1]+=1
            arriving=due.pop(cycle,None)
            if arriving is not None:
                if v['mem_rdata']!=arriving['data'] or v['pc_err']!=arriving['error']:raise ValueError('T_MEM4')
                arriving['done']=True;cov['T_MEM4']+=1
                if phase==2:hit('pc_err_on_return',v['pc_err'])
            elif phase==2:cov['unconstrained_mem_samples']+=1
            if success:
                bank=(success.bit_length()-1)
                candidates=[x for x in live if not x['issued'] and x['addr']>>6==o['arb_addr'] and (x['addr']>>3)&7==bank]
                if not candidates:raise ValueError('T_ASSOCIATION unknown ISSUE')
                token=candidates[0];token['issued']=True;issued+=1
                token.update(data=int(q['next_data'],16),error=int(q['next_error']))
                if cycle+4 in due:raise ValueError('T_MEM4 collision')
                due[cycle+4]=token
            response=o['rvalid'] & v['rready']
            if response:
                if response & (response-1):raise ValueError('DUT R not onehot0')
                src=response.bit_length()-1
                if not heads[src]:raise ValueError('T_ASSOCIATION unsolicited R')
                token=heads[src].pop(0)
                expected_resp=3 if token['resp']==3 else token.get('error',0)
                if not token['done'] or (o['rid'],o['r_port_id'],o['raddr'],o['rdata'],o['rresp']) != (
                        token['id'],token['port'],token['addr']&7,token.get('data'),expected_resp):
                    raise ValueError('T_ASSOCIATION source FIFO/data')
                live.remove(token);returned+=1;cov['T_ASSOCIATION']+=1
            if v['arvalid']:
                if (o['arready']>>v['src'])&1:
                    token=dict(src=v['src'],addr=v['araddr'],id=v['arid'],port=v['ar_port_id'],resp=v['arresp'],issued=False,done=False)
                    live.append(token);heads[v['src']].append(token)
                elif phase==2:cov['selected_source_rejected']+=1
            if phase==2:
                hit('arvalid',v['arvalid']);hit('rready',v['rready'])
                hit('src_when_valid' if v['arvalid'] else 'src_when_idle',v['src'])
                hit('arresp_when_valid' if v['arvalid'] else 'arresp_when_idle',v['arresp'])
                if o['req']:
                    b=o['req'].bit_length()-1
                    hit('grant_pair_on_req',(((v['ar_rd_gnt']>>b)&1)<<1)|((v['rw_rd_gnt']>>b)&1))
        if phases['random']!=expected:raise ValueError('RND_COUNT expected {} got {}'.format(expected,phases['random']))
        if live or due or any(heads):raise ValueError('RND_PENDING')
        if phases['quiet']!=8:raise ValueError('RND_QUIET_COUNT')
        if phases['directed'] and all(l.split()[0]=='2' for l in program.splitlines()):raise ValueError('RND_UNDECLARED_DIRECTED_PHASE')
    except (OSError,ValueError,KeyError,IndexError,TypeError) as e:
        failures.append('random audit cycle {}: {}'.format(locals().get('cycle','?'),e))
    report=dict(status='FAIL' if failures else 'PASS',failures=failures,phases=phases,coverage=cov,
                random_phase_histograms=hist,method='VCS constrained randomization; solver assignment equals sampled pins; independent contract/calendar/source-FIFO checks',
                distribution='Staged conditional randomization, not a claim of uniform sampling of all complete traces')
    (root/'constraint_coverage.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report
