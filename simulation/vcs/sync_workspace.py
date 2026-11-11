"""Copy snapshots to an editable VM workspace using three-way conflict checks.

VM-only new cases and edits are retained. Conflicting changes on both sides
stop synchronization before any workspace source file is changed.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def sync(source, root):
    source, root = Path(source).resolve(), Path(root).resolve()
    work = root / 'workspace'
    work.mkdir(parents=True, exist_ok=True)
    with (root / '.sync.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = root / 'workspace_sync.json'
        previous = json.loads(state.read_text())['files'] if state.exists() else {}
        incoming = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts}
        copies, removals, conflicts, retained = [], [], [], []
        for rel, new in incoming.items():
            dst = work / rel
            if work not in dst.resolve().parents:
                raise ValueError('Workspace path escapes root: ' + rel)
            old, current = previous.get(rel), sha(dst)
            if current == new:
                continue
            if old == new:
                retained.append(rel)
            elif current == old and (current is not None or not dst.exists()):
                copies.append(rel)
            else:
                conflicts.append(rel)
        for rel, old in previous.items():
            if rel not in incoming:
                dst = work / rel
                if work not in dst.resolve().parents:
                    raise ValueError('Workspace path escapes root: ' + rel)
                if sha(dst) == old:
                    removals.append(rel)
                elif dst.exists():
                    retained.append(rel)
        launcher = root / 'Makefile'
        marker = '# Managed CMDBUF RD Make entry\n'
        if launcher.exists() and not launcher.read_text().startswith(marker):
            conflicts.append(str(launcher))
        if conflicts:
            raise ValueError('Both Windows and VM changed these files; merge before syncing:\n' +
                             '\n'.join(conflicts))
        for rel in copies:
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(source / rel), str(dst))
        for rel in removals:
            (work / rel).unlink()
        launcher.write_text(marker + '.DEFAULT_GOAL := help\n' +
                            'export EDA_RESULT_ROOT := ' + str(root / 'results') + '\n' +
                            'EDA_PROJECT_ROOT := ' + str(work) + '\n' +
                            'include ' + str(work / 'Makefile') + '\n')
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps({'source': str(source), 'files': incoming,
                                        'retained_vm_edits': retained}, indent=2) + '\n')
        os.replace(str(temporary), str(state))
        print('VM workspace: ' + str(work))
        print('Make entry: cd {} && make help'.format(root))
        print('Updated {} files; retained {} VM edits. Untracked VM files were kept.'.format(len(copies), len(retained)))


if __name__ == '__main__':
    sync(sys.argv[1], sys.argv[2])
