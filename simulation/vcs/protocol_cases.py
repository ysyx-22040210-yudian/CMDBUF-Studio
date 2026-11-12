"""Small deterministic cases used by both the tutorial and contract acceptance."""
import copy


def lane(name, values, bus=False):
    wave=''; data=[]; old=None
    for v in values:
        if v==old: wave+='.'
        elif bus: wave+='='; data.append(v)
        else: wave+=str(v)
        old=v
    return dict(name=name,wave=wave,**({'data':data} if bus else {}))


def basic(bank=0,half=0,length=24):
    cfg={'description':'协议驱动：输入所有权明确，真实 ISSUE +4 返回；固定时钟和复位。',
         'sim':{'mode':'protocol','tick_ps':10000,'clock_skew_ps':1,'finish':'require_empty',
                'drivers':{'ar':'queued','bank':'busy_on_issue','mem':'on_issue','ar_grant':'on_req'},
                'queue_limit':64,'wait_cycles':100,'requests':{'A':{
                    'src':bank%3,'addr':hex(0x12000+bank*8+half*64+1),'id':42,'port':6,'resp':0,
                    'data':'0x00112233445566778899aabbccddeeff','pc_err':half}}},
         'signal':[{'name':'clk','wave':'p'+'.'*(length-1)},
                   lane('rst_n',[0]*2+[1]*(length-2)),
                   {'name':'ar_offer','wave':'0.='+'.'*(length-3),'data':['A']},
                   lane('ar_send',[1]*length),lane('rready',[7]*length,True),
                   lane('ar_grant_enable',[255]*length,True),lane('rw_rd_gnt',[255]*length,True)],
         'head':{'text':'Protocol-driven WaveDrom','tick':0},'config':{'hscale':1},
         'require':{'accepted':1,'issued':1,'captured':1,'retired':1}}
    cfg['signal'] += [lane('bank_busy[{}]'.format(b),[0]*length,True) for b in range(8)]
    return cfg


def replace(cfg,name,new):
    cfg['signal']=[new if s['name']==name else s for s in cfg['signal']]


def traffic(reset=False):
    n=72; cfg=basic(length=n); requests={}; offer=['.']*n; offer[0]='0'; names=[]
    columns=list(range(2,14))+(list(range(20,24)) if reset else [])
    for i,col in enumerate(columns):
        name='T{}'.format(i); offer[col]='='; names.append(name)
        requests[name]={'src':i%3,'addr':hex(0x12000+(i%8)*8+((i//8)%2)*64+(i%7)),
                        'id':42,'port':i,'resp':3 if i%5==0 else 0,
                        'data':'0x{:032x}'.format(0xabcdef0000000000+i*0x123456789abc+1),'pc_err':i%2}
    cfg['sim']['requests']=requests
    replace(cfg,'ar_offer',dict(name='ar_offer',wave=''.join(offer),data=names))
    clock=list('p'+'.'*(n-1)); pause=15 if reset else 17
    clock[pause]='l'; clock[pause+3]='p'; replace(cfg,'clk',dict(name='clk',wave=''.join(clock)))
    rst=[0]*2+[1]*(n-2)
    if reset: rst[15:17]=[0,0]
    replace(cfg,'rst_n',lane('rst_n',rst))
    replace(cfg,'rready',lane('rready',[0]*28+[7]*(n-28),True))
    cfg['require']={'accepted':12 if not reset else 13,'retired':12 if not reset else 4,
                    'ar_gated':1,'full':1}
    if not reset: cfg['require']['full_reuse']=1
    else: cfg['require'].update(queued_cancelled=3,aborted=9,reset_assertions=2)
    cfg['description']='协议驱动：12 笔同 ID 的多 XAU 请求、满载背压、停钟、同拍回收。'+('停钟复位取消排队及在途事务，恢复后再发 4 笔。' if reset else '')
    return cfg


def fixed_ar():
    cfg=basic(); n=24
    cfg['sim']['drivers']['ar']='fixed'; cfg['sim']['requests']={}
    cfg['signal']=[s for s in cfg['signal'] if s['name'] not in ('ar_offer','ar_send')]
    cfg['signal'] += [lane('arvalid',[0,0,1]+[0]*21),lane('src',[0]*n,True),
        lane('araddr',[0x12001]*n,True),lane('arid',[42]*n,True),lane('ar_port_id',[6]*n,True),
        lane('arresp',[0]*n,True),lane('mem_error',[1]*n)]
    return cfg


def fixed_mem():
    cfg=basic(); cfg['sim']['drivers']['mem']='fixed'
    data=int(cfg['sim']['requests']['A']['data'],16)
    cfg['signal'] += [lane('mem_rdata',[0]*8+[hex(data)]+[0]*15,True),lane('pc_err',[0]*24)]
    return cfg


def interactions():
    result={'protocol_read':basic(),'protocol_demo':traffic(),'protocol_reset':traffic(True),'protocol_fixed_ar':fixed_ar(),
            'protocol_fixed_mem':fixed_mem()}
    c=basic(length=40)
    replace(c,'ar_grant_enable',lane('ar_grant_enable',[255]*8+[0]*5+[255]*27,True))
    replace(c,'rw_rd_gnt',lane('rw_rd_gnt',[0]*8+[255]*32,True))
    c['require'].update(issue_failed=1,ar_only=1,rw_only=1); result['protocol_retry']=c
    c=basic(); replace(c,'ar_send',lane('ar_send',[0]*8+[1]*16)); result['protocol_window']=c
    c=basic(length=40); c['sim']['tick_ps']=5000
    replace(c,'clk',dict(name='clk',wave='n'+'.'*19,period=2)); result['protocol_nclock']=c
    c=copy.deepcopy(c); replace(c,'clk',dict(name='clk',wave='01'*20)); result['protocol_levels']=c
    c=basic(); c['sim']['requests']['A']['resp']=3; c['sim']['requests']['A']['pc_err']=1
    c['require']['resp3']=1; result['protocol_error_priority']=c
    c=basic(length=40); c['sim']['requests']['B']=copy.deepcopy(c['sim']['requests']['A'])
    c['sim']['requests']['B']['data']='0xfedcba98765432100123456789abcdef'
    replace(c,'ar_offer',dict(name='ar_offer',wave='0.=='+'.'*36,data=['A','B']))
    c['require'].update(accepted=2,retired=2); result['protocol_same_address']=c
    c=basic(length=48); c['sim']['requests']={}; names=[]; offer=['.']*48; offer[0]='0'
    for i in range(8):
        name='S'+str(i); names.append(name); offer[2+i]='='
        c['sim']['requests'][name]=dict(src=0,addr=0x12001+i*8,id=42,data=hex(i+1))
    replace(c,'ar_offer',dict(name='ar_offer',wave=''.join(offer),data=names))
    replace(c,'rready',lane('rready',[0]*24+[7]*24,True))
    c['description']='协议驱动：选中源未 ready 时保留原请求，等待该源 ready 再发。'
    c['require'].update(accepted=8,retired=8,ar_gated=1); result['protocol_selected_reject']=c
    c=basic(length=96); replace(c,'clk',dict(name='clk',wave='00111000'*12))
    result['protocol_duty_cycle']=c
    return result
