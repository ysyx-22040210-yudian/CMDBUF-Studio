"""CMDBUF FSDB samples -> readable, pattern-selected native SVA.

Value extraction is entirely NPI-based. DUT configuration is only used to emit
the optional integration wrapper; it never supplies expected waveform values.
"""
import csv
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dut_config import PORTS

SIGNALS = []
for name, direction, width in PORTS:
    if name == 'rd_mem_status':
        SIGNALS.extend(dict(name='rd_mem_status[%d]' % i, direction=direction, width=2) for i in range(8))
    else:
        SIGNALS.append(dict(name=name, direction=direction, width=width))
INPUTS = [s for s in SIGNALS if s['direction'] == 'input' and s['name'] != 'clk']
OUTPUTS = [s for s in SIGNALS if s['direction'] == 'output']
R_PAYLOAD = {'rdata', 'rid', 'r_port_id', 'raddr', 'rresp'}


def mapping(scope, overrides=None):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$.[\]-]*', scope):
        raise ValueError('无效的 FSDB 实例路径')
    result = []
    overrides = overrides or {}
    if set(overrides) - {s['name'] for s in SIGNALS}:
        raise ValueError('映射中出现未知的 CMDBUF 信号')
    for s in SIGNALS:
        path = overrides.get(s['name'], scope + '.' + s['name'])
        if not isinstance(path, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$.[\]:-]*', path):
            raise ValueError('无效的信号路径: ' + str(path))
        result.append(dict(s, path=path))
    return result


def write_mapping(path, signals):
    Path(path).write_text(''.join('{name}\t{width}\t{path}\n'.format(**s) for s in signals), encoding='utf-8')


def number(value, label, lo=0, hi=2147483647):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError('%s 必须为 %d..%d 的整数' % (label, lo, hi))
    return value


def output_names(values):
    # Preserve the project's validity contract. Every value is extracted, but
    # payloads have no specified meaning when their valid/request is zero.
    return [s['name'] for s in OUTPUTS
            if (s['name'] not in R_PAYLOAD or int(values['rvalid'], 2) != 0)
            and (s['name'] != 'arb_addr' or int(values['req'], 2) != 0)]


