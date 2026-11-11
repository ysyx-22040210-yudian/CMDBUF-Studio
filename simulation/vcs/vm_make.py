"""Portable Make launcher: local Linux or a configured SSH EDA workspace."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import eda


def main():
    args = sys.argv[1:] or ['help']
    if args == ['gui']:
        executable = Path(sys.executable)
        windowed = executable.with_name('pythonw.exe') if os.name == 'nt' else executable
        subprocess.Popen([str(windowed if windowed.exists() else executable),
                          str(eda.HERE / 'gui/server.py'), '--desktop'],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        print('Opening CMDBUF Studio')
        return 0
    if args[0] == 'dist':
        import portable
        return portable.main(args[1:])
    if args[0] == 'configure':
        aliases=dict(MODE='mode',HOST='host',ROOT='vm_root',DISPLAY='display',XAUTHORITY='xauthority',
                     TOOL_SETUP='tool_setup',VCS_HOME='vcs_home',VERDI_HOME='verdi_home')
        values=eda.platform_config.load()
        for arg in args[1:]:
            key,sep,val=arg.partition('=')
            if not sep or key not in aliases:raise ValueError('Unknown configure argument: '+arg)
            values[aliases[key]]=val
        eda.platform_config.save(values)
        print('Saved machine settings in eda.local.json (excluded from portable packages).')
        return 0
    settings = eda.platform_config.load()
    if args in (['help'],['--help']) and settings['mode']=='ssh' and not settings['host']:
        print('CMDBUF Studio: make.cmd gui | configure | dist')
        print('Configure once: make.cmd configure MODE=ssh HOST=user@eda ROOT=/home/user/cmdbuf_studio')
        print('Optional TOOL_SETUP=/path/to/site_setup.sh; then make.cmd doctor and make.cmd smoke')
        return 0
    eda.platform_config.validate(settings)
    if settings['mode']=='local':
        if args==['sync']:
            print('Local Linux workspace: no SSH or source synchronization required.');return 0
        if args[0]=='fetch':
            values=dict(a.split('=',1) for a in args[1:] if '=' in a)
            print(eda.select_regression(values.get('REGRESSION','latest')));return 0
        return subprocess.call(['make']+args,cwd=str(eda.ROOT))
    host,root = settings['host'],settings['vm_root']
    if host.startswith('-') or not eda.re.fullmatch(r'/[A-Za-z0-9_./-]+', root) or '..' in Path(root).parts:
        raise ValueError('Invalid EDA_VM_HOST or EDA_VM_ROOT')
    if args == ['sync']:
        return eda.main(['--vm', '--host', host, '--vm-root', root, 'sync'])
    if args[0] == 'new-case':
        values=dict(arg.split('=',1) for arg in args[1:] if '=' in arg)
        if len(values)!=len(args)-1 or set(values)-{'CASE','TEMPLATE'} or not values.get('CASE'):
            raise ValueError('Use make.cmd new-case CASE=my_read [TEMPLATE=tc_directed]')
        eda.main(['new-case',values['CASE'],'--template',values.get('TEMPLATE','tc_directed')])
        return eda.main(['--vm','--host',host,'--vm-root',root,'sync'])
    if args[0] in ('fetch', 'open-report'):
        values = dict(arg.split('=', 1) for arg in args[1:] if '=' in arg)
        if len(values) != len(args) - 1 or set(values) - {'REGRESSION', 'ALL'}:
            raise ValueError('Use REGRESSION=latest|batch_id and optionally ALL=1')
        rid = values.get('REGRESSION', 'latest')
        forwarded = ['--vm', '--host', host, '--vm-root', root, 'fetch', '--regression', rid]
        if values.get('ALL') == '1':
            forwarded.append('--all')
        rc = eda.main(forwarded)
        if rc == 0 and args[0] == 'open-report':
            return eda.main(['open-report', '--regression', rid])
        return rc
    if args[0] in ('compile','build','run','regress','smoke','doctor','validate','fsdb-inspect','fsdb-formal') and not os.environ.get('EDA_SKIP_SYNC'):
        rc=eda.main(['--vm','--host',host,'--vm-root',root,'sync'])
        if rc:return rc
    script = 'set -e\n' + eda.platform_config.shell_exports(settings) + 'cd ' + shlex.quote(root) + '\n'
    script += 'test -f Makefile || { echo "Run make.cmd sync first"; exit 2; }\n'
    script += 'make ' + ' '.join(shlex.quote(arg) for arg in args) + '\n'
    if args[0] == 'diagram':
        result = eda.ssh(host, script, capture=True)
        message = result.stdout.decode('utf-8', errors='replace')
        remote = next((line[8:] for line in message.splitlines() if line.startswith('DIAGRAM ')), None)
        if not remote:
            raise RuntimeError('make diagram did not produce a preview')
        dest = eda.ROOT / 'build/vcs/previews' / Path(remote).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(['scp', '-q', '-o', 'BatchMode=yes', host + ':' + remote, str(dest)])
        if not os.environ.get('EDA_NO_OPEN'):
            import webbrowser
            webbrowser.open(dest.as_uri())
        print(str(dest))
        return 0
    return eda.ssh(host, script).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print('VM MAKE ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(2)
