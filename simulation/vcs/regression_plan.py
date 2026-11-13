"""One explicit seed range per TC, shared by the GUI and Make runner."""
import re


def number(value, label, low, high=None):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(label + ' 必须是整数')
    n = int(value)
    if n < low or (high is not None and n > high):
        if high is None:
            raise ValueError('{} 必须大于等于 {}'.format(label, low))
        raise ValueError('{} 必须在 {}..{} 范围'.format(label, low, high))
    return n


def expand(plan):
    if plan.get('version') != 1:
        raise ValueError('Unsupported regression plan version')
    jobs, names = [], set()
    for row in plan.get('cases', []):
        if not row.get('enabled', True):
            continue
        name = row.get('case', '')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) or name in names:
            raise ValueError('用例名无效或重复: ' + name)
        names.add(name)
        count = number(row.get('runs'), '回归次数', 1, 10000)
        first = number(row.get('seed'), '起始种子', 1, 2147483647)
        cycles = number(row.get('cycles'), '每次周期', 1, 1000000)
        if first + count - 1 > 2147483647:
            raise ValueError('种子范围溢出')
        jobs.extend((name, first + i, cycles, '') for i in range(count))
    if not 1 <= len(jobs) <= 10000:
        raise ValueError('请选择用例，总回归次数须在 1..10000')
    return jobs
