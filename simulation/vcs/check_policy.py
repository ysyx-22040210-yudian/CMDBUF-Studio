"""Explicit verification scope; unknown timing is never reported as proven."""
import json
from pathlib import Path

MODES = ('transaction', 'strict')
COMMON = frozenset(('E03_SRC', 'E04_ARRESP', 'E05_ZERO_READY', 'E06_SUCCESS_BUSY',
                    'A_REQ_ONEHOT', 'A_R_ONEHOT', 'A_R_READY', 'A_PENDING_RANGE'))
TRANSACTION_NATIVE = frozenset(('A_COMPARE_RID', 'A_COMPARE_R_PORT_ID', 'A_COMPARE_RADDR',
                               'A_COMPARE_RRESP', 'A_COMPARE_RDATA'))
SCOPE = [
    dict(id='interface', title='接口与输入约束', rules='同拍 E03–E06、req/rvalid 独热、R ready、pending 范围、四态未知值'),
    dict(id='transactions', title='事务与数据', rules='接受/发射/返回对应、九项容量、单源顺序、ID/port/低地址、MEM 数据与错误优先级'),
    dict(id='timing', title='完整接口时序', rules='pending 组合递推、精确 ready/req/R 调度、五项窗口、RR、最早 N+5 响应')]


def normalize(mode='transaction', fatal=True):
    if mode not in MODES:
        raise ValueError('检查模式必须为 transaction 或 strict')
    if not isinstance(fatal, bool):
        raise ValueError('断言 fatal 开关必须为布尔值')
    return dict(check_mode=mode, assert_fatal=fatal)


def plan_options(plan):
    # Old plans were written for the full baseline. Do not silently relax them.
    return normalize(plan.get('check_mode', 'strict'), plan.get('assert_fatal', True))


def category(label):
    if label in COMMON or label == 'P_KNOWN_FRAME':
        return 'interface'
    if label.startswith('T_') or label.startswith('A_XAU_R_') or label in TRANSACTION_NATIVE:
        return 'transactions'
    return 'timing'


def audit_flags(directory):
    root = Path(directory)
    policy = root / 'check_policy.json'
    runtime = root / 'check_runtime.json'
    try:
        incomplete = runtime.exists() and bool(json.loads(runtime.read_text(encoding='utf-8')).get('incomplete'))
    except (OSError, ValueError, AttributeError):
        incomplete = False  # report() records archive corruption as a failure.
    return (policy.exists() and json.loads(policy.read_text(encoding='utf-8')).get('check_mode') == 'transaction', incomplete)


def catalog():
    value = json.loads(Path(__file__).with_name('check_profiles.json').read_text(encoding='utf-8'))
    if value.get('version') != 1 or set(value.get('common_native', [])) != COMMON:
        raise ValueError('CONTRACT_UNSUPPORTED: checking profile inventory differs from implemented common rules')
    return value


def report(directory, options, required=True):
    directory = Path(directory)
    runtime_path = directory / 'check_runtime.json'
    errors = []
    runtime = {}
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
            if not isinstance(runtime, dict):
                raise ValueError('expected a JSON object')
        except (OSError, ValueError) as exc:
            errors.append('invalid check_runtime.json: '+str(exc)); runtime = {}
    records = []
    path = directory / 'assertions.jsonl'
    if path.exists():
        for index,line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(),1):
            if not line.strip(): continue
            try:
                record = json.loads(line)
                if not isinstance(record,dict) or not isinstance(record.get('label'),str):
                    raise ValueError('missing assertion label')
                records.append(record)
            except ValueError as exc:
                errors.append('invalid assertions.jsonl line {}: {}'.format(index,exc))
    if required:
        if not runtime:
            errors.append('missing check_runtime.json')
        elif (runtime.get('mode') != options['check_mode'] or
              runtime.get('assert_fatal') != int(options['assert_fatal'])):
            errors.append('runtime verification policy differs from requested policy')
        if runtime and runtime.get('failures') != len(records):
            errors.append('assertion count does not match archived failures')
        if not runtime.get('finished') and not records:
            errors.append('verification monitor did not finish')
        if runtime.get('finished') and runtime.get('sampled_frames') != runtime.get('checked_frames'):
            errors.append('unconsumed snapshot at end of simulation')
    groups = []
    for item in SCOPE:
        group = dict(item)
        failures = [r for r in records if category(r['label']) == item['id']]
        disabled = item['id'] == 'timing' and options['check_mode'] == 'transaction'
        group['failures'] = len(failures)
        invalid_environment = any(category(r['label']) == 'interface' for r in records)
        unfinished = errors or not runtime.get('finished') or (item['id'] != 'interface' and
                     (runtime.get('incomplete') or invalid_environment))
        group['status'] = ('NOT_CHECKED' if disabled else 'FAIL' if failures else 'INCOMPLETE' if unfinished else 'PASS')
        group['reason'] = ('通用模式不规定 pending 更新时点、精确调度或最早 N+5；不作逐拍时序通过结论。'
                           if disabled else '')
        groups.append(group)
    return dict(version=1, **options, profile_version=catalog()['version'], runtime=runtime, failures=records, integrity_errors=errors,
                groups=groups, sampling='pre-edge #1step; complete four-state frame; final frame explicitly flushed',
                contract='CMDBUF protocol 20261021 common interface; MEM capture remains issue N+4',
                transaction_policy='All observable issue identities retained (bounded); same-source AR order; '
                                   'capture-edge R forwarding permitted in transaction mode; no fixed response deadline.',
                limitations=['Finite simulation evidence, not a formal proof.',
                             'An exhausted issue-identity hypothesis budget or outstanding requests is INCOMPLETE, never PASS.',
                             'TC legality, driver scheduling and infrastructure errors always stop execution.'])
