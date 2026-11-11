"""Check a fresh Linux distribution using its public Make entry points.

Run under a graphical display, with the site's licensed EDA tools on PATH.
No configuration file, EDA_TOOL_SETUP, VCS_HOME or VERDI_HOME is supplied.
The test only terminates the make gui process group it starts itself.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

project = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
checks = []
commands = []


def check(name, value, detail=None):
    checks.append(dict(name=name, pass_=bool(value), detail=detail))
    if not value:
        raise AssertionError(name + ': ' + str(detail))
    print('PASS ' + name, flush=True)


def run(argv, label, timeout=240):
    completed = subprocess.run(argv, cwd=str(project), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=timeout)
    # VCS -ID includes a device identifier; it is not needed for this release check.
    text = completed.stdout.decode('utf-8', errors='replace')
    text = '\n'.join(line for line in text.splitlines()
                     if 'hostid' not in line.lower() and 'host id' not in line.lower()) + '\n'
    (evidence / (label + '.log')).write_text(text, encoding='utf-8')
    commands.append(dict(argv=argv, returncode=completed.returncode, log=label + '.log'))
    check(label, completed.returncode == 0, completed.returncode)
    return text


try:
    check('fresh project has no machine configuration', not (project / 'eda.local.json').exists())
    check('no explicit setup path or installation homes',
          not any(os.environ.get(k) for k in ('EDA_TOOL_SETUP', 'VCS_HOME', 'VERDI_HOME')))
    check('project path contains spaces and Unicode', ' ' in str(project) and any(ord(c) > 127 for c in str(project)))
    run([sys.executable, 'simulation/vcs/portable.py', '--verify'], 'package_verify')
    run(['make', '--no-print-directory', 'env-info', 'INFO=' + str(evidence / 'environment.json')], 'env_info')
    detected = json.loads((evidence / 'environment.json').read_text(encoding='utf-8'))
    check('automatic tool discovery', detected['profile'] == 'auto' and
          all(any(row['name'] == name and row['status'] == '已发现' for row in detected['rows'])
              for name in ('vcs', 'verdi', 'urg')))
    run(['make', '--no-print-directory', 'doctor'], 'doctor')
    run(['make', '--no-print-directory', 'smoke', 'WAVES=0', 'JOBS=2'], 'smoke')
    sys.path.insert(0, str(project / 'simulation/vcs'))
    import platform_config
    out = platform_config.result_root()
    batch = json.loads((out / 'latest.json').read_text(encoding='utf-8'))['id']
    batch_path = out / 'regressions' / batch
    summary = json.loads((batch_path / 'summary.json').read_text(encoding='utf-8'))
    check('three smoke simulations pass', summary['status'] == 'PASS' and len(summary['results']) == 3,
          [(r['case'], r['seed'], r['status']) for r in summary['results']])
    check('coverage merge passes', summary['coverage']['status'] == 'PASS', summary['coverage'].get('metrics_percent'))
    check('WAVES=0 has no waveform files', not list(batch_path.rglob('*.fsdb')) and not list(batch_path.rglob('*.vcd')))
    check('automatic ASCII output directory', all(ord(c) < 128 and c != ' ' for c in str(out)), str(out))
    (evidence / 'smoke_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    with (evidence / 'make_gui.log').open('wb') as log:
        gui = subprocess.Popen(['make', '--no-print-directory', 'gui'], cwd=str(project),
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            windows = ''
            deadline = time.time() + 30
            while time.time() < deadline and gui.poll() is None:
                windows = subprocess.check_output(['xwininfo', '-root', '-tree']).decode('utf-8', errors='replace')
                if 'CMDBUF Studio' in windows:
                    break
                time.sleep(.2)
            check('make gui creates native window', gui.poll() is None and 'CMDBUF Studio' in windows)
            # Wait for automatic detection, then confirm the standalone app is still alive.
            deadline = time.time() + 20
            jobs = []
            while time.time() < deadline and gui.poll() is None:
                jobs = list((project / 'build/vcs/studio/jobs').glob('*.json'))
                if any(json.loads(p.read_text(encoding='utf-8')).get('state') == 'DONE' for p in jobs):
                    break
                time.sleep(.2)
            check('standalone GUI automatic detection completes', gui.poll() is None and any(
                  json.loads(p.read_text(encoding='utf-8')).get('kind') == 'detect' and
                  json.loads(p.read_text(encoding='utf-8')).get('state') == 'DONE' for p in jobs))
            (evidence / 'make_gui_windows.txt').write_text(windows, encoding='utf-8')
        finally:
            if gui.poll() is None:
                os.killpg(gui.pid, signal.SIGTERM)
            gui.wait(timeout=10)
    # Running the tools should not change any distributed source files.
    run([sys.executable, 'simulation/vcs/portable.py', '--verify'], 'package_verify_after_run')
    status = 'PASS'
except Exception as exc:
    status = 'FAIL'
    checks.append(dict(name='exception', pass_=False, detail=repr(exc)))
finally:
    receipt = dict(status=status, project=str(project), checks=checks, commands=commands,
                   manifest_sha256=hashlib.sha256((project / 'PORTABLE_MANIFEST.json').read_bytes()).hexdigest())
    (evidence / 'distribution.json').write_text(json.dumps(receipt, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    print('DISTRIBUTION ' + status, flush=True)
sys.exit(0 if status == 'PASS' else 1)
