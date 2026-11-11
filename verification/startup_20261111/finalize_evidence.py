"""Validate the final source and archive, then seal the startup evidence."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
def sha(data):return hashlib.sha256(data).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

accepted=read(HERE/'evidence/acceptance.json')
distributed=read(HERE/'distribution/acceptance.json')
checker=read(HERE/'evidence/checker_acceptance.json')
tc=read(HERE/'evidence/tc_acceptance.json')
for receipt,count in ((accepted,47),(distributed,16),(checker,76)):
    assert receipt['status']=='PASS' and len(receipt['checks'])==count
    assert all(c['passed'] for c in receipt['checks'])
assert tc['status']=='PASS' and tc['positive']==11 and tc['negative']==13
assert all(tc['checks'].values())
assert {k:sha((ROOT/k).read_bytes()) for k in accepted['source_sha256']}==accepted['source_sha256']
assert checker['sources']==accepted['source_sha256']
for name in ('protocol_summary.json','wavedrom_summary.json'):
    assert read(HERE/'compatibility'/name)['status']=='PASS'
final_wave=next(r for r in read(HERE/'evidence/waveform_evidence.json') if r['run']=='main_first_cycle_seed_17')
assert final_wave['signals']==read(HERE/'gui/figure_source.json')['signals']
assert read(HERE/'gui/acceptance.json')['status']=='PASS'

linux=(HERE/'evidence/unit_linux.log').read_text(encoding='utf-8')
windows=(HERE/'unit_windows.log').read_text(encoding='utf-8')
assert 'Ran 50 tests' in linux and linux.strip().endswith('OK')
assert 'Ran 50 tests' in windows and 'OK (skipped=2)' in windows

packages=read(ROOT/'packages/SHA256SUMS.json')
assert packages['files']==167
for name,metadata in packages['archives'].items():
    data=(ROOT/'packages'/name).read_bytes()
    assert len(data)==metadata['bytes'] and sha(data)==metadata['sha256']
with zipfile.ZipFile(str(ROOT/'packages/cmdbuf-studio-portable.zip')) as archive:
    manifest_data=archive.read('cmdbuf-studio/PORTABLE_MANIFEST.json')
    assert sha(manifest_data)==distributed['manifest_sha256']
    manifest=json.loads(manifest_data.decode('utf-8'))
    for name,expected in manifest['files'].items():
        data=archive.read('cmdbuf-studio/'+name)
        assert sha(data)==expected,name
        assert not name.endswith('.local.json') and not name.startswith(('build/','verification/'))
        if name!='README.md':
            source=(ROOT/name).read_bytes()
            if name.endswith('.sh'):source=source.replace(b'\r\n',b'\n')
            assert data==source,name
with tarfile.open(str(ROOT/'packages/cmdbuf-studio-portable.tar.gz'),'r:gz') as archive:
    assert archive.extractfile('cmdbuf-studio/PORTABLE_MANIFEST.json').read()==manifest_data
    for name,expected in manifest['files'].items():
        assert sha(archive.extractfile('cmdbuf-studio/'+name).read())==expected,name

previous=ROOT/'verification/check_modes_20261111'
previous_hashes=read(previous/'SHA256SUMS.json')
assert all(sha((previous/k).read_bytes())==v for k,v in previous_hashes['files'].items())
for name,expected in read(previous/'release.json')['docs']['sha256'].items():
    assert sha((ROOT/name).read_bytes())==expected,name
subprocess.check_call(['git','-c','core.whitespace=cr-at-eol','diff','--check'],cwd=str(ROOT))
subprocess.check_call(['git','diff','--exit-code','HEAD','--','rtl','formal',
    'verification/fsdb_formal_20261111','verification/portable_20261111',
    'verification/repository_20261111','verification/tk_studio_20261111'],cwd=str(ROOT))

release=dict(status='PASS',created_utc=datetime.datetime.utcnow().isoformat()+'Z',
    baseline_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=str(ROOT)).decode().strip(),
    publication='Local implementation and portable packages updated; no new GitHub push is claimed.',
    startup=dict(clock_initial=1,reset_active_low=True,reset_release_ps=1000,default_first_normal_posedge_ps=10000,
                 default_first_assignment_ps=12000,default_first_sample_ps=20000,reset_after_release='always high'),
    startup_acceptance_checks=47,checker_acceptance_checks=76,tc_positive=11,tc_expected_negative=13,
    fsdb_transition_comparisons=10,generic_regression_cases=9,distribution_checks=16,
    unit_linux=dict(total=50,passed=50),unit_windows=dict(total=50,passed=48,skipped=2),
    source_sha256=accepted['source_sha256'],distribution=packages,distribution_manifest_sha256=distributed['manifest_sha256'],
    replay='Exact new clock_reset.csv and legacy simulator replay both passed.',
    npi='Detected reset at 0 ps, release at 1000 ps, first working sample at 10000 ps; pattern 7 package generated. Formal engine was not run.',
    legacy_formats='protocol_read and wavedrom_read passed after isolating their explicit clock ownership.',
    docs=dict(pages=2,visual_review='Both final PDF pages and actual native waveform window inspected.',
              sha256={name:sha((ROOT/name).read_bytes()) for name in (
                  'documents/38_cmdbuf_startup_20261111.docx','documents/38_cmdbuf_startup_20261111.pdf')}),
    gui='Actual Tk waveform window; the recorded figure signal series equals the final VCS acceptance series.',
    previous_sealed_evidence='unchanged',test_tools=dict(vcs='O-2018.09-1',verdi='O-2018.09-SP2',python='3.6.8',tk='8.5'),
    redactions=['One FLEXlm hardware identifier line removed from distribution/doctor.log before sealing.'],
    scope='Finite startup, stimulus, checker, replay and NPI acceptance; not a proof for arbitrary RTL reset styles.')
write(HERE/'release.json',release)
files={p.relative_to(HERE).as_posix():sha(p.read_bytes()) for p in sorted(HERE.rglob('*'))
       if p.is_file() and p.name!='SHA256SUMS.json' and '__pycache__' not in p.parts}
write(HERE/'SHA256SUMS.json',dict(algorithm='SHA256',files=files))
print('RELEASE PASS: 47 startup checks, 76 checker checks, 11 positive / 13 expected-negative, 16 package checks; %d evidence files'%len(files))
