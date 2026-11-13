"""Explicit verification scope; unknown timing is never reported as proven."""
import json
from pathlib import Path
from contracts import BASELINE_REVISION, REGISTERED_PENDING_REVISIONS, REVISION, expected_rresp

MODES = ('transaction', 'strict')
COMMON = frozenset(('E03_SRC', 'E04_ARRESP', 'E05_ZERO_READY', 'E06_SUCCESS_BUSY',
                    'E07_SRC_READY', 'E08_AR_GRANT_REQ', 'A_REQ_ONEHOT', 'A_R_ONEHOT', 'A_R_READY', 'A_PENDING_RANGE',
                    'A_PENDING_RECURRENCE'))
TRANSACTION_NATIVE = frozenset(('A_COMPARE_RID', 'A_COMPARE_R_PORT_ID', 'A_COMPARE_RADDR',
                               'A_COMPARE_RRESP', 'A_COMPARE_RDATA'))
SCOPE = [
    dict(id='interface', title='接口与输入约束', rules='同拍 E03–E08、req/rvalid 独热、R ready、pending 范围与寄存递推、四态未知值'),
    dict(id='transactions', title='事务与数据', rules='接受/发射/返回对应、九项容量、单源顺序、ID/port/低地址、MEM 数据与错误优先级'),
    dict(id='timing', title='完整接口时序', rules='pending 参考计数、精确 ready/req/R 调度、五项窗口、RR、最早 N+5 响应')]


def normalize(mode='transaction', fatal=True, env_fatal=True):
    if mode not in MODES:
        raise ValueError('检查模式必须为 transaction 或 strict')
    if not isinstance(fatal, bool):
        raise ValueError('断言 fatal 开关必须为布尔值')
    if not isinstance(env_fatal, bool):
        raise ValueError('环境 fatal 开关必须为布尔值')
    return dict(check_mode=mode, assert_fatal=fatal, env_fatal=env_fatal)


def plan_options(plan):
    # Old plans were written for the full baseline. Do not silently relax them.
    return normalize(plan.get('check_mode', 'strict'), plan.get('assert_fatal', True),
                     plan.get('env_fatal', True))


def environment_report(directory, options, required=False):
    """A completed simulation with driver errors is still an invalid run."""
    root = Path(directory)
    runtime, records, errors = {}, [], []
    path = root / 'environment_runtime.json'
    if path.exists():
        try:
            runtime = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(runtime, dict):
                raise ValueError('expected a JSON object')
        except (OSError, ValueError) as exc:
            errors.append('invalid environment_runtime.json: '+str(exc)); runtime = {}
    path = root / 'environment_errors.jsonl'
    if path.exists():
        for index, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if not line.strip(): continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get('label'), str) or value.get('unrecoverable') not in (0, 1):
                    raise ValueError('missing environment error label or severity')
                records.append(value)
            except ValueError as exc:
                errors.append('invalid environment_errors.jsonl line {}: {}'.format(index, exc))
    elif required:
        errors.append('missing environment_errors.jsonl')
    if required and not runtime:
        errors.append('missing environment_runtime.json')
    if runtime:
        if runtime.get('env_fatal') != int(options.get('env_fatal', True)):
            errors.append('runtime environment fatal policy differs from requested policy')
        if runtime.get('failures') != len(records):
            errors.append('environment error count does not match archive')
        if runtime.get('unrecoverable') != sum(r['unrecoverable'] for r in records):
            errors.append('unrecoverable environment error count does not match archive')
    return dict(runtime=runtime, failures=records, integrity_errors=errors,
                completed=bool(runtime.get('finished')),
                status='FAIL' if records or errors else 'PASS' if runtime.get('finished') else
                       'INCOMPLETE' if runtime else 'NOT_RECORDED')


def category(label, revision=REVISION, user_labels=()):
    if label in user_labels:
        return 'user_sva'
    if label == 'A_PENDING_RECURRENCE' and revision not in REGISTERED_PENDING_REVISIONS:
        return 'timing'
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
    if (value.get('version') != 1 or set(value.get('common_native', [])) != COMMON or
            not value.get('strict_native') or COMMON.intersection(value['strict_native'])):
        raise ValueError('CONTRACT_UNSUPPORTED: checking profile inventory differs from implemented common rules')
    return value


