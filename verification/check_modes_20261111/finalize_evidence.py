"""Check the tested source/package identities and seal only this new receipt set."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


acceptance = read(HERE / 'evidence/check_acceptance.json')
gui = read(HERE / 'gui/acceptance.json')
distribution = read(HERE / 'distribution/acceptance.json')
for receipt in (acceptance, gui, distribution):
    assert receipt['status'] == 'PASS'
    assert all(c['passed'] for c in receipt['checks'])
assert len(acceptance['checks']) == 76
assert len(gui['checks']) == 19
assert len(distribution['checks']) == 14
sources = {name: digest((ROOT / name).read_bytes()) for name in acceptance['sources']}
assert sources == acceptance['sources'], 'Source differs from latest VCS acceptance'

packages = read(ROOT / 'packages/SHA256SUMS.json')
assert packages['files'] == 163
for name, expected in packages['archives'].items():
    data = (ROOT / 'packages' / name).read_bytes()
    assert len(data) == expected['bytes'] and digest(data) == expected['sha256'], name
with zipfile.ZipFile(str(ROOT / 'packages/cmdbuf-studio-portable.zip')) as archive:
    manifest_data = archive.read('cmdbuf-studio/PORTABLE_MANIFEST.json')
    assert digest(manifest_data) == distribution['manifest_sha256']
    manifest = json.loads(manifest_data.decode('utf-8'))
    for name, expected in manifest['files'].items():
        data = archive.read('cmdbuf-studio/' + name)
        assert digest(data) == expected, name
        assert not name.endswith('.local.json') and not name.startswith(('build/', 'verification/'))
        if name != 'README.md':
            current = (ROOT / name).read_bytes()
            if name.endswith('.sh'):
                current = current.replace(b'\r\n', b'\n')
            assert current == data, name
with tarfile.open(str(ROOT / 'packages/cmdbuf-studio-portable.tar.gz'), 'r:gz') as archive:
    assert archive.extractfile('cmdbuf-studio/PORTABLE_MANIFEST.json').read() == manifest_data
    for name, expected in manifest['files'].items():
        assert digest(archive.extractfile('cmdbuf-studio/' + name).read()) == expected, name

regressions = {}
for batch, count, mode, fatal in (
    ('20261111_210839_regress_4211bc', 12, 'transaction', False),
    ('20261111_211115_regress_ebe257', 8, 'strict', True),
    ('20261111_211835_replay_e71809', 1, 'transaction', False)):
    value = read(HERE / 'evidence/regressions' / batch / 'summary.json')
    assert value['status'] == 'PASS' and len(value['results']) == count
    assert value['check_policy'] == dict(check_mode=mode, assert_fatal=fatal)
    if 'replay' in batch:
        assert value['results'][0]['events_match_original']
    regressions[batch] = dict(status=value['status'], cases=count, policy=value['check_policy'])
main_phase = read(HERE / 'evidence/regressions/20261111_211401_main_phase_72050a/main_phase_acceptance.json')
assert main_phase['status'] == 'PASS' and all(main_phase['checks'].values())
assert main_phase['positive'] == 10 and main_phase['negative'] == 12

windows = (HERE / 'evidence/unit_windows.log').read_text(encoding='utf-8')
linux = (HERE / 'evidence/unit_linux.log').read_text(encoding='utf-8')
assert 'Ran 50 tests' in windows and 'OK (skipped=2)' in windows
assert 'Ran 50 tests' in linux and linux.strip().endswith('OK')
subprocess.check_call(['git', '-c', 'core.whitespace=cr-at-eol', 'diff', '--check'], cwd=str(ROOT))
subprocess.check_call(['git', 'diff', '--exit-code', 'HEAD', '--', 'rtl', 'formal',
    'verification/fsdb_formal_20261111', 'verification/portable_20261111',
    'verification/repository_20261111', 'verification/tk_studio_20261111'], cwd=str(ROOT))

manuals = {name: digest((ROOT / name).read_bytes()) for name in (
    'documents/37_cmdbuf_check_modes_20261111.docx',
    'documents/37_cmdbuf_check_modes_20261111.pdf')}
release = dict(
    status='PASS', created_utc=datetime.datetime.utcnow().isoformat() + 'Z',
    baseline_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT)).decode().strip(),
    working_tree='Implemented and tested locally; this receipt does not claim a new GitHub publication.',
    unit_linux=dict(total=50, passed=50),
    unit_windows=dict(total=50, passed=48, skipped=2, reason='Linux Bash discovery tests'),
    check_acceptance=dict(checks=76, status='PASS', fixture_scenarios=15, integration_runs=12,
                          directory=acceptance['directory'], coverage=acceptance['coverage']),
    gui_acceptance=dict(checks=19, regression_cases=6, status='PASS',
                        screenshots='gui/', note='Actual Tk callbacks and Make commands; both policies, nonfatal failures and Verdi coverage.'),
    regressions=regressions,
    main_phase=dict(positive=10, expected_negative=12, status='PASS'),
    distribution_acceptance=dict(checks=14, status='PASS', manifest_sha256=distribution['manifest_sha256'],
        note='Fresh Linux extraction in a Chinese/space path: three generic cases, one strict case, replay with exact events and snapshot bytes, real make gui window.'),
    distribution=packages, runtime_sha256=sources,
    docs=dict(sha256=manuals, pages=4, visual_review='All four final PDF pages inspected; both real GUI screenshots and tables are readable without clipping.'),
    previous_sealed_directories='unchanged; baseline RTL and canonical formal sources also unchanged',
    test_environment=dict(python='3.6.8 (Linux)', tk='8.5 (Linux)', vcs='O-2018.09-1',
                          verdi='O-2018.09-SP2', unicode_path_locale='en_US.UTF-8'),
    validation_scope='Finite simulation and GUI/Make acceptance. Transaction mode deliberately does not check exact pending/ready/req/R scheduling. No arbitrary-RTL correctness or exhaustive formal proof is claimed.',
    redactions=[
        'One FLEXlm hardware identifier line removed from distribution/doctor.log before sealing.',
        'One device license-configuration diagnostic replaced in evidence/integration/transaction_1/tc_directed_seed_17/novas_dump.log before sealing.'])
dump(HERE / 'release.json', release)
files = {p.relative_to(HERE).as_posix(): digest(p.read_bytes()) for p in sorted(HERE.rglob('*'))
         if p.is_file() and p.name != 'SHA256SUMS.json' and '__pycache__' not in p.parts}
dump(HERE / 'SHA256SUMS.json', dict(algorithm='SHA256', files=files))
print('RELEASE PASS: 76 VCS checks, 19 GUI checks, 14 distribution checks, 163 package files, %d evidence files' % len(files))
