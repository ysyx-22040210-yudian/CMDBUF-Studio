"""Run a CMDBUF Formal export through its generated SymbiYosys checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import toolchain


INPUTS = ('run_bmc.sby', 'run_cover.sby', 'slang.f', 'formal_top.sv', 'sby_properties.sv',
          'trace_properties.sv', 'sby_adaptation.json', 'summary.json', 'integration.json')
TERMINAL = r'C_END_P\d+_S\d+'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('无效的 Formal 编号')
    return value


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_receipt(directory, record):
    temp = directory/'proof.json.tmp'
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temp.replace(directory/'proof.json')


def copy_inputs(package, source, manifest):
    rtl = {name for name in manifest if name.startswith('rtl_snapshot/')}
    if not rtl:
        raise ValueError('Formal 包缺少 DUT 快照')
    files = set(INPUTS)
    files.update(rtl)
    for name in sorted(files):
        original = (package/name).resolve()
        dest = (source/name).resolve()
        if package not in original.parents or source not in dest.parents or name not in manifest:
            raise ValueError('Formal 包清单中的路径无效: '+name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with original.open('rb') as incoming, dest.open('wb') as outgoing:
            for chunk in iter(lambda: incoming.read(4*1024*1024), b''):
                digest.update(chunk)
                outgoing.write(chunk)
        if digest.hexdigest() != manifest[name]:
            raise ValueError('Formal 包文件校验失败: '+name)


def terminal_covers(summary, source):
    expected = {'C_END_P%d_S%d' % (pattern['pattern'], segment['segment'])
                for pattern in summary['patterns'] for segment in pattern['segments']}
    checker = (source/'sby_properties.sv').read_text(encoding='utf-8')
    declared = re.findall(r'^\s*('+TERMINAL+r'):\s*cover property\b', checker, re.MULTILINE)
    if not expected or sorted(expected) != sorted(declared) or len(expected) != summary['counts']['terminal_covers']:
        raise ValueError('终点 cover 与 pattern 清单不一致')
    if not re.search(r'\bassert property\b', checker):
        raise ValueError('Formal 包没有输出断言')
    return expected


def execute(source, mode, env, timeout):
    directory = source.parent
    config = source/('run_'+mode+'.sby')
    # SBY uses shell commands internally. Keep the workdir relative for paths with spaces.
    if timeout is not None:
        script = config.read_text(encoding='utf-8')
        # The generated configurations put timeout on a dedicated [options] line.
        script, replaced = re.subn(r'^timeout[ \t]+\d+[ \t]*\n', '', script, count=1, flags=re.MULTILINE)
        if replaced != 1:
            raise ValueError('Formal 包缺少可配置的 timeout 选项')
        if timeout:
            script = script.replace('[options]\n', '[options]\ntimeout %d\n' % timeout, 1)
        config = source/('effective_'+mode+'.sby')
        config.write_text(script, encoding='utf-8')
    argv = ['sby', '-d', '../'+mode, config.name]
    name = mode+'.log'
    reached = set()
    failed = []
    with (directory/name).open('w', encoding='utf-8', buffering=1) as log:
        log.write('$ '+' '.join(argv)+'\n')
        start = time.monotonic()
        proc = subprocess.Popen(argv, cwd=str(source), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                                start_new_session=True)
        try:
            with proc.stdout:
                for line in proc.stdout:
                    log.write(line)
                    print(line, end='', flush=True)
                    if re.search(r'\breached cover statement\b', line, re.IGNORECASE):
                        reached.update(re.findall(r'\b'+TERMINAL+r'\b', line))
                    if 'summary:' in line and 'failed assertion' in line.lower():
                        failed.append(line.partition('summary:')[2].strip())
            rc = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()  # SBY propagates SIGTERM to its solver process groups.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
    status_file = directory/mode/'status'
    words = status_file.read_text(encoding='utf-8').split() if status_file.is_file() else []
    status = words[0] if words else 'ERROR'
    if status not in ('PASS', 'FAIL', 'TIMEOUT', 'UNKNOWN', 'CANCELLED') or len(words) < 2 or words[1] != str(rc):
        status = 'ERROR'
    if status == 'PASS' and rc:
        status = 'ERROR'
    return dict(status=status, returncode=rc, seconds=round(time.monotonic()-start, 3), log=name,
                reached=sorted(reached), failed_assertions=failed,
                vcds=sorted(p.relative_to(directory).as_posix() for p in (directory/mode).glob('engine_*/trace*.vcd')))


def verdict(bmc, cover, missing):
    if bmc == 'FAIL':
        return 'FAIL'
    if 'ERROR' in (bmc, cover):
        return 'ERROR'
    if 'CANCELLED' in (bmc, cover):
        return 'CANCELLED'
    if bmc == 'PASS' and cover == 'PASS' and not missing:
        return 'PASS'
    return 'INCOMPLETE'


def prove(package, output, run_id=None, timeout=None):
    package = package.expanduser().resolve()
    manifest_bytes = (package/'SHA256SUMS.json').read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    summary = read_json(package/'summary.json')
    export_id = identifier(summary['id'])
    run_id = identifier(run_id or time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    directory = output.expanduser().resolve()/export_id/run_id
    directory.mkdir(parents=True, exist_ok=False)
    source = directory/'source'
    source.mkdir()
    record = dict(id=run_id, export_id=export_id, status='RUNNING',
                  package=str(package), package_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                  started_at=time.time(), timeout_override_seconds=timeout,
                  bmc=dict(status='NOT_RUN'), cover=dict(status='NOT_RUN'))
    write_receipt(directory, record)
    try:
        print('FORMAL_STAGE 正在校验验证包和 DUT 快照', flush=True)
        copy_inputs(package, source, manifest)
        summary = read_json(source/'summary.json')
        if summary['id'] != export_id:
            raise ValueError('Formal 包编号与校验的清单不一致')
        expected = terminal_covers(summary, source)
        integration = read_json(source/'integration.json')
        record.update(format=summary.get('format', 'cycle'), dut=summary['dut_name'], depth=integration['depth'],
                      checker_sha256=manifest['sby_properties.sv'], terminal_covers=sorted(expected),
                      unreached_covers=sorted(expected))
        (source/'SHA256SUMS.json').write_bytes(manifest_bytes)
        with toolchain.environment() as env:
            record['tools'] = toolchain.versions(env)
            print('FORMAL_STAGE BMC 检查输出断言 · %d 步' % record['depth'], flush=True)
            record['bmc'] = execute(source, 'bmc', env, timeout)
            write_receipt(directory, record)
            if record['bmc']['status'] == 'PASS':
                print('FORMAL_STAGE Cover 检查 %d 个轨迹终点' % len(expected), flush=True)
                record['cover'] = execute(source, 'cover', env, timeout)
                record['unreached_covers'] = sorted(expected.difference(record['cover']['reached']))
        record['status'] = verdict(record['bmc']['status'], record['cover']['status'], record['unreached_covers'])
    except KeyboardInterrupt:
        record['status'] = 'CANCELLED'
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        record.update(status='ERROR', error=str(exc))
        print(str(exc), file=sys.stderr, flush=True)
    record['finished_at'] = time.time()
    write_receipt(directory, record)
    print('FORMAL_RESULT %s %s' % (record['status'], directory/'proof.json'), flush=True)
    return {'PASS': 0, 'FAIL': 1, 'CANCELLED': 130}.get(record['status'], 2)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group()
    select.add_argument('--package')
    select.add_argument('--export-id')
    parser.add_argument('--project', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--run-id')
    parser.add_argument('--timeout', type=int, help='seconds per stage, 0 for unlimited; default from the export')
    return parser.parse_args()


def main():
    args = arguments()
    try:
        toolchain.require_linux()
        if args.timeout is not None and args.timeout < 0:
            raise ValueError('Formal 超时须为非负整数，0 表示不限时')
        package = Path(args.package or '.')
        output = Path(args.output or 'proof_runs')
        if args.project:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from platform_config import ROOT, result_root
            base = result_root()
            output = Path(args.output) if args.output else base/'formal_runs'
            if args.export_id:
                key = identifier(args.export_id)
                package = base/'fsdb_formal'/key
                if not (package/'summary.json').is_file():
                    package = ROOT/'build/vcs/studio/formal_exports'/key
            elif not args.package:
                raise ValueError('请指定 FORMAL_ID 或 FORMAL_PACKAGE（已解压目录）')
        elif args.export_id:
            raise ValueError('--export-id 需要在 CMDBUF Studio 工程中运行')
        with toolchain.interruptible():
            return prove(package, output, args.run_id, args.timeout)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
