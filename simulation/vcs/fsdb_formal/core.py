"""CMDBUF FSDB samples -> readable, pattern-selected native SVA.

Value extraction is entirely NPI-based. DUT configuration is only used to emit
the optional integration wrapper; it never supplies expected waveform values.
"""
import csv
from itertools import groupby
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

FORMAT_LABELS = dict(cycle='逐拍分组', signal='逐拍逐信号', table='按拍查表',
                     packed='逐拍拼接向量', property='可复用 Property', boolean='逐拍布尔条件',
                     channel='逐拍按接口分组', range='按信号合并相邻拍',
                     rom='常量 ROM 查表', generate='Generate 逐拍展开')
FORMATS = tuple(FORMAT_LABELS)

CHANNELS = {
    'RESET': ('rst_n',),
    'AR': ('arvalid', 'araddr', 'src', 'arid', 'ar_port_id', 'arresp', 'arready'),
    'R': ('rready', 'rvalid', 'rdata', 'rid', 'r_port_id', 'raddr', 'rresp'),
    'BANK': ('req', 'arb_addr', 'ar_rd_gnt', 'rw_rd_gnt') + tuple('rd_mem_status[%d]' % i for i in range(8)),
    'MEM': ('mem_rdata', 'pc_err'),
    'PENDING': ('pending_cnt',),
}
CHANNEL_BY_PORT = {name: channel for channel, names in CHANNELS.items() for name in names}
INPUT_NAMES = [signal['name'] for signal in INPUTS]
PORT_WIDTHS = {signal['name']: signal['width'] for signal in SIGNALS}
INPUT_WIDTH = sum(signal['width'] for signal in INPUTS)
OUTPUT_WIDTH = sum(signal['width'] for signal in OUTPUTS)
OUTPUT_NAMES = [signal['name'] for signal in OUTPUTS]
TABLE_FIELDS = ('expected_input', 'expected_output', 'output_mask')
TABLE_WIDTHS = (INPUT_WIDTH, OUTPUT_WIDTH, OUTPUT_WIDTH)
SVA_CLOCK = '@(posedge clk) disable iff (!rst_n)'


def formal_format(value):
    if value not in FORMATS:
        raise ValueError('Formal 写法须为 ' + '、'.join(FORMATS))
    return value

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


def property_lines(kind, label, guard, expression, style, indent='    '):
    if style == 'property':
        return [f'{indent}{label}: {kind} property (trace_equals(',
                f'{indent}    {guard}, ({expression})));']
    if style == 'boolean':
        # An unknown one-bit antecedent does not match the SVA sequence.
        body = f"(({guard} !== 1'b1) || ({expression}))"
    elif style == 'sby':
        body = f'(!{guard} || ({expression}))'
    else:
        body = f'{guard} |-> ({expression})'
    return [f'{indent}{label}: {kind} property ({SVA_CLOCK}',
            f'{indent}    {body});']


def vector(names):
    return '{' + ', '.join(names) + '}'


def comparisons(names, values, packed=False):
    if packed:
        return vector(names) + ' == ' + vector(literal(values[name]) for name in names)
    return ' &&\n            '.join(f'{name} == {literal(values[name])}' for name in names)


def signal_label(name):
    return name.replace('[', '_').replace(']', '')


def cycle_properties(row, guard, suffix, style, property_style):
    values = row['values']
    groups = [('assume', 'M', INPUT_NAMES), ('assert', 'A', row['compared_outputs'])]
    for kind, prefix, names in groups:
        label = prefix + '_' + suffix
        if style == 'signal':
            for name in names:
                yield from property_lines(kind, label+'_'+signal_label(name), guard,
                                          comparisons([name], values), property_style)
        elif style == 'channel':
            channels = {}
            for name in names:
                channels.setdefault(CHANNEL_BY_PORT[name], []).append(name)
            for channel, ports in channels.items():
                yield from property_lines(kind, label+'_'+channel, guard, comparisons(ports, values), property_style)
        else:
            yield from property_lines(kind, label, guard, comparisons(names, values, style == 'packed'), property_style)


