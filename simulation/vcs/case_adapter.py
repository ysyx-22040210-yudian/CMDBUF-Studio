"""CMDBUF RD JSON sequences -> checked, deterministic stimulus instructions."""
import json
from pathlib import Path

COUNTERS = {'accepted', 'issued', 'captured', 'retired', 'issue_failed', 'ar_rejected', 'ar_gated',
            'ar_only', 'rw_only', 'full', 'full_reuse', 'ready_zero', 'head_blocked',
            'src0', 'src1', 'src2', 'resp0', 'resp1', 'resp3'}
COUNTERS |= {'reset_assertions', 'aborted', 'aborted_unissued', 'aborted_mem', 'aborted_response',
             'clock_edges', 'active_edges'}
COUNTERS |= {'submitted', 'queued_cancelled', 'queue_pending'}
COUNTERS |= {'random_cycles', 'random_solves'}
ENV_LIMITS = {'ready': 7, 'status': 65535, 'argrant': 255, 'rwgrant': 255, 'parity': 1}
AR_LIMITS = {'src': 2, 'addr': 262143, 'id': 255, 'port': 31, 'resp': 3}


def integer(value, name, maximum, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(name + ' must be an integer or 0x hexadecimal string')
    try:
        n = int(value, 0) if isinstance(value, str) else value
    except ValueError:
        raise ValueError('Invalid integer for ' + name)
    if not minimum <= n <= maximum:
        raise ValueError('{} must be {}..{}'.format(name, minimum, maximum))
    return n


def fields(data, limits):
    if not isinstance(data, dict) or set(data) - set(limits):
        raise ValueError('Unknown fields: ' + str(data))
    result = {k: integer(v, k, limits[k]) for k, v in data.items()}
    if 'resp' in result and result['resp'] not in (0, 3):
        raise ValueError('AR resp must be 0 or 3')
    return result


def load_case(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if isinstance(cfg, dict) and 'signal' in cfg:
        from wavedrom_adapter import compile_wave
        compile_wave(cfg)
        for key, value in cfg.get('require', {}).items():
            if key not in COUNTERS or isinstance(value, bool) or not isinstance(value, int):
                raise ValueError('Invalid required counter: ' + key)
            integer(value, key, 10000000)
        return cfg
    if not isinstance(cfg, dict) or set(cfg) - {'description', 'sequence', 'require'}:
        raise ValueError('Unknown case-level field')
    if not isinstance(cfg.get('sequence'), list) or not cfg['sequence']:
        raise ValueError('Case needs a nonempty sequence')
    for key, value in cfg.get('require', {}).items():
        if key not in COUNTERS:
            raise ValueError('Unknown required counter: ' + key)
        integer(value, key, 10000000)
        if not isinstance(value, int):
            raise ValueError('require counts must be JSON integers')
    return cfg


def make_program(cfg, seed, cycles=None):
    seed = integer(seed, 'seed', 2147483647, 1)
    if 'signal' in cfg:
        if cycles is not None:
            raise ValueError('CYCLES cannot resize an explicit WaveDrom case; edit its wave strings')
        from wavedrom_adapter import make_program as wave_program
        return wave_program(cfg)
    if cycles is not None:
        cycles = integer(cycles, 'cycles', 1000000, 1)
    env = {'ready': 7, 'status': 0, 'argrant': 255, 'rwgrant': 255, 'parity': 0}
    default_ar = {'src': 0, 'addr': 0, 'id': 0, 'port': 0, 'resp': 0}
    lines = []
    def emit(mode, count, valid, ar, background):
        values = [mode, count, valid, ar['src']]
        hexadecimal = [ar[k] for k in ('addr', 'id', 'port', 'resp')]
        hexadecimal += [background[k] for k in ('ready', 'status', 'argrant', 'rwgrant')]
        lines.append(' '.join(str(v) for v in values) + ' ' +
                     ' '.join(format(v, 'x') for v in hexadecimal) + ' ' + str(background['parity']))
    for item in cfg['sequence']:
        if not isinstance(item, dict):
            raise ValueError('Sequence entry must be an object')
        actions = set(item) & {'set', 'send', 'wait', 'random', 'cycle'}
        if len(actions) != 1 or set(item) - actions - {'timeout'}:
            raise ValueError('Each step needs exactly one set/send/wait/random/cycle action')
        action = next(iter(actions))
        if 'timeout' in item and action != 'send':
            raise ValueError('timeout is only valid on send')
        if action == 'set':
            env.update(fields(item[action], ENV_LIMITS))
        elif action in ('send', 'cycle'):
            ar = dict(default_ar); ar.update(fields(item[action], AR_LIMITS))
            if 'src' not in item[action] or 'addr' not in item[action]:
                raise ValueError('send/cycle require src and addr')
            count = integer(item.get('timeout', 100), 'timeout', 1000000, 1) if action == 'send' else 1
            emit(1 if action == 'send' else 0, count, 1, ar, env)
        elif action == 'wait':
            emit(0, integer(item[action], 'wait', 1000000, 1), 0, default_ar, env)
        else:
            count = cycles if cycles is not None else integer(item[action], 'random', 1000000, 1)
            # A random block is solved online by VCS with actual DUT feedback.
            # The source field carries the explicit RNG seed for opcode 2;
            # no offline candidate AR/busy values are generated or repaired.
            lines.append('2 {} 0 {} 0 0 0 0 0 0 0 0 0'.format(count, seed))
    if not lines:
        raise ValueError('Case must execute at least one cycle')
    return '\n'.join(lines) + '\n'


def explain_case(cfg, cycles=None):
    """Explain semantic actions; actual clock numbers require the saved event trace."""
    if 'signal' in cfg:
        from wavedrom_adapter import explain
        return explain(cfg)
    env = {'ready': 7, 'status': 0, 'argrant': 255, 'rwgrant': 255, 'parity': 0}
    out = [cfg.get('description', ''),
           '默认背景：ready=0b111，status=0，argrant=rwgrant=0xff，parity=0。',
           '以下是步骤语义，实际握手拍号用 make trace 查看。']
    for index, item in enumerate(cfg['sequence'], 1):
        action = next(iter(set(item) & {'set', 'send', 'wait', 'random', 'cycle'}))
        if action == 'set':
            env.update(fields(item[action], ENV_LIMITS))
            detail = '更新背景（不占拍、未写字段保持）；' + json.dumps(env, ensure_ascii=False)
        elif action in ('send', 'cycle'):
            ar = {'id': 0, 'port': 0, 'resp': 0}
            ar.update(fields(item[action], AR_LIMITS))
            addr = ar['addr']
            detail = ('{} src={} addr=0x{:05x} id={} port={} resp={}；bank={} half={} '
                      'arb_addr=0x{:03x} raddr={}'.format(
                          '等 AR 握手，最多 {} 拍；不等 ISSUE/R。'.format(item.get('timeout', 100))
                          if action == 'send' else '只尝试一拍，不保证接收。',
                          ar['src'], addr, ar['id'], ar['port'], ar['resp'],
                          (addr >> 3) & 7, (addr >> 6) & 1, addr >> 6, addr & 7))
        elif action == 'wait':
            detail = '{} 拍不发新 AR；背景仍有效，旧事务继续发射/返回。'.format(item[action])
        else:
            detail = ('{} 拍 VCS randomize() 约束求解；当拍 ready/req 和 MEM 历史参与求解，'
                      '解原样驱动，不作 valid/busy 后处理；随机块不继承 set 掩码。').format(cycles or item[action])
        out.append('{} [{}] {}'.format(index, action, detail))
    out.append('最后自动放开 ready/grant/status 排空，再观察 8 拍；require 统计包含排空。')
    out.append('require 为下限，不是精确次数/逐拍断言：' + json.dumps(cfg.get('require', {}), ensure_ascii=False))
    return '\n'.join(out)
