"""Studio data model: main_phase-only edits and optimistic file versioning."""
import hashlib
import json
import re
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import eda
import dut_config
import regression_plan

ROOT = eda.ROOT
STATE = ROOT / 'build/vcs/studio'


def mask_sv(code):
    # Keep source offsets while ignoring keywords inside comments and strings.
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"',
                  lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group()), code, flags=re.S)


def phase_span(code):
    clean = mask_sv(code)
    matches = list(re.finditer(r'\b(?:virtual\s+)?task\s+(?:automatic\s+)?main_phase\s*;', clean))
    if len(matches) != 1:
        raise ValueError('TC 必须包含一个 task main_phase;')
    start = matches[0].start()
    end = re.search(r'\bendtask\b', clean[matches[0].end():])
    if not end:
        raise ValueError('main_phase 缺少 endtask')
    return start, matches[0].end() + end.end()


def check_phase(code):
    a, b = phase_span(code)
    clean = mask_sv(code)
    if clean[:a].strip() or clean[b:].strip() or re.search(r'\b(?:class|endclass|module|endmodule|program)\b', clean[a:b]):
        raise ValueError('窗口内只填写 main_phase，从 task main_phase; 到 endtask')
    if len(re.findall(r'\btask\b', clean)) != 1:
        raise ValueError('辅助 task 应放在环境中，当前窗口只编辑 main_phase')
    if len(code) > 200000:
        raise ValueError('main_phase 超过 200 KB')
    return code.strip()


def tc_path(name):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(name)):
        raise ValueError('用例名须以字母开头，只含字母、数字和下划线')
    return eda.HERE / 'tc' / (name + '.sv')


def case(name):
    path = tc_path(name)
    code = path.read_text(encoding='utf-8')
    a, b = phase_span(code)
    import textwrap
    phase = textwrap.dedent('    ' + code[a:b]).strip()
    cycles = re.search(r'num_cycles\s*=\s*(\d+)', mask_sv(code))
    return dict(name=name, phase=phase, version=eda.digest(path), cycles=int(cycles.group(1)) if cycles else 64,
                description=next((s[3:].strip() for s in code.splitlines() if s.startswith('// ')), ''),
                file=path.relative_to(ROOT).as_posix())


def cases():
    return [case(p.stem) for p in sorted((eda.HERE / 'tc').glob('*.sv'))]


def save_case(payload):
    name, phase = payload['name'], check_phase(payload['phase'])
    path = tc_path(name)
    if payload.get('create'):
        if path.exists():
            raise ValueError('用例已存在，请选择其他名称')
        template = tc_path(payload.get('template', 'tc_directed')).read_text(encoding='utf-8')
        source = re.sub(r'\bclass\s+[A-Za-z]\w*\s+extends', 'class ' + name + ' extends', template, count=1)
    else:
        if eda.digest(path) != payload.get('version'):
            raise ValueError('文件已在外部修改，请重新加载后再保存，避免覆盖')
        source = path.read_text(encoding='utf-8')
        backup = STATE / 'backups' / ('{}_{}.sv'.format(name, eda.stamp() + '_' + eda.uuid.uuid4().hex[:6]))
        backup.parent.mkdir(parents=True, exist_ok=True); backup.write_text(source, encoding='utf-8')
    a, b = phase_span(source)
    rewritten = source[:a] + phase.replace('\n', '\n    ') + source[b:]
    temporary = path.with_suffix('.sv.tmp')
    temporary.write_text(rewritten, encoding='utf-8'); temporary.replace(path)
    return case(name)


def default_plan():
    return dict(version=1, name='CMDBUF 功能回归', jobs=2, timeout=120, waves=True,
                cases=[dict(case=c['name'], enabled=c['name'] != 'tc_user_example', runs=10 if c['name']=='tc_random' else 1,
                            seed=1, cycles=2000 if c['name']=='tc_random' else c['cycles']) for c in cases()])


def check_plan(plan):
    regression_plan.expand(plan)
    plan['jobs'] = regression_plan.number(plan.get('jobs'), '并行数', 1, 32)
    plan['timeout'] = regression_plan.number(plan.get('timeout'), '超时秒数', 1, 36000)
    if not isinstance(plan.get('waves'), bool):
        raise ValueError('波形开关必须为布尔值')
    for row in plan['cases']:
        if not tc_path(row['case']).is_file():
            raise ValueError('找不到用例: ' + row['case'])
    return plan


def state_read():
    path = STATE / 'settings.json'
    default = dict(plan=default_plan())
    if path.exists():
        saved = eda.read_json(path)
        if 'plan' in saved:
            default['plan'] = saved['plan']
    default.update(eda.platform_config.load())
    return default


def settings_save(value):
    saved = state_read()
    saved.update(eda.platform_config.save(dict(saved,**value)))
    return saved


def results_root():
    return eda.OUT/'regressions'
