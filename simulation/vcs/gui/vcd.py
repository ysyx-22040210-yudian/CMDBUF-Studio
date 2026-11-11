"""Index actual VCS VCD transitions, preserving four-state values and aliases."""
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

LOCK = threading.Lock()


def index(path, cache):
    path, cache = Path(path), Path(cache)
    stat = path.stat()
    key = hashlib.sha256(('v2:' + str(path) + str(stat.st_size) + str(stat.st_mtime_ns)).encode()).hexdigest()[:24]
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + '.sqlite')
    with LOCK:
        if target.exists():
            return target
        temporary = target.with_suffix('.partial')
        if temporary.exists():
            temporary.unlink()
        db = sqlite3.connect(str(temporary))
        try:
            db.executescript('CREATE TABLE signals(id INTEGER PRIMARY KEY,code TEXT,name TEXT,width INTEGER,kind TEXT,UNIQUE(code,name,width,kind));'
                             'CREATE TABLE changes(code TEXT,t INTEGER,v TEXT);CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);')
            scopes, batch, previous = [], [], {}
            t, maximum, scale, header = 0, 0, '1 ps', True
            with path.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    line = line.strip()
                    if header:
                        if line.startswith('$timescale'):
                            value = line
                            while '$end' not in value:
                                value += ' ' + next(stream).strip()
                            scale = re.sub(r'\$(?:timescale|end)', '', value).strip()
                        elif line.startswith('$scope'):
                            scopes.append(line.split()[2])
                        elif line.startswith('$upscope'):
                            scopes.pop()
                        elif line.startswith('$var'):
                            pieces = line.split()
                            name = '.'.join(scopes + [''.join(pieces[4:-1])])
                            db.execute('INSERT OR IGNORE INTO signals(code,name,width,kind) VALUES(?,?,?,?)',
                                       (pieces[3], name, int(pieces[2]), pieces[1]))
                        elif line.startswith('$enddefinitions'):
                            header = False
                        continue
                    if not line or line[0] == '$':
                        continue
                    if line[0] == '#':
                        t = int(line[1:]); maximum = max(maximum, t)
                        continue
                    if line[0] in 'bBrR':
                        value, code = line[1:].split()
                    elif line[0] in '01xXzZ':
                        value, code = line[0], line[1:]
                    else:
                        continue
                    value = value.lower()
                    if previous.get(code) == value:
                        continue
                    previous[code] = value
                    batch.append((code, t, value))
                    if len(batch) >= 50000:
                        db.executemany('INSERT INTO changes VALUES(?,?,?)', batch); batch = []
            db.executemany('INSERT INTO changes VALUES(?,?,?)', batch)
            db.execute('CREATE INDEX transition_time ON changes(code,t)')
            meta = {'timescale': scale, 'end': maximum, 'source_bytes': stat.st_size,
                    'source': path.name, 'signals': db.execute('SELECT count(*) FROM signals').fetchone()[0],
                    'transitions': db.execute('SELECT count(*) FROM changes').fetchone()[0]}
            db.execute('INSERT INTO meta VALUES(?,?)', ('info', json.dumps(meta)))
            db.commit()
        finally:
            db.close()
        temporary.replace(target)
    return target


def describe(database):
    with closing(sqlite3.connect(str(database))) as db:
        result = json.loads(db.execute("SELECT v FROM meta WHERE k='info'").fetchone()[0])
        result['signals'] = [dict(id=r[0], name=r[1], width=r[2], kind=r[3])
                             for r in db.execute('SELECT id,name,width,kind FROM signals ORDER BY name')]
        return result


def window(database, ids, start, end):
    if not 0 <= start < end or not 1 <= len(ids) <= 64:
        raise ValueError('选择 1..64 个信号以及有效时间范围')
    result, budget = [], 200000
    with closing(sqlite3.connect(str(database))) as db:
        for identifier in ids:
            row = db.execute('SELECT code,name,width,kind FROM signals WHERE id=?', (int(identifier),)).fetchone()
            if row is None:
                raise ValueError('信号不存在')
            code, name, width, kind = row
            prior = db.execute('SELECT t,v FROM changes WHERE code=? AND t<=? ORDER BY t DESC,rowid DESC LIMIT 1', (code, start)).fetchone()
            values = list(db.execute('SELECT t,v FROM changes WHERE code=? AND t>? AND t<=? ORDER BY t,rowid LIMIT 50001', (code, start, end)))
            dense = len(values) > 50000 or len(values) > budget
            if dense:
                values = []
            else:
                budget -= len(values)
                if prior:
                    values.insert(0, (start, prior[1]))
                elif not values or values[0][0] != start:
                    values.insert(0, (start, 'x'))
            result.append(dict(id=int(identifier), name=name, width=width, kind=kind, values=values, dense=dense))
    return dict(start=start, end=end, signals=result)
