"""Independent procedural TC assignments, edge timing, calendar and per-XAU FIFO audit."""
import csv
import json
from pathlib import Path
from wavedrom_adapter import PORTS


def verify_execution(directory, program):
    root=Path(directory); failures=[]
    cov={'E03_SRC':0,'E04_ARRESP':0,'E05_ZERO_READY':0,'E06_SUCCESS_BUSY':[0]*16,
         'T_MEM4':0,'T_ASSOCIATION':0,'P_KNOWN':0,'assignment_equal_pins':0,
         'paused_intervals':0,'reset_assertions':1,'cancelled':0}
    phases={'body':0,'drain':0,'quiet':0}; hist={}
    try:
        with (root/'samples.csv').open() as f: samples=list(csv.DictReader(f))
        with (root/'assigned.csv').open() as f: solutions=list(csv.DictReader(f))
        config=json.loads((root/'tc_config.json').read_text())
        if not samples or len(samples)!=len(solutions): raise ValueError('MAIN_ARCHIVE_COUNT')
        tokens=program.split()
        if tokens[:1]!=['MAIN_PHASE_V1'] or config['name']!=tokens[1] or config['seed']!=int(tokens[2]):
            raise ValueError('MAIN_CONFIG_ARCHIVE')
        if int(tokens[3]) and config['num_cycles']!=int(tokens[3]): raise ValueError('MAIN_CYCLES_OVERRIDE')
        live=[];heads=[[],[],[]];due={};edge=1;previous_sample=5000;previous_high=5;old_reset=0;last_phase=0
        for slot,(s,q) in enumerate(zip(samples,solutions)):
            if None in s or None in q: raise ValueError('MAIN_ARCHIVE_COLUMNS')
            if int(s['cycle'])!=slot or int(q['cycle'])!=slot: raise ValueError('MAIN_CYCLE_GAP')
            if int(s['edge'])!=edge or int(q['edge'])!=edge: raise ValueError('MAIN_EDGE_GAP')
            phase=int(s['phase'])
            if phase not in (0,1,2) or phase<int(last_phase) or phase!=int(q['phase']): raise ValueError('MAIN_PHASE')
            phases[{0:'body',1:'drain',2:'quiet'}[phase]]+=1;last_phase=phase
            lo=int(q['clk_low_ns']);hi=int(q['clk_high_ns']);pause=int(q['clk_pause_ns'])
            if lo<3 or hi<3 or pause<0: raise ValueError('MAIN_CLOCK_CONFIG')
            launch=int(q['launch_ps']);sample_time=int(s['time_ps'])
            if launch!=previous_sample or int(q['time_ps'])!=launch+2000: raise ValueError('MAIN_AFTER_POSEDGE')
            expected=launch+1000*(previous_high+lo+pause)
            if sample_time!=expected or int(q['expected_sample_ps'])!=expected: raise ValueError('MAIN_CLOCK_TIME')
            previous_sample=sample_time;previous_high=hi
            if pause: cov['paused_intervals']+=1
            for name,width in PORTS:
                if len(s[name])!=width or set(s[name])-{'0','1'}: raise ValueError('P_KNOWN '+name)
                if s[name]!=q[name]: raise ValueError('MAIN_PIN_MISMATCH '+name)
            v={n:int(s[n],2) for n,_ in PORTS}
            o={n:int(s[n],16) for n in ['arready','req','arb_addr','rvalid','rdata','rid','r_port_id','raddr','rresp','pending_cnt']}
            if any(o[n]!=int(q[n],16) for n in ['arready','req','arb_addr']): raise ValueError('MAIN_CONTEXT_CHANGED')
            cov['P_KNOWN']+=1;cov['assignment_equal_pins']+=1
            if v['clk']!=1: raise ValueError('MAIN_EXPECT_POSEDGE')
            if phase and (not v['rst_n'] or not v['clk'] or v['arvalid'] or v['rready']!=7 or v['ar_rd_gnt']!=255 or v['rw_rd_gnt']!=255):
                raise ValueError('MAIN_COMPLETION_POLICY')
            if not v['rst_n'] and old_reset!=0:
                cov['reset_assertions']+=1;cov['cancelled']+=len(live)
                live=[];heads=[[],[],[]];due={}
            old_reset=v['rst_n']
            if v['rst_n']:
                if v['arvalid']:
                    if v['src']>=3: raise ValueError('E03_SRC')
                    if v['arresp'] not in (0,3): raise ValueError('E04_ARRESP')
                    cov['E03_SRC']+=1;cov['E04_ARRESP']+=1
                if o['arready']==0:
                    cov['E05_ZERO_READY']+=1
                    if v['arvalid']: raise ValueError('E05_ZERO_READY')
                success=o['req'] & v['ar_rd_gnt'] & v['rw_rd_gnt']
                if o['req'] & (o['req']-1): raise ValueError('DUT_REQ_ONEHOT0')
                half=o['arb_addr']&1
                for bank in range(8):
                    if (success>>bank)&1:
                        if not (v['rd_mem_status[{}]'.format(bank)]>>half)&1: raise ValueError('E06_SUCCESS_BUSY')
                        if v['clk']:cov['E06_SUCCESS_BUSY'][bank+8*half]+=1
                if edge in due:
                    token=due[edge]
                    if (v['mem_rdata'],v['pc_err'])!=(token['data'],token['error']): raise ValueError('T_MEM4')
                if v['clk']:
                    arriving=due.pop(edge,None)
                    if arriving is not None: arriving['done']=True;cov['T_MEM4']+=1
                    if success:
                        bank=success.bit_length()-1
                        candidates=[x for x in live if not x['issued'] and x['addr']>>6==o['arb_addr'] and (x['addr']>>3)&7==bank]
                        if not candidates: raise ValueError('T_ASSOCIATION_UNKNOWN_ISSUE')
                        token=candidates[0];token['issued']=True
                        token.update(data=int(q['issue_data'],16),error=int(q['issue_error']))
                        if edge+4 in due: raise ValueError('T_MEM4_COLLISION')
                        due[edge+4]=token
                    response=o['rvalid'] & v['rready']
                    if response:
                        if response & (response-1): raise ValueError('DUT_R_ONEHOT0')
                        src=response.bit_length()-1
                        if not heads[src]: raise ValueError('T_ASSOCIATION_UNSOLICITED_R')
                        token=heads[src].pop(0)
                        resp=3 if token['resp']==3 else token.get('error',0)
                        if not token['done'] or (o['rid'],o['r_port_id'],o['raddr'],o['rdata'],o['rresp'])!=(
                            token['id'],token['port'],token['addr']&7,token.get('data'),resp):
                            raise ValueError('T_ASSOCIATION_FIFO_DATA')
                        live.remove(token);cov['T_ASSOCIATION']+=1
                    if v['arvalid'] and (o['arready']>>v['src'])&1:
                        token=dict(src=v['src'],addr=v['araddr'],id=v['arid'],port=v['ar_port_id'],resp=v['arresp'],issued=False,done=False)
                        live.append(token);heads[v['src']].append(token)
                    if phase==0:
                        for name,value in [('arvalid',v['arvalid']),('rready',v['rready']),('src_valid',v['src'] if v['arvalid'] else -1),('arresp_valid',v['arresp'] if v['arvalid'] else -1)]:
                            h=hist.setdefault(name,{});h[str(value)]=h.get(str(value),0)+1
            if v['clk']:edge+=1
        if phases['body']!=config['num_cycles']:raise ValueError('MAIN_BODY_COUNT')
        if phases['quiet']!=8:raise ValueError('MAIN_QUIET_COUNT')
        if live or due or any(heads) or o['req'] or o['pending_cnt']:raise ValueError('MAIN_PENDING')
    except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        failures.append('main_phase audit cycle {} edge {}: {}'.format(locals().get('slot','?'),locals().get('edge','?'),exc))
    report=dict(status='FAIL' if failures else 'PASS',failures=failures,phases=phases,coverage=cov,
                body_histograms=hist,method='Procedural main_phase if assignments; independent next-posedge pin sampling, timing, calendar and per-XAU FIFO',
                distribution='Seeded urandom in legal if branches; no solver or uniform-trace-distribution claim')
    (root/'constraint_coverage.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report