def range_properties(segment, selector, suffix, property_style):
    rows = segment['rows']
    groups = [('assume', 'M', INPUT_NAMES), ('assert', 'A', OUTPUT_NAMES)]
    for kind, prefix, names in groups:
        for name in names:
            is_input = kind == 'assume'
            keys = (row['values'][name] if is_input or name in row['compared_outputs'] else None for row in rows)
            cycle = 0
            for value, run in groupby(keys):
                first = cycle
                cycle += sum(1 for _ in run)
                if value is None:
                    continue
                if cycle == first + 1:
                    window = f"trace_cycle == 32'd{first}"
                else:
                    window = f"trace_cycle >= 32'd{first} && trace_cycle < 32'd{cycle}"
                label = f'{prefix}_{suffix}_{signal_label(name)}_C{first:05d}'
                yield from property_lines(kind, label, f'({selector} && {window})',
                                          f'{name} == {literal(value)}', property_style)


def table_values(row):
    # Zero mask bits remove invalid output lanes even when they contain X/Z.
    values = row['values']
    compared = set(row['compared_outputs'])
    expected_input = ''.join(values[name] for name in INPUT_NAMES)
    expected_output = ''.join(values[name] if name in compared else '0'*PORT_WIDTHS[name]
                              for name in OUTPUT_NAMES)
    output_mask = ''.join(('1' if name in compared else '0')*PORT_WIDTHS[name] for name in OUTPUT_NAMES)
    return tuple(literal(bits) for bits in (expected_input, expected_output, output_mask))


def packed_ports():
    return [f'    wire [{INPUT_WIDTH-1}:0] trace_inputs = {vector(INPUT_NAMES)};',
            f'    wire [{OUTPUT_WIDTH-1}:0] trace_outputs = {vector(OUTPUT_NAMES)};']


def packed_comparisons(guard, suffix, columns, property_style, indent='    '):
    input_value, output_value, mask = columns
    return (property_lines('assume', 'M_'+suffix, guard, 'trace_inputs == '+input_value, property_style, indent)
            + property_lines('assert', 'A_'+suffix, guard, f'(trace_outputs & {mask}) == {output_value}',
                             property_style, indent))


def table_properties(segment, selector, suffix, scale, property_style):
    columns = [name+'_'+suffix for name in TABLE_FIELDS]
    lines = [f'    logic [{width-1}:0] {column};' for width, column in zip(TABLE_WIDTHS, columns)]
    lines += ['    always_comb begin', '        case (trace_cycle)']
    for row in segment['rows']:
        lines.append(f"            32'd{row['cycle']}: begin // FSDB posedge {row['time']} {scale}")
        for column, value in zip(columns, table_values(row)):
            lines.append(f'                {column} = {value};')
        lines.append('            end')
    lines.append('            default: begin')
    lines.extend(f"                {column} = '0;" for column in columns)
    lines += ['            end', '        endcase', '    end']
    guard = f"({selector} && trace_cycle < 32'd{segment['cycles']})"
    return lines + packed_comparisons(guard, suffix, columns, property_style)


def rom_properties(segment, selector, suffix, elaborated, property_style):
    cycles = segment['cycles']
    lines = [f"    localparam int unsigned TRACE_LENGTH_{suffix} = 32'd{cycles};"]
    columns = []
    for name, width, values in zip(TABLE_FIELDS, TABLE_WIDTHS, zip(*(table_values(row) for row in segment['rows']))):
        column = name + '_' + suffix
        columns.append(column)
        lines.append(f"    localparam logic [{width-1}:0] {column} [0:TRACE_LENGTH_{suffix}-1] = '{{")
        lines.append('        '+',\n        '.join(values))
        lines.append('    };')
    if elaborated:
        lines += ['    generate',
                  f'        for (genvar c = 0; c < TRACE_LENGTH_{suffix}; c++) begin : G_{suffix}']
        guard = f'({selector} && trace_cycle == c)'
        lines.extend(packed_comparisons(guard, suffix, [column+'[c]' for column in columns],
                                        property_style, indent='            '))
        return lines + ['        end', '    endgenerate']
    guard = f'({selector} && trace_cycle < TRACE_LENGTH_{suffix})'
    return lines + packed_comparisons(guard, suffix, [column+'[trace_cycle]' for column in columns],
                                      property_style)


