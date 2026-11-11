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
    paths.update((ROOT/'documents').glob('3[2345]_*.pdf'))
    paths.update((ROOT/'documents').glob('3[2345]_*.docx'))
    for p in list(paths):
        rel=p.relative_to(ROOT)
        if 'plans' in rel.parts or p.name.endswith('.local.json') or '__pycache__' in rel.parts:
            paths.remove(p)
    return sorted(paths)


QUICKSTART='''# CMDBUF Studio portable edition

Linux with VCS/Verdi already configured in the shell:
    make doctor
    make smoke
    make gui

If your site requires an environment script:
    export EDA_TOOL_SETUP=/absolute/path/to/site_setup.sh
    make doctor

Windows (Python 3.6+ and OpenSSH client; no Windows Make or Node.js needed):
    .\\make.cmd gui
Choose SSH mode in connection settings. Enter user@host (or SSH alias),
an independent absolute remote directory, and optional tool setup path.
Alternatively:
    .\\make.cmd configure MODE=ssh HOST=user@eda ROOT=/home/user/cmdbuf_studio
    .\\make.cmd doctor
    .\\make.cmd smoke
Use an existing authenticated SSH connection (test with ssh user@eda).

GUI: DUT import, main_phase editor, per-case seed sweeps, FSDB/VCD viewers,
Verdi coverage, and direct NPI FSDB-to-Formal generation are included.
Native EDA execution requires Linux x86_64, licensed VCS/Verdi, GNU Make,
Python >=3.6, a compatible C/C++ toolchain and Verdi PLI/NPI components.
The GUI uses a modern browser; all web assets are bundled for offline use.
Verdi windows require a usable DISPLAY and desktop authorization.
SBY/Yosys/read_slang/ABC/Yices are optional, used only by generated bmc/cover jobs.

Machine settings: eda.local.json (created by GUI/configure, never distributed).
Site scripts should set the site's own license environment. No license, EDA
binary, cached simv, previous results, password, or machine configuration is bundled.
Run doctor to locate components; smoke compiles and runs real cases to check licensing.

Detailed Chinese Word/PDF guides: documents/32*, 33*, 34*, 35*.
Current source and protocol contracts are included; historical evidence is kept
in the original repository, separate from this clean runtime distribution.
Verify the archive with: python3 simulation/vcs/portable.py --verify
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
