"""SystemVerilog TC discovery, archival and factory generation (Python 3.6)."""
import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TC_DIR = HERE / 'tc'


def align_template_delays(code):
    """Migrate only the two standalone waits used by older main_phase templates."""
    from sva_source import mask_sv
    clean = mask_sv(code)
    waits = (
        r'@\s*\(\s*posedge\s+clk\s*\)\s*;\s*(#\s*1\s*;)(?=\s*begin_cycle\s*\()',
        r'\bdrive_control\s*\(\s*\)\s*;\s*(#\s*1\s*;)(?=\s*sample_context\s*\()',
    )
    for pattern in waits:
        for match in re.finditer(pattern, clean):
            a, b = match.span(1)
            code = code[:a] + re.sub(r'[^\r\n]', ' ', code[a:b]) + code[b:]
    return code


def load(path, expected_sha256=None):
    path = Path(path)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', path.stem):
        raise ValueError('SV TC filename must be an identifier')
    raw = path.read_bytes()
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError('TC source changed while preparing the build: ' + str(path))
    code = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    if not re.search(r'\bclass\s+' + re.escape(path.stem) + r'\s+extends\s+cmdbuf_main_tc\s*;', code):
        raise ValueError('SV TC must declare class {} extends cmdbuf_main_tc;'.format(path.stem))
    return {'format':'sv_main_phase', 'name':path.stem, 'source':code,
            'source_sha256':hashlib.sha256(code.encode('utf-8')).hexdigest(),
            'description':next((s[3:].strip() for s in code.splitlines() if s.startswith('// ')), '')}


def program(cfg, seed, cycles=None):
    if cycles is not None and not 1 <= cycles <= 1000000:
        raise ValueError('CYCLES must be 1..1000000')
    return 'MAIN_PHASE_V1 {} {} {}\n'.format(cfg['name'], seed, cycles or 0)


def generate_factory(destination, source_hashes=None):
    """Compile all local TC classes once; run-time selection does not rebuild RTL."""
    directory = Path(destination)
    archive = directory / 'tc_sources'
    archive.mkdir(exist_ok=True)
    folders = (TC_DIR, HERE/'tests'/'main_phase')
    paths = [path for folder in folders for path in sorted(folder.glob('*.sv'))]
    if source_hashes is None:
        entries = [load(path) for path in paths]
    else:
        root = HERE.parents[1]
        expected = {root/name: sha for name, sha in source_hashes.items()
                    if (root/name).parent in folders and name.endswith('.sv')}
        if set(paths) != set(expected):
            raise RuntimeError('TC file list changed while preparing the build')
        entries = [load(path, expected[path]) for path in paths]
    if len({c['name'] for c in entries})!=len(entries):
        raise ValueError('Duplicate SV TC class name in cases and test fixtures')
    lines = []
    for c in entries:
        p = archive / (c['name'] + '.sv')
        p.write_text(c['source'], encoding='utf-8')
        compiled_source = align_template_delays(c['source'])
        compiled = p
        if compiled_source != c['source']:
            compiled = directory / (c['name'] + '.sv')
            compiled.write_text(compiled_source, encoding='utf-8')
        lines.append('`include "{}"'.format(compiled.as_posix()))
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
    code = re.sub(r'\bclass\s+'+source['name']+r'\b', 'class '+name, align_template_delays(source['source']), count=1)
    target = TC_DIR / (name+'.sv')
    with target.open('x',encoding='utf-8') as stream: stream.write(code)
    return target


def explain(cfg):
    return ('SV main_phase TC: {name}\n'
      '用户在 main_phase 中，每次 @(posedge clk) 后用 = 赋值，用 if/else 实现输入约束。\n'
      '时钟初始为 1；rst_n 在 0..1 ns 为 0，1 ns 释放后始终为 1。\n'
      'begin_cycle() 等待本沿 DUT 更新，sample_context() 等待 rready 的组合反馈稳定。两步都不增加仿真时间。\n'
      'AR/grant/Bank/MEM 的本次赋值由下一次上升沿采样，不回写到刚结束的采样沿。\n'
      'cycle 从 0 开始；默认首次上升沿 10 ns 启动并驱动 cycle=0，20 ns 采样。\n'
      '当前模板在 begin_cycle() 和 sample_context() 前无需 #1。旧模板的这两处固定等待在编译副本中迁移。\n'
      'TC 中 rst_n 保持 1；再次拉低会报 MAIN_RESET_FIXED_HIGH_AFTER_1NS，不会被静默改写。\n'
      'clk_low_ns/high_ns 控制时钟，clk_pause_ns 延长下一段低电平，保持每上升沿执行一次。\n'
      '缺失约束或赋了非法值会报错；driver 只检查并复制 TC 值，不隐藏修补输入。\n'
      '每个非复位采样拍，src=0/1/2 必须对应 arready[src]=1，空闲用 src=3、arvalid=0。\n'
      'ar_rd_gnt[b] 仅在 req[b]=1 时可为 1（b 为 Bank 编号）。rready[i]=0 时，DUT 必须令 rvalid[i]=0（i 为 XAU 编号）。\n'
      'issue_data/error 在 ISSUE 成功时预约；mem_due 时用 if 驱动 due_data/error。\n'
      'main_phase 是普通 SV task，不依赖 UVM；make validate 编译，make run 执行检查。\n').format(**cfg)
