"""Seal this version's evidence, without modifying previous sealed receipts."""
import datetime,hashlib,json,subprocess,sys,tarfile,zipfile
from pathlib import Path
here=Path(__file__).resolve().parent;root=here.parents[1]
sys.path.insert(0,str(root/'simulation/vcs'))
import eda
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
actual=read(here/'evidence/acceptance.json')
assert actual['status']=='PASS' and all(c['passed'] for c in actual['checks'])
for rel,expected in actual['sources'].items():assert digest(root/rel)==expected,rel
checker=read(here/'evidence/checker_acceptance.json');tc=read(here/'evidence/tc_acceptance.json')
package=read(here/'package/acceptance.json')
assert checker['status']==tc['status']==package['status']=='PASS'
previous={}
for name in ('check_modes_20261111','startup_20261111'):
    folder=here.parent/name;manifest=read(folder/'SHA256SUMS.json')
    for rel,expected in manifest['files'].items():assert digest(folder/rel)==expected,(name,rel)
    previous[name]=dict(files=len(manifest['files']),status='UNCHANGED')
archives=read(root/'packages/SHA256SUMS.json')
for name,info in archives['archives'].items():assert digest(root/'packages'/name)==info['sha256'],name
with zipfile.ZipFile(str(root/'packages/cmdbuf-studio-portable.zip')) as archive:
    manifest=read(here/'package/acceptance.json')['manifest_sha256']
    raw=archive.read('cmdbuf-studio/PORTABLE_MANIFEST.json')
    assert hashlib.sha256(raw).hexdigest()==manifest
    files=json.loads(raw.decode())['files']
    for rel,expected in files.items():assert hashlib.sha256(archive.read('cmdbuf-studio/'+rel)).hexdigest()==expected,rel
with tarfile.open(str(root/'packages/cmdbuf-studio-portable.tar.gz')) as archive:
    for rel,expected in files.items():assert hashlib.sha256(archive.extractfile('cmdbuf-studio/'+rel).read()).hexdigest()==expected,rel
for name,ending in [('unit_windows.log','OK (skipped=3)'),('evidence/unit_linux.log','OK')]:
    assert (here/name).read_text(encoding='utf-8').strip().endswith(ending),name
manuals={p.relative_to(root).as_posix():digest(p) for p in (root/'documents').glob('39_cmdbuf_license_wait_20261111.*')}
assert len(manuals)==2
release=dict(status='PASS',created_utc=datetime.datetime.utcnow().isoformat()+'Z',
    baseline_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=str(root)).decode().strip(),
    publication='Local implementation and portable packages updated; no new GitHub push is claimed.',
    license_wait_limit_seconds=None,compile_flags=['-lca','-licqueue','+vcs+lic+wait'],
    simulation_flags=['+vcs+lic+wait'],coverage_flags=['+urg+lic+wait'],
    execution_timeout_default_seconds=120,execution_timeout_zero='unlimited',
    execution_start='VPI cbStartOfSimulation before HDL initial blocks',
    legacy_replay='original binary with no process deadline when start hook is absent',
    license_checks=len(actual['checks']),checker_checks=len(checker['checks']),
    tc_positive=tc['positive'],tc_expected_negative=tc['negative'],package_checks=len(package['checks']),
    unit_linux=dict(run=57,passed=57),unit_windows=dict(run=57,passed=54,skipped=3),
    actual_license_exhaustion_tested=False,controlled_wait_tests='delay beyond deadline, post-start timeout, zero timeout, parallel isolation, pre-start failure, Linux interrupt',
    portable=archives,manuals=manuals,previous_sealed=previous,
    sources=actual['sources'],documents_visual_qa=dict(pages=1,result='PASS',renderer='Word 2021 PDF export and Poppler PNG; visually inspected entire page'),
    gui_visual_qa=dict(image='package/regression_gui.png',result='PASS'))
eda.write_json(here/'release.json',release)
hashes={p.relative_to(here).as_posix():digest(p) for p in sorted(here.rglob('*'))
        if p.is_file() and p.name!='SHA256SUMS.json' and '__pycache__' not in p.parts}
eda.write_json(here/'SHA256SUMS.json',dict(algorithm='SHA256',files=hashes))
print('LICENSE RELEASE PASS: {} sealed files; {} package checks; {} portable files'.format(len(hashes),len(package['checks']),len(files)))
