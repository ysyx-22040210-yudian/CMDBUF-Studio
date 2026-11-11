"""Validate the release against the observed acceptance run, then seal new evidence.

The Linux acceptance snapshot of portable.py is supplied as the first argument;
only its QUICKSTART documentation may differ from the current release.
No previous verification directory is modified.
"""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def code_without_quickstart(text):
    module = ast.parse(text)
    module.body = [node for node in module.body if not (
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'QUICKSTART' for t in node.targets))]
    return ast.dump(module, include_attributes=False)


accepted = json.loads((HERE / 'acceptance.json').read_text(encoding='utf-8'))
distribution = json.loads((HERE / 'distribution/distribution.json').read_text(encoding='utf-8'))
assert accepted['status'] == distribution['status'] == 'PASS'
assert all(c['pass_'] for c in accepted['checks'] + distribution['checks'])
runtime = {rel: digest((ROOT / rel).read_bytes()) for rel in accepted['runtime_sha256']}
changed = [rel for rel in runtime if runtime[rel] != accepted['runtime_sha256'][rel]]
assert changed == ['simulation/vcs/portable.py'], changed
snapshot = Path(sys.argv[1])
assert digest(snapshot.read_bytes()) == accepted['runtime_sha256'][changed[0]]
assert code_without_quickstart(snapshot.read_text(encoding='utf-8')) == code_without_quickstart((ROOT / changed[0]).read_text(encoding='utf-8'))

packages = json.loads((ROOT / 'packages/SHA256SUMS.json').read_text(encoding='utf-8'))
for name, expected in packages['archives'].items():
    data = (ROOT / 'packages' / name).read_bytes()
    assert len(data) == expected['bytes'] and digest(data) == expected['sha256']
with zipfile.ZipFile(str(ROOT / 'packages/cmdbuf-studio-portable.zip')) as archive:
    manifest_data = archive.read('cmdbuf-studio/PORTABLE_MANIFEST.json')
    assert digest(manifest_data) == distribution['manifest_sha256']
    manifest = json.loads(manifest_data.decode('utf-8'))
    for rel, expected in manifest['files'].items():
        data = archive.read('cmdbuf-studio/' + rel)
        assert digest(data) == expected, rel
        assert not rel.endswith('.local.json') and '/gui/web/' not in rel
        if rel != 'README.md':
            current = (ROOT / rel).read_bytes()
            if rel.endswith('.sh'):
                current = current.replace(b'\r\n', b'\n')
            assert current == data, rel

subprocess.check_call(['git', '-c', 'core.whitespace=cr-at-eol', 'diff', '--check'], cwd=str(ROOT))
subprocess.check_call(['git', 'diff', '--exit-code', 'HEAD', '--',
                       'verification/fsdb_formal_20261111', 'verification/portable_20261111',
                       'verification/repository_20261111'], cwd=str(ROOT))
windows = (HERE / 'unit_windows.log').read_text(encoding='utf-8')
linux = (HERE / 'unit_linux.log').read_text(encoding='utf-8')
assert 'Ran 44 tests' in windows and 'OK (skipped=2)' in windows
assert 'Ran 44 tests' in linux and linux.strip().endswith('OK')
# Exclude the unneeded FLEXlm hardware identifier from the newly published log.
doctor = HERE / 'doctor.log'
lines = doctor.read_text(encoding='utf-8').splitlines()
doctor.write_text('\n'.join(line for line in lines if 'host id' not in line.lower() and 'hostid' not in line.lower()) + '\n', encoding='utf-8')

receipt = dict(
    status='PASS', created_utc=datetime.datetime.utcnow().isoformat() + 'Z',
    baseline_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT)).decode().strip(),
    unit_linux=dict(total=44, passed=44), unit_windows=dict(total=44, passed=42, skipped=2, reason='Linux Bash discovery tests'),
    native_acceptance_checks=len(accepted['checks']), distribution_checks=len(distribution['checks']),
    runtime_sha256=runtime, acceptance_delta=dict(files=changed, change='QUICKSTART documentation only; executable Python AST matches the tested snapshot'),
    distribution=packages, package_files=packages['files'],
    docs=dict(word='documents/36_cmdbuf_native_tk_20261111.docx', pdf='documents/36_cmdbuf_native_tk_20261111.pdf',
              pages=6, visual_review='All six rendered pages inspected: no clipping, overlap or missing screenshots'),
    previous_sealed_directories='unchanged',
    validation_scope='Native Tk/local Make integration and finite simulation/trace export; no claim of arbitrary EDA-version compatibility or exhaustive formal proof',
    test_environment=dict(python=accepted['python'], tk=accepted['tk'], vcs='O-2018.09-1', verdi='O-2018.09-SP2',
                          unicode_distribution_locale='en_US.UTF-8',
                          note='Python 3.6 uses locale-dependent filesystem encoding; the initial bare SSH C-locale probe could not represent a Chinese project path. The Unicode-path acceptance uses an installed UTF-8 locale.'),
    redactions=['FLEXlm hardware host identifier removed from doctor.log before sealing'])
(HERE / 'release.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
files = {p.relative_to(HERE).as_posix(): digest(p.read_bytes()) for p in sorted(HERE.rglob('*'))
         if p.is_file() and p.name != 'SHA256SUMS.json' and '__pycache__' not in p.parts}
(HERE / 'SHA256SUMS.json').write_text(json.dumps(dict(algorithm='SHA256', files=files), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('RELEASE PASS: %d native checks, %d distribution checks, %d packaged files, %d sealed evidence files' %
      (len(accepted['checks']), len(distribution['checks']), packages['files'], len(files)))
