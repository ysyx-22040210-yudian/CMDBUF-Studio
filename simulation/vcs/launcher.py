"""Native Studio launcher; EDA commands always run on this Linux machine."""
import os
from pathlib import Path
import subprocess
import sys
import eda


def main(args=None):
    args = sys.argv[1:] if args is None else args
    args = args or ['help']
    if args[0] == 'gui':
        sys.path.insert(0, str(eda.HERE/'gui'))
        from desktop import main as desktop_main
        return desktop_main(args[1:])
    if args[0] == 'dist':
        import portable
        return portable.main(args[1:])
    if args[0] == 'configure':
        aliases = dict(TOOL_SETUP='tool_setup', VCS_HOME='vcs_home', VERDI_HOME='verdi_home',
                       DISPLAY='display', XAUTHORITY='xauthority')
        value = eda.platform_config.load()
        for arg in args[1:]:
            key, sep, val = arg.partition('=')
            if not sep or key not in aliases:
                raise ValueError('支持配置: '+', '.join(sorted(aliases)))
            value[aliases[key]] = val
        value['profile'] = 'manual' if args[1:] else 'auto'
        eda.platform_config.save(value)
        print('Saved local tool settings: eda.local.json')
        return 0
    if os.name == 'nt':
        raise RuntimeError('请在 Linux EDA 设备运行 make '+args[0]+'。Windows 可使用 make.cmd gui 编辑工程。')
    return subprocess.call(['make']+args, cwd=str(eda.ROOT))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print('STUDIO: '+str(exc), file=sys.stderr)
        sys.exit(2)