def parse_trace(path, source):
    records = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    meta = records[0]
    if meta.get('type') != 'meta':
        raise ValueError('NPI 输出缺少元信息')
    scale = re.fullmatch(r'(\d+)\s*(fs|ps|ns|us|ms|s)', meta['scale'])
    if not scale:
        raise ValueError('无法识别 FSDB 时间单位: ' + meta['scale'])
    meta['tick_fs'] = int(scale.group(1)) * {'fs':1, 'ps':1000, 'ns':10**6, 'us':10**9, 'ms':10**12, 's':10**15}[scale.group(2)]
    gaps = [tuple(map(int, pair)) for pair in re.findall(r'\(\s*(\d+)\s+(\d+)\s*\)', meta.get('dump_off', ''))]
    if meta.get('dump_off', '').strip() and not gaps:
        raise ValueError('无法解释 FSDB dump-off 区间；停止生成以防缺失数据')
    resets = [r for r in records if r['type'] == 'reset']
    releases = []
    last = None
    asserted_at = None
    for r in resets:
        if r['value'] == '0':
            asserted_at = r['time']
        elif r['value'] == '1' and last != '1':
            if last != '0' or asserted_at is None:
                raise ValueError('波形中存在没有可见复位前序的释放，无法建立复位后初态')
            releases.append(dict(reset_asserted_at=asserted_at, reset_released_at=r['time'], rows=[]))
        last = r['value']
    if not releases:
        raise ValueError('FSDB 中没有完整的低有效复位及释放过程')
    for row in (r for r in records if r['type'] == 'edge'):
        values = row['values']; t = row['time']
        if values.get('rst_n') == '0':
            continue
        if values.get('rst_n') != '1':
            raise ValueError('时钟采样沿上的复位含 X/Z')
        episodes = [s for s in releases if s['reset_released_at'] < t]
        if not episodes:
            raise ValueError('出现没有可见复位前序的工作周期')
        episode = episodes[-1]
        if any(a < t and b > episode['reset_asserted_at'] for a,b in gaps):
            raise ValueError('复位及工作区间存在 dump-off，不能用缺失的跳变生成 Formal')
        for signal in SIGNALS:
            v = values.get(signal['name'])
            if not isinstance(v, str) or len(v) != signal['width'] or re.search(r'[^01xz]', v):
                raise ValueError('信号值缺失或位宽不匹配: ' + signal['name'])
        for s in INPUTS:
            if re.search('[xz]', values[s['name']]):
                raise ValueError('复位后输入含 X/Z，无法生成确定输入假设: %s @ %s' % (s['name'], t))
        for s in OUTPUTS:
            if s['name'] not in R_PAYLOAD | {'arb_addr'} and re.search('[xz]', values[s['name']]):
                raise ValueError('输出控制信号含 X/Z: %s @ %s' % (s['name'], t))
        compared = output_names(values)
        for name in compared:
            if re.search('[xz]', values[name]):
                raise ValueError('有效输出负载含 X/Z: %s @ %s' % (name, t))
        episode['rows'].append(dict(cycle=len(episode['rows']), time=t, values=values, compared_outputs=compared))
    segments = [s for s in releases if s['rows']]
    if not segments:
        raise ValueError('复位释放后没有可提取的时钟上升沿')
    for i,s in enumerate(segments):
        s['segment'] = i
        s['cycles'] = len(s['rows'])
    return dict(pattern=number(source['pattern'], 'pattern'), name=source['name'],
                source=source, meta=meta, segments=segments)


