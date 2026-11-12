"""SystemVerilog TC discovery, archival and factory generation (Python 3.6)."""
import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TC_DIR = HERE / 'tc'


def load(path):
    path = Path(path)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', path.stem):
        raise ValueError('SV TC filename must be an identifier')
    code = path.read_text(encoding='utf-8')
    if not re.search(r'\bclass\s+' + re.escape(path.stem) + r'\s+extends\s+cmdbuf_main_tc\s*;', code):
        raise ValueError('SV TC must declare class {} extends cmdbuf_main_tc;'.format(path.stem))
    return {'format':'sv_main_phase', 'name':path.stem, 'source':code,
            'source_sha256':hashlib.sha256(code.encode('utf-8')).hexdigest(),
            'description':next((s[3:].strip() for s in code.splitlines() if s.startswith('// ')), '')}


def program(cfg, seed, cycles=None):
    if cycles is not None and not 1 <= cycles <= 1000000:
        raise ValueError('CYCLES must be 1..1000000')
    return 'MAIN_PHASE_V1 {} {} {}\n'.format(cfg['name'], seed, cycles or 0)


def generate_factory(destination):
    """Compile all local TC classes once; run-time selection does not rebuild RTL."""
    directory = Path(destination)
    archive = directory / 'tc_sources'
    archive.mkdir(exist_ok=True)
    paths=sorted(TC_DIR.glob('*.sv')) + sorted((HERE/'tests'/'main_phase').glob('*.sv'))
    entries = [load(p) for p in paths]
    if len({c['name'] for c in entries})!=len(entries):
        raise ValueError('Duplicate SV TC class name in cases and test fixtures')
    lines = []
    for c in entries:
        p = archive / (c['name'] + '.sv')
        p.write_text(c['source'], encoding='utf-8')
        lines.append('`include "{}"'.format(p.as_posix()))
    lines += ['function automatic cmdbuf_main_tc create_sv_tc(input string name);']
    for c in entries:
        lines.append('    {} obj_{};'.format(c['name'], c['name']))
    lines.append('    case (name)')
    for c in entries:
        lines.append('        "{0}": begin obj_{0}=new(); return obj_{0}; end'.format(c['name']))
    lines += ['        default: return null;', '    endcase', 'endfunction']
    (directory / 'sv_tc_factory.svh').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return entries


def new_case(name, template):
    source = load(template)
    code = re.sub(r'\bclass\s+'+source['name']+r'\b', 'class '+name, source['source'], count=1)
    target = TC_DIR / (name+'.sv')
    with target.open('x',encoding='utf-8') as stream: stream.write(code)
    return target


def explain(cfg):
    return ('SV main_phase TC: {name}\n'
      '用户在 main_phase 中，每次 @(posedge clk) 后用 = 赋值，用 if/else 实现输入约束。\n'
      '时钟初始为 1；rst_n 在 0..1 ns 为 0，1 ns 释放后始终为 1。\n'
      '默认模板先 #1 等待 DUT 本拍更新；驱动 rready 后再 #1 读取稳定反馈。\n'
      'AR/grant/Bank/MEM 的本次赋值由下一次上升沿采样，不回写到刚结束的采样沿。\n'
      'cycle 是赋值编号，从 0 开始；默认 10 ns 的首次上升沿启动 cycle=0，12 ns 驱动，20 ns 采样。\n'
      'TC 中 rst_n 保持 1；再次拉低会报 MAIN_RESET_FIXED_HIGH_AFTER_1NS，不会被静默改写。\n'
      'clk_low_ns/high_ns 控制时钟，clk_pause_ns 延长下一段低电平，保持每上升沿执行一次。\n'
      '缺失约束或赋了非法值会报错；driver 只检查并复制 TC 值，不隐藏修补输入。\n'
      '每个非复位采样拍，src=0/1/2 必须对应 arready[src]=1，空闲用 src=3、arvalid=0。\n'
      'ar_rd_gnt[b] 仅在 req[b]=1 时可为 1（b 为 Bank 编号）。rready[i]=0 时，DUT 必须令 rvalid[i]=0（i 为 XAU 编号）。\n'
      'issue_data/error 在 ISSUE 成功时预约；mem_due 时用 if 驱动 due_data/error。\n'
      'main_phase 是普通 SV task，不依赖 UVM；make validate 编译，make run 执行检查。\n').format(**cfg)