def report(directory, options, required=True, environment_required=False, contract_revision=REVISION, checker=None):
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
    environment = environment_report(directory, options, environment_required)
    errors.extend(environment['integrity_errors'])
    if environment_required and not environment['completed'] and not environment['failures'] and not records:
        errors.append('environment did not complete the testcase')
    if required:
        if not runtime:
            errors.append('missing check_runtime.json')
        elif (runtime.get('mode') != options['check_mode'] or
              runtime.get('assert_fatal') != int(options['assert_fatal'])):
            errors.append('runtime verification policy differs from requested policy')
        if runtime and runtime.get('failures') != len(records):
            errors.append('assertion count does not match archived failures')
        if runtime and ('env_fatal' in runtime or environment_required):
            effective = bool(options.get('env_fatal', True) and options['assert_fatal'])
            if (runtime.get('env_fatal') != int(options.get('env_fatal', True)) or
                runtime.get('effective_assert_fatal') != int(effective)):
                errors.append('runtime effective assertion fatal policy differs from requested policy')
        if not runtime.get('finished') and not records:
            errors.append('verification monitor did not finish')
        if runtime.get('finished') and runtime.get('sampled_frames') != runtime.get('checked_frames'):
            errors.append('unconsumed snapshot at end of simulation')
    checker = checker or {}
    user_labels = set(checker.get('user_properties', []))
    scopes = list(SCOPE)
    if user_labels:
        scopes.append(dict(id='user_sva', title='用户 SVA', rules='自定义属性及其失败记录'))
    by_scope = {}
    for record in records:
        key = category(record['label'], contract_revision, user_labels)
        by_scope[key] = by_scope.get(key, 0)+1
    groups = []
    registered_pending = contract_revision in REGISTERED_PENDING_REVISIONS
    for item in scopes:
        group = dict(item)
        if item['id']=='interface' and not registered_pending:
            end_rule = 'E06' if contract_revision==BASELINE_REVISION else 'E08'
            group['rules'] = '同拍 E03–{}、req/rvalid 独热、R ready、pending 范围、四态未知值'.format(end_rule)
        if item['id']=='timing' and not registered_pending:
            group['rules'] = group['rules'].replace('pending 参考计数', 'pending 组合递推')
        failures = by_scope.get(item['id'], 0)
        disabled = item['id'] == 'timing' and options['check_mode'] == 'transaction'
        group['failures'] = failures
        invalid_environment = by_scope.get('interface', 0)
        unfinished = errors or environment['failures'] or not runtime.get('finished') or (item['id'] != 'interface' and
                     (runtime.get('incomplete') or invalid_environment))
        group['status'] = ('NOT_CHECKED' if disabled else 'FAIL' if failures else 'INCOMPLETE' if unfinished else 'PASS')
        if disabled:
            pending_scope = 'pending 参考计数' if registered_pending else 'pending 组合时点'
            group['reason'] = '通用模式不执行{}、精确调度及最早 N+5 的检查。'.format(pending_scope)
        else:
            group['reason'] = ''
        groups.append(group)
    return dict(version=1, **options, profile_version=catalog()['version'], runtime=runtime, failures=records, integrity_errors=errors,
                environment=environment, effective_assert_fatal=bool(options.get('env_fatal', True) and options['assert_fatal']),
                groups=groups, sampling='pre-edge #1step; complete four-state frame; final frame explicitly flushed',
                contract_revision=contract_revision, checker=checker,
                contract='CMDBUF {}: MEM capture at issue N+4, parity error RRESP={}'.format(
                    contract_revision, expected_rresp(0, 1, contract_revision)),
                transaction_policy='All observable issue identities retained (bounded); same-source AR order; '
                                   'capture-edge R forwarding permitted in transaction mode; no fixed response deadline.',
                limitations=['Finite simulation evidence, not a formal proof.',
                             'An exhausted issue-identity hypothesis budget or outstanding requests is INCOMPLETE, never PASS.',
                             'ENV_FATAL=0 continues after recoverable driver and assertion errors; failures still invalidate the run.',
                             'Invalid clocks, missing inputs/files, unsatisfiable solvers, corrupted driver state and watchdogs remain fatal.',
                             'User/DUT $fatal, $stop, $finish, simulator crashes and infinite loops are outside this switch.'])
