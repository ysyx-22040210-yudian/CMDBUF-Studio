"""Executable WaveJSON for CMDBUF RD. Exact port levels, no implicit cycles.

WaveDrom is a drawing format; this adapter defines a checked executable subset.
Every input lane is mandatory. Unsupported timing syntax fails rather than
being interpreted as a don't-care or silently changed by a protocol driver.
"""
import csv
import html
import io
import json
import re
from pathlib import Path

PORTS = [('clk', 1), ('rst_n', 1), ('arvalid', 1), ('src', 2), ('araddr', 18),
         ('arid', 8), ('ar_port_id', 5), ('arresp', 2), ('rready', 3),
         ('ar_rd_gnt', 8), ('rw_rd_gnt', 8)]
PORTS += [('rd_mem_status[{}]'.format(b), 2) for b in range(8)]
PORTS += [('mem_rdata', 128), ('pc_err', 1)]
WIDTHS = dict(PORTS)
PROTOCOL_PROGRAM_PREFIXES = ('PROTOCOL_V1 ', 'PROTOCOL_V2 ')
META = {'description', 'signal', 'sim', 'require', 'head', 'foot', 'config', 'edge'}


def positive(value, label, maximum, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError('{} must be an integer in {}..{}'.format(label, minimum, maximum))
    return value


def bits(value, width, label):
    if isinstance(value, bool):
        raise ValueError(label + ': use integers, not booleans')
    if isinstance(value, int):
        n = value
    elif isinstance(value, str):
        v = value.lower().replace('_', '')
        if v in ('x', 'z'):
            return v * width
        match = re.fullmatch(r"(?:(\d+))?'([hbd])([0-9a-fxz]+)", v)
        if match:
            if match[1] and int(match[1]) != width:
                raise ValueError(label + ': literal width must match port width')
            base, digits = match[2], match[3]
            v = {'h': '0x', 'b': '0b', 'd': ''}[base] + digits
        if v.startswith('0b') and re.fullmatch('[01xz]+', v[2:]):
            value_bits = v[2:]
            if len(value_bits) > width:
                raise ValueError(label + ': binary value exceeds port width')
            return value_bits.zfill(width)
        if v.startswith('0x') and re.fullmatch('[0-9a-fxz]+', v[2:]) and ('x' in v[2:] or 'z' in v[2:]):
            value_bits = ''.join(c * 4 if c in 'xz' else format(int(c, 16), '04b') for c in v[2:])
            if len(value_bits) > width and any(c != '0' for c in value_bits[:-width]):
                raise ValueError(label + ': hexadecimal value exceeds port width')
            return value_bits[-width:].zfill(width)
        try:
            n = int(v, 0) if v.startswith(('0x', '0b', '0o')) else int(v, 10)
        except ValueError:
            raise ValueError(label + ': bus data must be a numeric value, x or z')
    else:
        raise ValueError(label + ': bus data must be an integer or string')
    if not 0 <= n < (1 << width):
        raise ValueError(label + ': value exceeds {} bits'.format(width))
    return format(n, '0{}b'.format(width))


def flatten(signal):
    if not isinstance(signal, list):
        raise ValueError('signal must be an array')
    lanes = []
    for entry in signal:
        if isinstance(entry, list):
            if not entry or not isinstance(entry[0], str):
                raise ValueError('A WaveDrom group must begin with its name')
            lanes.extend(flatten(entry[1:]))
        elif isinstance(entry, dict):
            if entry:  # Empty objects are visual spacers.
                lanes.append(entry)
        else:
            raise ValueError('signal entries must be lanes, groups or {} spacers')
    return lanes


def compile_wave(cfg):
    if isinstance(cfg, dict) and cfg.get('sim', {}).get('mode') == 'protocol':
        from protocol_adapter import compile_protocol
        return compile_protocol(cfg)
    if not isinstance(cfg, dict) or set(cfg) - META:
        raise ValueError('Unknown WaveDrom case-level field')
    sim = cfg.get('sim', {})
    if not isinstance(sim, dict) or set(sim) - {'tick_ps', 'clock_skew_ps', 'finish', 'values'}:
        raise ValueError('sim supports tick_ps, clock_skew_ps, finish and values')
    symbols = sim.get('values', {})
    if not isinstance(symbols, dict) or any(not isinstance(k, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', k) for k in symbols):
        raise ValueError('sim.values must map identifier names to numeric literals')
    tick = positive(sim.get('tick_ps', 10000), 'tick_ps', 1000000000, 4)
    skew = positive(sim.get('clock_skew_ps', 1), 'clock_skew_ps', tick // 2 - 1)
    finish = sim.get('finish', 'require_empty')
    if finish not in ('require_empty', 'allow_pending'):
        raise ValueError('finish must be require_empty or allow_pending')
    lanes, expanded, scheduled, durations = {}, {}, {}, set()
    for lane in flatten(cfg.get('signal')):
        if set(lane) - {'name', 'wave', 'data', 'period', 'phase', 'node'}:
            raise ValueError('Unsupported WaveDrom lane field: ' + str(lane))
        name = lane.get('name')
        if name not in WIDTHS:
            raise ValueError('Not a DUT input port: ' + str(name))
        if name in lanes:
            raise ValueError('Duplicate input lane: ' + name)
        if lane.get('phase', 0) != 0:
            raise ValueError(name + ': executable lanes require phase=0; use explicit 0/1 cells or period for edge placement')
        period = positive(lane.get('period', 1), name + '.period', 100000)
        wave = lane.get('wave')
        if not isinstance(wave, str) or not 1 <= len(wave) <= 100000:
            raise ValueError(name + ': wave must be a nonempty string, max 100000 cells')
        if re.search(r'[^01.xz=23456789pPnNhHlL]', wave):
            raise ValueError(name + ': unsupported wave symbol (spaces, gaps | and subcycles <> are not executable)')
        data = lane.get('data', [])
        if isinstance(data, str):
            data = data.split()
        if not isinstance(data, list):
            raise ValueError(name + ': data must be an array or whitespace-separated string')
        duration = len(wave) * period * tick
        if duration > 1000000000000:
            raise ValueError('WaveDrom duration exceeds 1 second')
        durations.add(duration)
        values, previous_symbol, previous_bits, used = [], None, None, 0
        for i, symbol in enumerate(wave):
            label = '{} cell {}'.format(name, i)
            if symbol == '.':
                if previous_symbol is None:
                    raise ValueError(label + ': first cell cannot be .')
                kind, value = previous_symbol, previous_bits
            elif symbol in '=23456789':
                if used >= len(data):
                    raise ValueError(label + ': missing data label')
                literal = symbols.get(data[used], data[used]) if isinstance(data[used], str) else data[used]
                kind, value = '=', bits(literal, WIDTHS[name], label)
                used += 1
            elif symbol in 'pPnN':
                if name != 'clk':
                    raise ValueError(label + ': pulse symbols are only supported for clk')
                kind, value = symbol.lower(), None
            else:
                kind = {'h': '1', 'H': '1', 'l': '0', 'L': '0'}.get(symbol, symbol)
                value = kind * WIDTHS[name]
            if name in ('clk', 'rst_n') and (value is not None and set(value) - {'0', '1'}):
                raise ValueError(label + ': clk and rst_n must be known 0/1')
            if name == 'clk' and kind not in ('0', '1', 'p', 'n', '='):
                raise ValueError(label + ': unsupported clock state')
            previous_symbol, previous_bits = kind, value
            start = i * period * tick
            if kind in ('p', 'n'):
                if period * tick % 2:
                    raise ValueError(label + ': half period must be an integer number of ps')
                first, second = ('1', '0') if kind == 'p' else ('0', '1')
                scheduled.setdefault(start + skew, {})[name] = first
                scheduled.setdefault(start + period * tick // 2 + skew, {})[name] = second
                values.append(kind)
            else:
                scheduled.setdefault(start + (skew if name == 'clk' else 0), {})[name] = value
                values.append(value)
        if used != len(data):
            raise ValueError(name + ': unused data labels')
        lanes[name], expanded[name] = lane, values
    if set(lanes) != set(WIDTHS):
        raise ValueError('Missing input lanes: ' + ', '.join(sorted(set(WIDTHS) - set(lanes))))
    if len(durations) != 1:
        raise ValueError('All input lanes must cover the same duration (len(wave) * period)')
    duration = durations.pop()
    state = {name: '0' * width for name, width in PORTS}
    frames, edges = [], []
    for t in sorted(scheduled):
        before = state['clk']
        state.update(scheduled[t])
        if state['clk'] == '1' and before == '0':
            edge = dict(state, time_ps=t, cycle=len(edges), column=(t-skew)//tick)
            edges.append(edge)
            if state['rst_n'] == '1' and state['arvalid'] == '1':
                if state['src'] not in ('00', '01', '10'):
                    raise ValueError('src must be 0..2 on active AR at column {}'.format(edge['column']))
                if state['arresp'] not in ('00', '11'):
                    raise ValueError('arresp must be 0 or 3 on active AR at column {}'.format(edge['column']))
        # Preserve every planned boundary, even if the value did not change.
        frames.append(dict(state, time_ps=t))
    frames.append(dict(state, time_ps=duration + skew))
    return {'tick_ps': tick, 'clock_skew_ps': skew, 'duration_ps': duration,
            'finish': finish, 'frames': frames, 'samples': edges, 'columns': duration // tick,
            'expanded': expanded, 'input_ports': [name for name, _ in PORTS]}


def make_program(cfg):
    if cfg.get('sim', {}).get('mode') == 'protocol':
        from protocol_adapter import make_program as protocol_program
        return protocol_program(cfg)
    plan = compile_wave(cfg)
    header = 'WAVEDROM_V1 {} {} {} {} {}'.format(len(plan['frames']), plan['tick_ps'],
                plan['duration_ps'], plan['clock_skew_ps'], int(plan['finish'] == 'require_empty'))
    lines = [header]
    for frame in plan['frames']:
        lines.append(str(frame['time_ps']) + ' ' + ' '.join(frame[name] for name, _ in PORTS))
    return '\n'.join(lines) + '\n'


def sample_csv(plan):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=['cycle', 'column', 'time_ps'] + plan['input_ports'])
    writer.writeheader(); writer.writerows(plan['samples'])
    return stream.getvalue()


def verify_execution(cfg, directory, program):
    """Verify all scheduled levels and every sampled edge against simulator logs."""
    if program.startswith(PROTOCOL_PROGRAM_PREFIXES):
        from protocol_adapter import verify_execution as verify_protocol
        return verify_protocol(cfg, directory, program)
    # Audit the archived instruction stream, including on replay. A later edit
    # to the WaveJSON compiler must not change the intent of a saved run.
    rows = program.splitlines()
    header = rows[0].split()
    if len(header) != 6 or header[0] != 'WAVEDROM_V1':
        raise ValueError('Unknown archived WaveDrom program format')
    _, count, tick, duration, skew, empty = header
    frames, samples, last_clk = [], [], '0'
    for row in rows[1:]:
        values = row.split()
        if len(values) != len(PORTS) + 1:
            raise ValueError('Malformed archived WaveDrom frame')
        frame = dict(zip(WIDTHS, values[1:]), time_ps=int(values[0]))
        frames.append(frame)
        if frame['clk'] == '1' and last_clk == '0':
            samples.append(dict(frame, cycle=len(samples), column=(frame['time_ps']-int(skew))//int(tick)))
        last_clk = frame['clk']
    if len(frames) != int(count):
        raise ValueError('Archived WaveDrom frame count mismatch')
    plan = {'frames': frames, 'samples': samples, 'input_ports': [n for n, _ in PORTS],
            'finish': 'require_empty' if int(empty) else 'allow_pending'}
    root = Path(directory)
    failures = []
    for filename, expected, columns in (
        ('driven.csv', plan['frames'], ['time_ps'] + plan['input_ports']),
        ('samples.csv', plan['samples'], ['cycle', 'column', 'time_ps'] + plan['input_ports'])):
        path = root / filename
        if not path.exists():
            failures.append('missing ' + filename); continue
        with path.open() as stream:
            observed_rows = list(csv.DictReader(stream))
        if len(observed_rows) != len(expected):
            failures.append('{} rows: expected {}, got {}'.format(filename, len(expected), len(observed_rows)))
            continue
        for i, (observed, intended) in enumerate(zip(observed_rows, expected)):
            differences = [key for key in columns if observed.get(key) != str(intended[key])]
            if differences:
                failures.append('{} row {} differs: {}'.format(filename, i, ','.join(differences)))
                break
    return {'status': 'PASS' if not failures else 'FAIL', 'failures': failures,
            'frames': len(plan['frames']), 'clock_edges': len(plan['samples']),
            'all_input_lanes': plan['input_ports'], 'finish': plan['finish']}


def explain(cfg):
    p = compile_wave(cfg)
    if p.get('mode') == 'protocol':
        return ('约束驱动 WaveDrom：{} 列；{} 个请求标记。\n固定输入：{}\n自动输入：{}\n'
                'ar_offer 每个数据标记提交一笔；ar_send 控制发送窗口。请求仅在实际握手后出队。\n'
                'Bank 基线仅补成功当拍忙位；MEM 绑定实际 ISSUE +4 上升沿；复位取消队列和返回日历。\n'
                '固定值冲突、超时、未知规则均失败；finish={}，不自动追加时钟。').format(
                    p['columns'],len(p['requests']),','.join(p['fixed']),','.join(p['owned']),p['finish'])
    return ('WaveDrom：{} 列，每列 {} ps，共 {} 个实际上升沿。\n'
            'clk 按波形执行，时钟沿统一滞后输入边界 {} ps；cycle 是上升沿序号，column 是图上的列号。\n'
            '所有 {} 路输入显式给出，不修改 arvalid/status/MEM，不自动复位或排空。\n'
            'finish={}；复位会取消在途事务，取消数单独记录。').format(
                p['columns'], p['tick_ps'], len(p['samples']), p['clock_skew_ps'], len(PORTS), p['finish'])


def write_preview(cfg, path, validate=True):
    """Standalone offline editor: real WaveDrom renderer, strict JSON download."""
    if validate:
        compile_wave(cfg)
    here = Path(__file__).resolve().parent
    renderer = (here / 'vendor/wavedrom/wavedrom.min.js').read_text(encoding='utf-8')
    skin = (here / 'vendor/wavedrom/default.js').read_text(encoding='utf-8')
    if all(isinstance(lane, dict) for lane in cfg['signal']):
        # Put each wave string on one row so users can align columns by eye.
        rows = []
        for key, value in cfg.items():
            if key == 'signal':
                lanes = [json.dumps(lane, ensure_ascii=False) for lane in value]
                rows.append('  "signal": [\n    ' + ',\n    '.join(lanes) + '\n  ]')
            else:
                rows.append('  ' + json.dumps(key) + ': ' + json.dumps(value, ensure_ascii=False))
        source_text = '{\n' + ',\n'.join(rows) + '\n}'
    else:
        source_text = json.dumps(cfg, ensure_ascii=False, indent=2)
    raw = html.escape(source_text)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>WaveDrom 用例编辑</title>
<style>body{font:16px system-ui;margin:24px;color:#162c40}textarea{width:100%%;height:350px;font:14px monospace}
button{padding:8px 18px;margin:8px 8px 8px 0}#diagram{overflow:auto;border:1px solid #ccd4dc;padding:12px}
#error{color:#b42318;white-space:pre-wrap}p{line-height:1.55}</style>
<h1>WaveDrom 定向用例</h1><p>编辑 JSON 后点击预览。下载文件并放入 cases 目录后，使用 make validate 和 make run 执行。
每列是一个 tick；时钟沿在仿真中统一滞后 clock_skew_ps。预览仅负责绘图，执行合法性以 make validate 和运行检查为准。</p>
<textarea id="source" spellcheck="false" wrap="off">%s</textarea><br>
<button onclick="draw()">更新 WaveDrom 预览</button><button onclick="save()">下载用例 JSON</button>
<span id="error"></span><div id="diagram"></div><pre id="values"></pre>
<script>%s</script><script>%s</script>
<script>
function config(){return JSON.parse(document.getElementById('source').value);}
function draw(){try{const cfg=config();document.getElementById('diagram').innerHTML='<div id="WaveDrom_Display_0"></div>';
WaveDrom.RenderWaveForm(0,cfg,'WaveDrom_Display_');document.getElementById('error').textContent='';
document.getElementById('values').textContent='sim.values = '+JSON.stringify((cfg.sim||{}).values||{},null,2);}
catch(e){document.getElementById('error').textContent=String(e);}}
function save(){try{const cfg=config();const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(cfg,null,2)+'\\n'],{type:'application/json'}));
a.download='my_wavedrom.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}catch(e){document.getElementById('error').textContent=String(e);}}
draw();</script></html>''' % (raw, skin, renderer)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(page, encoding='utf-8')
