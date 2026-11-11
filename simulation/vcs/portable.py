"""Create a clean, offline application distribution; never ship device settings or tools."""
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import zipfile
import eda

ROOT=eda.ROOT


def selected_files():
    paths=set(eda.sources())
    paths.update(ROOT/p for p in ('CMDBUF Studio.cmd','CMDBUF Studio.sh','eda.example.json','AGENTS.md'))
    paths.update((ROOT/'documents').glob('3[23456789]_*.pdf'))
    paths.update((ROOT/'documents').glob('3[23456789]_*.docx'))
    for p in list(paths):
        rel=p.relative_to(ROOT)
        if 'plans' in rel.parts or p.name.endswith('.local.json') or '__pycache__' in rel.parts:
            paths.remove(p)
    return sorted(paths)


QUICKSTART='''# CMDBUF Studio — native Tk / local Linux

On the Linux EDA workstation, load your site's tool/license environment, then:
    python3 -c "import tkinter; print(tkinter.TkVersion)"
    make doctor
    make smoke
    make gui

Requires Python >= 3.6, Tk >= 8.5, a Linux graphical session, GNU Make,
compatible C/C++ tools, and licensed VCS / Verdi with FSDB PLI and NPI.
No browser, Node.js, HTTP service, SSH connection, or pip packages are required.
Tk installation if missing: Debian/Ubuntu python3-tk; RHEL/CentOS python3-tkinter.

Optional site configuration:
    export EDA_TOOL_SETUP=/absolute/path/to/site_setup.sh
Normally no paths are entered: the Local tools page automatically detects the
project, launch directory, commands, installation roots, PLI/NPI, and display.
Special sites can opt in to machine-bound overrides in Advanced options.
Old VM profiles and overrides copied from a different device are ignored.
Settings are saved to ignored eda.local.json; DISPLAY inherits the current desktop.

Compile / simulate / open waves and coverage all use Make:
    make compile
    make regress SEEDS=1,2,42 JOBS=2 WAVES=1
    make regress CHECK_MODE=transaction ASSERT_FATAL=0 SEEDS=1,2,42
    make run CHECK_MODE=strict ASSERT_FATAL=1 CASE=tc_directed
    make check-test JOBS=2
    make wave REGRESSION=latest CASE=tc_random SEED=42
    make coverage REGRESSION=latest
    make wave-file FSDB=/absolute/path/to/input.fsdb

VCS compilation includes -lca -licqueue +vcs+lic+wait. Simulation uses
+vcs+lic+wait; coverage merging uses +urg+lic+wait. License waiting has no
deadline. TIMEOUT=120 counts execution only after VCS starts simulation;
TIMEOUT=0 disables that execution deadline too. The GUI shows startup/wait
and running states. Older archived binaries without the startup hook replay
without a process deadline, preserving their original code. See documents/39_*.

The GUI includes DUT import and instantiation, a main_phase-only SV editor,
per-case seed sweeps, a native waveform canvas, local Verdi coverage, and direct
NPI FSDB-to-Formal generation with one pattern per FSDB. FSDB viewing uses local
fsdb2vcd for the canvas; Formal extraction reads FSDB directly through NPI.
Generated Formal packages require the site's formal engine; generation is not a proof.

Windows: make.cmd gui opens the same native editor and existing VCD viewer.
EDA commands intentionally require Linux; there is no remote-execution fallback.

Checking scopes and fatal policy: documents/37_*. Current desktop manual: documents/36_*.
New runs use transaction mode; exact scheduling/pending timing is NOT_CHECKED.
Old saved plans keep strict mode. ASSERT_FATAL=0 continues but errors still FAIL.
Startup timing: documents/38_*. SV clock starts high at t=0; rst_n is low until
1 ns and remains high afterwards. Each TC keeps rst_n=1. Default first rising
edge is 10 ns; cycle=0 drives at 12 ns for sampling at 20 ns.
TC guide: documents/32_* (its older multi-cycle reset examples are superseded).
FSDB semantics: documents/34_*. Guides 33/35 describe the previous web/SSH UI.
Verify the archive: python3 simulation/vcs/portable.py --verify
No EDA binaries, licenses, credentials, machine settings, or prior results are shipped.
'''


def verify():
    manifest=json.loads((ROOT/'PORTABLE_MANIFEST.json').read_text(encoding='utf-8'))
    for rel,expected in manifest['files'].items():
        p=(ROOT/rel).resolve()
        if ROOT not in p.parents or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            raise ValueError('Package verification failed: '+rel)
    print('PACKAGE PASS: '+str(len(manifest['files']))+' files')
    return 0


def main(args=None):
    args=sys.argv[1:] if args is None else args
    if args==['--verify']:return verify()
    if args:raise ValueError('Use make dist, or portable.py --verify inside an extracted package')
    payload={p.relative_to(ROOT).as_posix():p.read_bytes() for p in selected_files()}
    # Shell launchers work after extraction even when authored on Windows.
    for name in payload:
        if name.endswith('.sh'):payload[name]=payload[name].replace(b'\r\n',b'\n')
    payload['README.md']=QUICKSTART.encode('utf-8')
    manifest=dict(format=1,product='CMDBUF Studio',built_at=datetime.datetime.utcnow().isoformat()+'Z',
                  files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())})
    payload['PORTABLE_MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    name='cmdbuf-studio-portable';prefix='cmdbuf-studio/'
    zip_path=out/(name+'.zip');tar_path=out/(name+'.tar.gz')
    with zipfile.ZipFile(str(zip_path),'w',zipfile.ZIP_DEFLATED) as archive:
        for rel,data in sorted(payload.items()):
            item=zipfile.ZipInfo(prefix+rel);item.create_system=3
            item.external_attr=((0o100755 if rel.endswith('.sh') else 0o100644)<<16)
            archive.writestr(item,data,compress_type=zipfile.ZIP_DEFLATED)
    with tarfile.open(str(tar_path),'w:gz') as archive:
        for rel,data in sorted(payload.items()):
            item=tarfile.TarInfo(prefix+rel);item.size=len(data);item.mtime=1700000000
            item.mode=0o755 if rel.endswith('.sh') else 0o644
            archive.addfile(item,io.BytesIO(data))
    receipt=dict(files=len(manifest['files']),archives={p.name:dict(bytes=p.stat().st_size,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (zip_path,tar_path)})
    (out/'SHA256SUMS.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,indent=2));return 0


if __name__=='__main__':
    sys.exit(main())
