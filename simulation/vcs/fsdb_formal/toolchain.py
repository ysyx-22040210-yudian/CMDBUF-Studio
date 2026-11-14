"""Locate the shipped OSS CAD Suite and check its formal engines."""
import argparse
from contextlib import contextmanager
from importlib import import_module
import os
from pathlib import Path
import platform
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace


RELEASE = '2026-07-27'
LINUX_DOWNLOADS = 'https://github.com/ysyx-22040210-yudian/CMDBUF-Studio/releases/latest'
ASSETS = {
    'x86_64': ('x64', 'd5cd8fb0276b51ae6b35945e70e03f222d82a863f2e3523b33b937be0ea416f4'),
    'aarch64': ('arm64', 'dbdcbfec4b0d642e2f3ebf6ce1d9e6068a0d7732b45b798325b50ca396a101b5'),
}
TOOLS = ('yosys', 'sby', 'yosys-abc', 'yosys-smtbmc', 'yosys-witness', 'yices-smt2')
SMTBMC_STDERR = '--smtbmc-with-stderr'
VERSION_PROBES = (
    ('yosys', '-V', 'Yosys '),
    ('sby', '--version', 'SBY '),
    ('yices-smt2', '--version', 'Yices '),
)


def runtime_identity():
    suite = bundled_suite()
    override = os.environ.get('CMDBUF_FORMAL_HOME')
    if suite is None and override:
        suite = Path(override).expanduser().resolve()
    if suite:
        programs = [suite/'libexec'/name for name in ('slang', 'yosys')]
    else:
        programs = [Path(path) for path in (shutil.which(name) for name in ('slang', 'yosys')) if path]
    return tuple((str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
                 for path in programs if path.is_file())


def architecture():
    machine = platform.machine().lower()
    return {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)


def bundled_suite():
    arch = architecture()
    if arch not in ASSETS:
        return None
    suffix, _ = ASSETS[arch]
    for parent in Path(__file__).resolve().parents:
        runtime = parent/'formal_runtime'
        if runtime.is_dir():
            suite = runtime/('linux-'+suffix)/'oss-cad-suite'
            return suite if suite.is_dir() else None
    return None


@contextmanager
def suite_path(suite):
    # The upstream wrappers resolve their own directory before starting their
    # Python. A path containing shell separators needs a private alias whose
    # suite subdirectories point at the packaged files.
    if all(ord(ch) < 128 and (ch.isalnum() or ch in '/-._') for ch in str(suite)):
        yield suite
        return
    with tempfile.TemporaryDirectory(prefix='cmdbuf-formal-suite-', dir='/tmp') as folder:
        alias = Path(folder)
        for path in suite.iterdir():
            if path.name != 'bin':
                (alias/path.name).symlink_to(path, target_is_directory=path.is_dir())
        (alias/'bin').mkdir()
        entries = TOOLS + ('tabbypy3',) + (('slang',) if (suite/'bin'/'slang').is_file() else ())
        for name in entries:
            shutil.copy2(str(suite/'bin'/name), str(alias/'bin'/name))
        yield alias


def smtbmc_with_stderr(entry, args):
    """Run the bundled SMTBMC with SMT replies and solver diagnostics on separate streams."""
    entry = Path(entry).resolve()
    sys.path.insert(0, str(entry.parent.parent/'share/yosys/python3'))
    smtio = import_module('smtio')

    def popen(*positional, **keywords):
        # Yosys merges solver stderr into its SMT-LIB input pipe. Inherit the
        # engine's stderr instead, which SBY already captures in the job log.
        if keywords.get('stderr') == subprocess.STDOUT:
            keywords['stderr'] = None
        return subprocess.Popen(*positional, **keywords)

    smtio.subprocess = SimpleNamespace(
        Popen=popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
        check_output=subprocess.check_output)
    sys.argv = [str(entry)] + args
    runpy.run_path(str(entry), run_name='__main__')


def require_linux():
    if sys.platform != 'linux':
        raise RuntimeError('开源 Formal 工具链在 Linux 上运行。')


@contextmanager
def interruptible():
    cancelled = False

    def interrupt(signum, frame):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def environment():
    require_linux()
    suite = bundled_suite()
    override = os.environ.get('CMDBUF_FORMAL_HOME')
    if suite is None and override:
        suite = Path(override).expanduser().resolve()
    env = os.environ.copy()
    search = env.get('PATH', os.defpath)
    # Keep unrelated shell settings from overriding the selected SBY binaries.
    env.update(YOSYS='yosys', ABC='yosys-abc', SMTBMC='yosys-smtbmc', WITNESS='yosys-witness')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for name in ('MAKEFLAGS', 'MFLAGS', 'PYTHONPATH', 'PYTHONHOME'):
        env.pop(name, None)
    if suite is not None:
        bindir = suite/'bin'
        missing = [name for name in TOOLS + ('tabbypy3',) if not shutil.which(name, path=str(bindir))]
        if missing:
            raise RuntimeError('工具目录 %s 缺少 %s' % (suite, ', '.join(missing)))
        with suite_path(suite) as active:
            env['PATH'] = str(active/'bin')+os.pathsep+search
            env['CMDBUF_FORMAL_TOOLCHAIN'] = str(suite)
            smtbmc = active/'libexec/yosys-smtbmc'
            if smtbmc.is_file() and (active/'share/yosys/python3/smtio.py').is_file():
                # SBY expands SMTBMC in a shell command, so quote each path.
                env['SMTBMC'] = ' '.join(shlex.quote(str(arg)) for arg in (
                    active/'bin/tabbypy3', Path(__file__).resolve(), SMTBMC_STDERR, smtbmc))
            yield env
        return
    missing = [name for name in TOOLS if not shutil.which(name, path=search)]
    if missing:
        raise RuntimeError('当前目录缺少 %s 的 Formal 运行时。请从 GitHub Release 的 Assets 下载对应架构的'
                           ' cmdbuf-studio-linux-*.tar.gz 完整包。源码 ZIP 不含运行时。\n%s' %
                           (architecture(), LINUX_DOWNLOADS))
    yield env


def versions(env):
    result = {}
    for name, flag, prefix in VERSION_PROBES:
        proc = subprocess.run([name, flag], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding='utf-8', errors='replace', timeout=30)
        version = next((line for line in proc.stdout.splitlines() if line.startswith(prefix)), '')
        if proc.returncode or not version:
            detail = (proc.stdout + proc.stderr).strip()
            raise RuntimeError('%s 无法读取版本（退出码 %d）\n%s' % (name, proc.returncode, detail))
        for line in dict.fromkeys(proc.stderr.splitlines()):
            print(line, file=sys.stderr, flush=True)
        result[name] = dict(version=version,
                            path=shutil.which(name, path=env['PATH']))
        if env.get('CMDBUF_FORMAL_TOOLCHAIN'):
            result[name]['path'] = str(Path(env['CMDBUF_FORMAL_TOOLCHAIN'])/'bin'/name)
    return result


def doctor(env):
    for item in versions(env).values():
        print('%s  (%s)' % (item['version'], item['path']), flush=True)
    # Compile and exercise the same frontend and both solver paths as the export.
    with tempfile.TemporaryDirectory(prefix='cmdbuf-formal-doctor-', dir='/tmp') as folder:
        path = Path(folder)
        (path/'probe.sv').write_text(
            'module probe(input logic clk, input logic fault);\n'
            '  logic [1:0] state = 0;\n'
            '  always @(posedge clk)\n'
            '    if (fault) state <= 3;\n'
            "    else if (state != 2) state <= state + 1'b1;\n"
            '  assume property (@(posedge clk) !fault);\n'
            '  assert property (@(posedge clk) state != 3);\n'
            '  cover property (@(posedge clk) state == 2);\n'
            'endmodule\n', encoding='utf-8')
        for mode, engine in (('bmc', 'abc bmc3'), ('cover', 'smtbmc yices')):
            config = path/(mode+'.sby')
            config.write_text(
                '[options]\nmode %s\ndepth 4\ntimeout 45\n\n[engines]\n%s\n\n'
                '[script]\nread_slang --top probe probe.sv\nprep -top probe\n'
                'async2sync\ndffunmap\nchformal -lower\n\n[files]\nprobe.sv\n'
                % (mode, engine), encoding='utf-8')
            work = path/mode
            proc = subprocess.run(['sby', '-d', str(work), config.name], cwd=folder, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  encoding='utf-8', errors='replace')
            status = work/'status'
            if proc.returncode or not status.is_file() or status.read_text().split()[:1] != ['PASS']:
                raise RuntimeError('工具链 %s 自检失败（SBY 退出码 %d）\n%s' % (mode, proc.returncode, proc.stdout[-6000:]))
            print('工具链 %s 自检 PASS' % mode, flush=True)


def main():
    # Invoked by the bundled Python from SBY. Keep its CLI and exit code.
    if sys.argv[1:2] == [SMTBMC_STDERR]:
        return smtbmc_with_stderr(sys.argv[2], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-sby', choices=('run_bmc.sby', 'run_cover.sby'))
    args = parser.parse_args()
    try:
        with interruptible(), environment() as env:
            if args.run_sby:
                return subprocess.call(['sby', '-f', args.run_sby], env=env)
            print('FORMAL_STAGE 检查开源工具链', flush=True)
            doctor(env)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('已取消工具链任务。', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