def literal(value):
    return "%d'h%s" % (len(value), format(int(value, 2), '0%dX' % ((len(value)+3)//4)))


def declarations(directions=False):
    result = []
    for name, direction, width in PORTS:
        role = direction if directions else 'input'
        if name == 'rd_mem_status':
            result.append('    %s wire [1:0] rd_mem_status [7:0]' % role)
        else:
            result.append('    %s wire %s%s' % (role, '' if width==1 else '[%d:0] ' % (width-1), name))
    return result


def native_sva(traces):
    maximum = max(s['cycles'] for trace in traces for s in trace['segments'])
    lines = [
        '// Generated from direct NPI FSDB samples; values are sampled BEFORE posedge.',
        '// A pattern identifies one FSDB. reset_segment selects a complete post-reset interval.',
        '// Initialize the DUT using its reset in the formal tool before checking these properties.',
        '// disable iff is not initialization. Verify every C_END cover to detect vacuity.',
        '// Unspecified ARB/R payloads on invalid cycles are recorded but are not asserted.',
        '`default_nettype none', 'module cmdbuf_fsdb_properties (',
        '    input wire [31:0] pattern,', '    input wire [31:0] reset_segment,',
        ',\n'.join(declarations()), ');',
        "    reg [31:0] trace_cycle = 32'd0;",
        '    always @(posedge clk or negedge rst_n)',
        "        if (!rst_n) trace_cycle <= 32'd0;",
        "        else if (trace_cycle < 32'd%d) trace_cycle <= trace_cycle + 32'd1;" % maximum,
        '', '// Selectors are symbolic constants within each reset interval.',
        '    M_PATTERN_STABLE: assume property (@(posedge clk) disable iff (!rst_n)',
        "        (trace_cycle != 32'd0) |-> $stable(pattern));",
        '    M_SEGMENT_STABLE: assume property (@(posedge clk) disable iff (!rst_n)',
        "        (trace_cycle != 32'd0) |-> $stable(reset_segment));",
        '    M_SELECTION: assume property (@(posedge clk) disable iff (!rst_n)',
        '        ' + ' ||\n        '.join("(pattern == 32'd%d && reset_segment < 32'd%d)" % (t['pattern'],len(t['segments'])) for t in traces) + ');', '']
    for trace in traces:
        pattern = trace['pattern']
        for segment in trace['segments']:
            sid = segment['segment']
            lines.append('// pattern %d, reset_segment %d; FSDB release tick %d; %d cycles.' %
                         (pattern, sid, segment['reset_released_at'], segment['cycles']))
            for row in segment['rows']:
                guard = "(pattern == 32'd%d && reset_segment == 32'd%d && trace_cycle == 32'd%d)" % (pattern,sid,row['cycle'])
                suffix = 'P%d_S%d_C%05d' % (pattern,sid,row['cycle'])
                lines.append('    // FSDB posedge %d %s; snapshot uses the preceding time tick.' % (row['time'], trace['meta']['scale']))
                for kind, label, names in [('assume','M', [s['name'] for s in INPUTS]), ('assert','A', row['compared_outputs'])]:
                    conditions = ['%s == %s' % (name,literal(row['values'][name])) for name in names]
                    lines += ['    %s_%s: %s property (@(posedge clk) disable iff (!rst_n)' % (label,suffix,kind),
                              '        %s |-> (' % guard,
                              '            ' + ' &&\n            '.join(conditions) + '));']
            lines += ['    C_END_P%d_S%d: cover property (@(posedge clk) disable iff (!rst_n)' % (pattern,sid),
                      "        pattern == 32'd%d && reset_segment == 32'd%d && trace_cycle == 32'd%d);" % (pattern,sid,segment['cycles']-1), '']
    return '\n'.join(lines + ['endmodule', '`default_nettype wire', ''])


def wrapper(instance):
    return '\n'.join(['`default_nettype none', 'module cmdbuf_fsdb_formal_top (',
        '    input wire [31:0] pattern,', '    input wire [31:0] reset_segment,',
        ',\n'.join(declarations(True)), ');', instance,
        '    cmdbuf_fsdb_properties trace_checks (.*);', 'endmodule', '`default_nettype wire', ''])


def export_data(directory, traces, instance):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    (directory/'trace_properties.sv').write_text(native_sva(traces), encoding='utf-8')
    (directory/'formal_top.sv').write_text(wrapper(instance), encoding='utf-8')
    transactions = []
    total_cycles = 0
    fields = ['pattern', 'reset_segment', 'cycle', 'fsdb_time', 'timescale'] + [s['name'] for s in SIGNALS]
    with (directory/'samples.csv').open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fields); writer.writeheader()
        for t in traces:
            for s in t['segments']:
                for r in s['rows']:
                    v=r['values']; total_cycles+=1
                    writer.writerow(dict(pattern=t['pattern'], reset_segment=s['segment'], cycle=r['cycle'],
                                         fsdb_time=r['time'], timescale=t['meta']['scale'], **v))
                    kinds=[]
                    if int(v['arvalid'],2) and int(v['src'],2)<3 and (int(v['arready'],2)>>int(v['src'],2))&1:
                        kinds.append('AR')
                    if int(v['req'],2)&int(v['ar_rd_gnt'],2)&int(v['rw_rd_gnt'],2): kinds.append('ISSUE')
                    if int(v['rvalid'],2)&int(v['rready'],2): kinds.append('R')
                    transactions.extend(dict(pattern=t['pattern'],reset_segment=s['segment'],cycle=r['cycle'],
                                             fsdb_time=r['time'],kind=kind,values=v) for kind in kinds)
    (directory/'transactions.json').write_text(json.dumps(transactions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (directory/'trace.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(patterns=len(traces), segments=sum(len(t['segments']) for t in traces), cycles=total_cycles,
                input_assumptions=total_cycles, output_assertions=total_cycles,
                terminal_covers=sum(len(t['segments']) for t in traces),transactions=len(transactions))