def native_sva(traces, style='cycle', lowered=False):
    style = formal_format(style)
    maximum = max(s['cycles'] for trace in traces for s in trace['segments'])
    lines = [
        '// Generated from direct NPI FSDB samples; values are sampled BEFORE posedge.',
        '// A pattern identifies one FSDB. reset_segment selects a complete post-reset interval.',
        '// Initialize the DUT using its reset in the formal tool before checking these properties.',
        '// Verify every C_END cover for reachability.',
        '// Compare outputs according to the validity recorded in the waveform.',
        '`default_nettype none', 'module cmdbuf_fsdb_properties (',
        '    input wire [31:0] pattern,', '    input wire [31:0] reset_segment,',
        ',\n'.join(declarations()), ');',
        "    reg [31:0] trace_cycle = 32'd0;",
        '    always @(posedge clk or negedge rst_n)',
        "        if (!rst_n) trace_cycle <= 32'd0;",
        f"        else if (trace_cycle < 32'd{maximum}) trace_cycle <= trace_cycle + 32'd1;",
        '', '// Selectors are symbolic constants within each reset interval.',
    ]
    property_style = 'sby' if lowered else style
    if lowered:
        lines += ['    reg [31:0] trace_prev_pattern, trace_prev_segment;',
                  '    always @(posedge clk) begin',
                  '        trace_prev_pattern <= pattern;',
                  '        trace_prev_segment <= reset_segment;',
                  '    end']
        stable_pattern, stable_segment = 'pattern == trace_prev_pattern', 'reset_segment == trace_prev_segment'
    else:
        stable_pattern, stable_segment = '$stable(pattern)', '$stable(reset_segment)'
    for label, condition in [('M_PATTERN_STABLE', stable_pattern), ('M_SEGMENT_STABLE', stable_segment)]:
        lines.extend(property_lines('assume', label, "(trace_cycle != 32'd0)", condition,
                                    'sby' if lowered else 'cycle'))
    lines += [f'    M_SELECTION: assume property ({SVA_CLOCK}',
              '        ' + ' ||\n        '.join(f"(pattern == 32'd{t['pattern']} && reset_segment < 32'd{len(t['segments'])})"
                                              for t in traces) + ');', '']
    if style == 'property' and not lowered:
        lines += ['    property trace_equals(logic selected, logic expected_ok);',
                  f'        {SVA_CLOCK} selected |-> expected_ok;',
                  '    endproperty', '']
    if style in ('table', 'rom', 'generate'):
        lines.append('    // Packed columns follow the port concatenation order.')
        lines.extend(packed_ports())
    for trace in traces:
        pattern = trace['pattern']
        scale = trace['meta']['scale']
        for segment in trace['segments']:
            sid = segment['segment']
            selector = f"pattern == 32'd{pattern} && reset_segment == 32'd{sid}"
            suffix = f'P{pattern}_S{sid}'
            lines.append(f"// pattern {pattern}, reset_segment {sid}; FSDB release tick {segment['reset_released_at']}; {segment['cycles']} cycles.")
            if style == 'table':
                lines.extend(table_properties(segment, selector, suffix, scale, property_style))
            elif style in ('rom', 'generate'):
                lines.extend(rom_properties(segment, selector, suffix, style == 'generate', property_style))
            elif style == 'range':
                lines.extend(range_properties(segment, selector, suffix, property_style))
            else:
                for row in segment['rows']:
                    guard = f"({selector} && trace_cycle == 32'd{row['cycle']})"
                    lines.append(f"    // FSDB posedge {row['time']} {scale}; snapshot uses the preceding time tick.")
                    lines.extend(cycle_properties(row, guard, f"{suffix}_C{row['cycle']:05d}", style, property_style))
            lines += [f'    C_END_{suffix}: cover property ({SVA_CLOCK}',
                      f"        {selector} && trace_cycle == 32'd{segment['cycles']-1});", '']
    return '\n'.join(lines + ['endmodule', '`default_nettype wire', ''])


def wrapper(instance):
    return '\n'.join(['`default_nettype none', 'module cmdbuf_fsdb_formal_top (',
        '    input wire [31:0] pattern,', '    input wire [31:0] reset_segment,',
        ',\n'.join(declarations(True)), ');', instance,
        '    cmdbuf_fsdb_properties trace_checks (.*);', 'endmodule', '`default_nettype wire', ''])


def export_data(directory, traces, instance, style='cycle'):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    native = native_sva(traces, style)
    (directory/'trace_properties.sv').write_text(native, encoding='utf-8')
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
    segment_count = sum(len(t['segments']) for t in traces)
    expanded = total_cycles - segment_count if style == 'generate' else 0
    return dict(patterns=len(traces), segments=segment_count, cycles=total_cycles,
                input_assumptions=native.count(': assume property') - 3 + expanded,
                output_assertions=native.count(': assert property') + expanded,
                terminal_covers=segment_count,transactions=len(transactions))
