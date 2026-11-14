"""Generate cover witnesses with the bundled SBY, then prepare exact pin replay."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import eda
import dut_config
import execution
import functional_properties as props
from fsdb_formal.engine import package_rtl
from fsdb_formal import toolchain


ENGINE = Path(__file__).relative_to(eda.ROOT).as_posix()
TRACE_PATH = re.compile(r'solver/engine_0/(trace[0-9]+)\.vcd')


def safe_id(identifier):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier or ''):
        raise ValueError('无效的 Formal 功能覆盖任务编号')
    return identifier


def folder(identifier):
    return eda.OUT / 'functional_cover' / safe_id(identifier)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ports():
    for name, _, width in dut_config.PORTS:
        if name == 'rst_n':
            yield 'reg rst_n = 0;'
        if isinstance(width, str):
            yield 'wire [1:0] rd_mem_status [7:0];'
        elif name not in ('clk', 'rst_n'):
            yield 'wire {}{};'.format('' if width == 1 else '[{}:0] '.format(width - 1), name)


def formal_top(dut, builtin, user, action):
    properties = builtin + user
    lines = ['`default_nettype none',
             'module {} (input wire clk, input wire [{}:0] fc_stimulus);'.format(props.TOP, props.WIDTH - 1)]
    lines.extend(ports())
    lines += [
        'reg fc_active = 0;',
        'always @(posedge clk) begin',
        "    rst_n <= 1'b1;",
        '    fc_active <= rst_n;',
        'end',
        'assign {} = fc_active ? fc_stimulus : {};'.format(props.input_concat(), props.sv_literal(props.packed({'src': 3}))),
        dut['instance'],
    ]
    lines.extend(props.helpers(max([1] + [p['history'] for p in properties])))
    if action == 'cover':
        lines += [
            'reg [4:0] fc_outstanding;',
            'reg [3:0] fc_mem_due;',
            'reg [6:0] fc_quiet;',
            'wire fc_idle = fc_active && fc_outstanding == 0 && fc_mem_due == 0 &&',
            '               !arvalid && req == 0 && rvalid == 0 && pending_cnt == 0;',
            'always @(posedge clk) begin',
            '    if (!rst_n) begin',
            '        fc_outstanding <= 0; fc_mem_due <= 0; fc_quiet <= 0;',
            '    end else begin',
            "        fc_outstanding <= fc_outstanding + {4'b0,ar_fire} - {4'b0,r_fire};",
            '        fc_mem_due <= {fc_mem_due[2:0],issue_fire};',
            '        fc_quiet <= {fc_quiet[5:0],fc_idle};',
            '    end',
            'end',
            props.statement('FC_REPLAY_CAPACITY', 'assert', "fc_outstanding <= 5'd9 && (!r_fire || fc_outstanding != 0)") + ';',
        ]
    for index, p in enumerate(properties):
        name, kind, expr = p['name'], p['kind'], p['predicate']
        user_name = 'FC_C_' if kind == 'cover' else 'FC_USER_'
        label = name if index < len(builtin) else user_name + name
        if kind == 'cover' and action == 'bmc':
            continue
        if label == 'E06_SUCCESS_BUSY':
            lines += ['for (genvar bank_env=0; bank_env<8; bank_env=bank_env+1) begin : env_bank',
                      '    ' + props.statement(label, kind, expr) + ';', 'end']
        elif kind == 'cover' and action == 'cover':
            # Complete the witness under the same assumptions as the prefix.
            # Idle here means no outstanding transactions; free pin levels
            # still come from the solver, including user-constrained rready.
            seen = 'fc_seen_' + name
            lines += ['reg {};'.format(seen), 'always @(posedge clk)',
                      '    if (!rst_n) {} <= 0;'.format(seen),
                      '    else if ({}) {} <= 1;'.format(expr, seen),
                      props.statement(label, kind, 'fc_quiet[6] && fc_idle && ({} || ({}))'.format(seen, expr)) + ';']
        else:
            lines.append(props.statement(label, kind, expr) + ';')
    lines += ['endmodule', '`default_nettype wire']
    return '\n'.join(lines) + '\n'


def snapshot(directory, action, depth, timeout):
    inventory = eda.contracts.audit()
    dut_path = eda.HERE / 'dut.json'
    dut_sha = eda.digest(dut_path)
    dut = dut_config.load()
    rtl = {path.relative_to(eda.ROOT).as_posix(): path.read_bytes()
           for path in dut_config.source_files(dut)}
    authored = props.USER_FILE.read_bytes()
    functional = props.manifest(authored)
    if action == 'cover' and not props.cover_names(functional):
        raise ValueError('请先加入至少一条带名称的 cover property')
    checker = eda.ROOT / eda.PROJECT['checker']
    builtin_bytes = checker.read_bytes()
    if eda.digest(dut_path) != dut_sha or digest(builtin_bytes) != inventory['checker']['sha256']:
        raise RuntimeError('DUT 配置或基线端口检查器在取快照期间改变，请重新运行')
    builtin = props.builtin_properties(builtin_bytes.decode('utf-8-sig'))
    if set(inventory['checker'].get('added_assumptions', [])) - {p['name'] for p in builtin}:
        raise ValueError('旧端口检查器含有不兼容的自定义 assume；请将自定义约束迁到功能覆盖率属性文件')
    if action == 'bmc' and not any(p['kind'] == 'assert' for p in builtin + functional['properties']):
        raise ValueError('当前属性没有可执行的 assert')
    names, includes = package_rtl(directory, dut, rtl)
    (directory/'files.f').unlink()  # This campaign uses the Slang command file below.
    for relative, data in rtl.items():
        if digest((eda.ROOT/relative).read_bytes()) != digest(data):
            raise RuntimeError('RTL 在取快照期间改变 ' + relative)
    (directory/'formal_top.sv').write_text(formal_top(dut, builtin, functional['properties'], action), encoding='utf-8')
    (directory/'user_properties.sv').write_bytes(authored)
    (directory/'port_contract.sv').write_bytes(builtin_bytes)
    opts = ['-D', 'SYNTHESIS']
    for inc in includes:
        opts.extend(['-I', inc])
    for define in dut['defines']:
        opts.extend(['-D', define])
    opts += names + ['formal_top.sv']
    (directory/'slang.f').write_text('\n'.join(json.dumps(word, ensure_ascii=False) for word in opts)+'\n', encoding='utf-8')
    script = ('read_slang --top {top} --no-implicit-memories -f slang.f\n'
              'prep -top {top}\nasync2sync\ndffunmap\nchformal -lower\n').format(top=props.TOP)
    if action == 'cover':
        # Search only passing prefixes. A separate BMC keeps these as
        # assertions and can expose violations instead of ruling them out.
        script += 'chformal -assert -assert2assume\n'
    options = ['mode ' + action, 'depth ' + str(depth + 2)]
    if timeout:
        options.append('timeout ' + str(timeout))
    (directory/'run.sby').write_text(
        '[options]\n' + '\n'.join(options) + '\n\n[engines]\nsmtbmc yices\n\n[script]\n'
        + script + '\n[files]\nrtl_snapshot\nformal_top.sv\nslang.f\n', encoding='utf-8')
    return dict(version=1, id=directory.name, action=action, max_body_cycles=depth, timeout=timeout,
                dut=dut, functional=functional, contract_revision=inventory['revision'],
                builtin=[dict(name=p['name'], kind=p['kind']) for p in builtin],
                sources={name: digest(data) for name, data in rtl.items()},
                checker_sha256=digest(builtin_bytes),
                engine_sha256=eda.digest(Path(__file__)), adapter_sha256=eda.digest(Path(props.__file__)))


def trace_frames(path, end_step):
    """Read top-level inputs at SMT state indices, independent of VCD time units."""
    scope, codes, frames = [], {}, {}
    values = {}

    def save_step():
        if 'smt_step' in values:
            index = int(values['smt_step'], 2)
            if index in frames:  # The VCD also contains falling-edge clock changes.
                return
            frames[index] = dict(values)

    with path.open(encoding='utf-8') as stream:
        for line in stream:
            words = line.split()
            if not words:
                continue
            if words[0] == '$scope':
                scope.append(words[2])
            elif words[0] == '$upscope':
                scope.pop()
            elif words[0] == '$var':
                code, name = words[3:5]
                if (scope == [] and name == 'smt_step' or
                        scope == [props.TOP] and name in ('fc_stimulus', 'rst_n', 'fc_active')):
                    codes[code] = name
            elif words[0] == '$enddefinitions':
                break
        for line in stream:
            item = line.strip()
            if item.startswith('#'):
                save_step()
            elif item.startswith(('b', 'B')):
                value, code = item[1:].split()
                if code in codes:
                    values[codes[code]] = value
            elif item and item[1:] in codes:
                values[codes[item[1:]]] = item[0]
        save_step()
    if not 1 <= end_step or not set(range(end_step + 1)).issubset(frames):
        raise ValueError('求解轨迹缺少复位、启动或覆盖采样拍')
    for index in range(end_step + 1):
        frame = frames[index]
        if (frame.get('rst_n'), frame.get('fc_active')) != ('1' if index else '0', '1' if index >= 2 else '0'):
            raise ValueError('求解轨迹的复位或启动拍不符合 VCS 回放时序')
    stimulus = [frames[index]['fc_stimulus'] for index in range(2, end_step + 1)]
    if any(len(frame) != props.WIDTH or set(frame) - {'0', '1'} for frame in stimulus):
        raise ValueError('求解轨迹含未知或宽度不符的输入')
    return [int(frame, 2) for frame in stimulus]


def extract_traces(directory, statuses, names):
    traces = {}
    prefix = props.TOP + '.FC_C_'
    for row in statuses:
        name = row['name'][len(prefix):]
        if row['kind'] != 'COVER' or not row['name'].startswith(prefix) or name not in names or row['status'] != 'PASS':
            continue
        path = row.get('trace') or ''
        match = TRACE_PATH.fullmatch(path)
        if not match or not isinstance(row.get('depth'), int):
            raise ValueError('SBY 已命中属性但没有可解析的轨迹 ' + name)
        item = traces.setdefault(match[1], dict(trace=match[1], goals={}, failures=[], path=path))
        item['goals'][name] = row['depth']
    by_input = {}
    for item in traces.values():
        try:
            frames = trace_frames(directory/item['path'], max(item['goals'].values()))
        except (ValueError, KeyError, OSError) as exc:
            item['failures'].append(str(exc))
            continue
        if len(frames) < 8:
            item['failures'].append('轨迹缺少求解器生成的 8 拍空闲收尾')
            continue
        key = tuple(frames)
        if key in by_input:
            item['duplicate_of'] = by_input[key]['trace']
            by_input[key]['goals'].update(item['goals'])
            continue
        by_input[key] = item
        artifact = directory / (item['trace']+'.json')
        eda.write_json(artifact, dict(frames=['{:x}'.format(frame) for frame in frames]))
        item.update(frames=artifact.name, frames_sha256=eda.digest(artifact), body_cycles=len(frames))
    return list(traces.values())


def collect(directory, result, env):
    workspace = directory/'solver'
    status = workspace/'status'
    if not status.is_file():
        raise RuntimeError('Formal 未生成工具状态，详见 ' + str(directory/'sby.log'))
    solver_status = status.read_text(encoding='utf-8').partition(' ')[0].strip()
    if solver_status not in ('PASS', 'FAIL', 'TIMEOUT', 'UNKNOWN'):
        raise RuntimeError('Formal {}，详见 {}'.format(solver_status, directory/'sby.log'))
    status_log = directory/'properties.jsonl'
    rc, _ = eda.execute(['sby', '--status', '--statusfmt', 'jsonl', '-d', 'solver', 'run.sby'],
                        directory, status_log, env, 60, phase='functional_status')
    if rc:
        raise RuntimeError('无法读取 SBY 逐属性状态，详见 ' + str(status_log))
    statuses = [json.loads(line) for line in status_log.read_text(encoding='utf-8').splitlines()
                if line.startswith('{')]
    if result['action'] == 'bmc':
        checks = [row for row in statuses if row['kind'] == 'ASSERT']
        result.update(status=solver_status, checks=checks)
        return
    names = props.cover_names(result['functional'])
    candidates = extract_traces(directory, statuses, names)
    reached = {name for item in candidates if 'frames' in item for name in item['goals']}
    rejected = {name for item in candidates if item['failures'] for name in item['goals']}
    goals = {name: ('WITNESS' if name in reached else 'REPLAY_REJECTED' if name in rejected
                    else 'TIMEOUT' if solver_status == 'TIMEOUT'
                    else 'UNKNOWN' if solver_status == 'UNKNOWN' else 'NOT_FOUND_WITHIN_DEPTH') for name in names}
    if solver_status == 'PASS' and reached != set(names):
        print('FUNCTIONAL_WARNING SBY 命中目标中有轨迹无法提取，请查看任务结果', flush=True)
    status = ('COMPLETE' if reached == set(names) else 'TIMEOUT' if solver_status == 'TIMEOUT'
              else 'PARTIAL' if reached else 'NO_WITNESSES')
    result.update(status=status, solver_status=solver_status, goals=goals, candidates=candidates)


def load_campaign(identifier):
    result = eda.read_json(folder(identifier)/'result.json')
    if result.get('version') != 1 or result['id'] != identifier or result.get('action') != 'cover':
        raise ValueError('请选择已完成的 Formal 找用例任务')
    return result


def cases_for(result):
    root = folder(result['id'])
    cases = {}
    for entry in result.get('candidates', []):
        if not entry.get('frames'):
            continue
        trace = safe_id(entry['trace'])
        path = root / (trace+'.json')
        if eda.digest(path) != entry['frames_sha256']:
            raise ValueError('回放输入已改变 ' + trace)
        name = 'fc_' + result['id'].replace('-', '_') + '_' + trace
        cases[name] = (name, dict(name=name, format='formal_cover',
                                 campaign=result['id'], trace=trace, goals=entry['goals'],
                                 frames=eda.read_json(path)['frames']))
    if not cases:
        raise ValueError('该 Formal 任务没有可回放的用例')
    return cases


def program(case, seed):
    frames = case['frames']
    if not 1 <= len(frames) <= 1000000 or any(
            not re.fullmatch(r'[0-9a-f]+', value) or int(value, 16).bit_length() > props.WIDTH for value in frames):
        raise ValueError('非法 Formal 回放输入')
    return 'FORMAL_COVER_V1 {} {} {}\n{}\n'.format(case['name'], seed, len(frames), '\n'.join(frames))


def check_replay_build(campaign, build):
    hashes = build['sources']
    checked = dict(campaign['sources'])
    checked.update({ENGINE: campaign['engine_sha256'], props.SOURCE: campaign['adapter_sha256'],
                    eda.PROJECT['checker']: campaign['checker_sha256']})
    same_rtl = set(campaign['sources']) == {
        name for name in hashes if name in campaign['dut']['files'] or
        (name.endswith(('.v', '.sv', '.vh', '.svh')) and
         name.startswith(tuple(path.rstrip('/')+'/' for path in campaign['dut']['include_dirs'])))}
    if (not same_rtl or campaign['dut'] != build['dut'] or campaign['functional'] != build.get('functional') or
            campaign['contract_revision'] != build['contract_revision'] or
            any(hashes.get(name) != sha for name, sha in checked.items())):
        raise ValueError('Formal 找用例与 VCS 的 RTL、属性或约束版本不同。请使用当前工程重新找用例。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', required=True, choices=('cover', 'bmc'))
    parser.add_argument('--id', default='')
    parser.add_argument('--depth', type=int, default=48)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args(argv)
    if not 1 <= args.depth <= 1000000 or args.timeout < 0:
        parser.error('深度须为 1–1000000，超时须非负，0 表示不限时')
    execution.install_signal_handlers()
    try:
        identifier = args.id or eda.stamp()+'_'+eda.uuid.uuid4().hex[:8]
        directory = folder(identifier)
        directory.mkdir(parents=True)
        result = snapshot(directory, args.action, args.depth, args.timeout)
        print('FUNCTIONAL_STAGE ' + ('寻找 cover 轨迹' if args.action == 'cover' else '有界检查 assert'), flush=True)
        with toolchain.environment() as env:
            eda.execute(['sby', '-d', 'solver', 'run.sby'], directory, directory/'sby.log', env, None,
                        phase='functional_'+args.action)
            collect(directory, result, env)
        path = directory/'result.json'
        eda.write_json(path, result)
        print('FUNCTIONAL_RESULT ' + str(path), flush=True)
        print('{} {}'.format(args.action, result['status']), flush=True)
        return 0 if args.action == 'cover' or result['status'] == 'PASS' else 1
    except KeyboardInterrupt:
        return 130
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print('FUNCTIONAL: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
