"""Coverage items and scores used by the native source browser."""
from collections import defaultdict
import re


METRICS = (('LINE', '行'), ('COND', '条件'), ('BRANCH', '分支'), ('TOGGLE', '翻转'),
           ('FSM', '状态机'), ('ASSERT', '断言'), ('GROUP', '覆盖组'))
COVERED, PARTIAL, MISSED = '已覆盖', '部分覆盖', '未覆盖'
EXCLUDED, UNREACHABLE, ILLEGAL = '排除', '不可达', '非法'
MEASURED = (COVERED, PARTIAL, MISSED)


def item_state(row):
    flags = max(0, row['status'])
    if flags & (4 | 64):
        return EXCLUDED
    if flags & 1:
        return UNREACHABLE
    if flags & 2:
        return ILLEGAL
    if row['total'] < 1:
        return '—'
    if row['covered'] == row['total']:
        return COVERED
    return PARTIAL if row['covered'] else MISSED


def line_coverage(rows):
    """Group the active metric's measured bins by exact file and source line."""
    lines = defaultdict(list)
    for row in rows:
        if row['line'] > 0 and (row['bin'] >= 0 or row['metric'] in ('ASSERT', 'GROUP')):
            lines[(row['file'], row['line'])].append(row)
    result = {}
    for location, items in lines.items():
        measured = [row for row in items if item_state(row) in MEASURED]
        if measured:
            hits = sum(row['covered'] for row in measured)
            total = sum(row['total'] for row in measured)
            result[location] = (hits, total, COVERED if hits == total else PARTIAL if hits else MISSED)
        else:
            states = {item_state(row) for row in items}
            result[location] = (0, 0, next((s for s in (EXCLUDED, UNREACHABLE, ILLEGAL) if s in states), '—'))
    return result


def urg_scores(path, hierarchy=False):
    """Read URG's reported percentages, keeping score and bin counts distinct."""
    if not path.is_file():
        return {}
    accepted = {'SCORE'} | {key for key, _ in METRICS}
    result, parents = {}, []
    columns, depth = [], 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        words = line.split()
        if words and words[0] == 'SCORE':
            columns = []
            for word in words:
                if word not in accepted:
                    break
                columns.append(word)
            depth = len(line)-len(line.lstrip())
            continue
        if not columns:
            continue
        fields = line.split(None, len(columns))
        if len(fields) != len(columns)+1 or any(not re.fullmatch(r'--|\d+(?:\.\d+)?', value) for value in fields[:-1]):
            continue
        name = fields[-1].strip()
        if hierarchy:
            while parents and parents[-1][0] >= depth:
                parents.pop()
            if parents and not name.startswith(parents[-1][1]+'.'):
                name = parents[-1][1]+'.'+name
            parents.append((depth, name))
        result[name] = dict(zip(columns, fields[:-1]))
    return result


class CoverageData:
    def __init__(self, rows):
        self.instances, self.items, self.labels = {}, {}, {}
        self.metrics = defaultdict(list)
        self.missing = defaultdict(int)
        ancestors = {}
        for row in rows:
            instance = row['instance']
            if 'id' not in row:
                self.instances[instance] = row
                ancestors[instance] = (instance,) + ancestors.get(row['parent_instance'], ())
                continue
            identifier = row['id']
            self.items[identifier] = row
            self.metrics[row['metric']].append(row)
            parent = self.labels.get(row['parent'])
            self.labels[identifier] = parent+' / '+row['name'] if parent else row['name']
            if row['bin'] >= 0 and item_state(row) in (MISSED, PARTIAL):
                module = self.instances[instance]['module']
                count = row['total']-row['covered']
                self.missing['模块', module, row['metric']] += count
                self.missing['全部范围', '@all', row['metric']] += count
                for scope in ancestors[instance]:
                    self.missing['实例层级', scope, row['metric']] += count
        self.ancestors = ancestors

    def scoped_items(self, mode, scope, metric):
        for row in self.metrics[metric]:
            instance = row['instance']
            if scope == '@all' or (mode == '模块' and self.instances[instance]['module'] == scope) or (
                    mode == '实例层级' and scope in self.ancestors[instance]):
                yield row
