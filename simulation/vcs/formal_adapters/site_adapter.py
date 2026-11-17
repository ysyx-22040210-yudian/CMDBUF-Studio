#!/usr/bin/env python3
"""Run a site-maintained commercial Formal driver and convert its TSV to Studio v1."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def convert(report):
    result = dict(properties=[], reports=[])
    with report.open(encoding='utf-8', newline='') as stream:
        for number, row in enumerate(csv.reader(stream, delimiter='\t'), 1):
            if not row:
                continue
            record, values = row[0], row[1:]
            if record == 'engine' and len(values) == 2 and 'engine' not in result:
                result['engine'] = dict(zip(('name', 'version'), values))
            elif record == 'complete' and values in (['1'], ['0']) and 'complete' not in result:
                result['complete'] = values[0] == '1'
            elif record == 'property' and len(values) in (6, 7):
                kind, label, name, status, depth, trace = values[:6]
                prop = dict(kind=kind, label=label, name=name, status=status,
                            depth=int(depth) if depth else None, trace=trace or None)
                if len(values) == 7 and values[6]:
                    prop['stimulus'] = values[6]
                result['properties'].append(prop)
            elif record == 'report' and len(values) == 1:
                result['reports'].append(values[0])
            elif record == 'message' and len(values) == 1 and 'message' not in result:
                result['message'] = values[0]
            else:
                raise ValueError('invalid site TSV record on line {}'.format(number))
    if 'engine' not in result or 'complete' not in result:
        raise ValueError('site TSV must include engine and complete records')
    return result


def main(argv):
    if len(argv) != 2:
        raise ValueError('usage: site_adapter.py request.json adapter-result.json')
    request_path, output = [Path(item).resolve() for item in argv]
    raw = request_path.read_bytes()
    request = json.loads(raw.decode('utf-8'))
    if request['schema'] != 'test_mode.commercial_formal.v1':
        raise ValueError('unsupported Studio request schema')
    driver = shutil.which(os.environ.get('EDA_COMMERCIAL_FORMAL_DRIVER', ''))
    if not driver:
        raise ValueError('configure executable EDA_COMMERCIAL_FORMAL_DRIVER on this device')
    report = request_path.parent / 'site-result.tsv'
    subprocess.run([driver, str(request_path), str(report)], cwd=str(request_path.parent), check=True)
    result = convert(report)
    result.update(schema=request['schema'], run_id=request['run_id'],
                  request_sha256=hashlib.sha256(raw).hexdigest())
    temporary = output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(output)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print('SITE_FORMAL_ADAPTER: ' + str(exc), file=sys.stderr)
        sys.exit(2)
