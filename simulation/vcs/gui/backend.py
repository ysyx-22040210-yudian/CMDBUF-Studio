"""Local, asynchronous Make jobs shared by the native Tk views. No network service."""
import copy
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import model
import formal_model
import vcd

eda, ROOT, STATE = model.eda, model.ROOT, model.STATE


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('无效的结果编号')
    return value


def batch_path(batch):
    return model.results_root()/identifier(batch)


def result_path(batch, run):
    folder = batch_path(batch)
    if not any(r['id'] == run for r in eda.read_json(folder/'summary.json')['results']):
        raise ValueError('该用例不属于所选回归')
    return folder/'cases'/identifier(run)


def history():
    rows = []
    for path in sorted(model.results_root().glob('*/summary.json'), reverse=True)[:100]:
        try:
            s = eda.read_json(path)
            rows.append(dict(id=s['id'], status=s['status'], total=len(s['results']), passed=s['passed'],
                             coverage=s.get('coverage', {}).get('metrics_percent', {}),
                             waves=s.get('waves_enabled', True), dut=s.get('dut', {}).get('name', ''),
                             created=s.get('created_at', ''), seconds=sum(r.get('seconds', 0) for r in s['results'])))
        except (OSError, ValueError, KeyError):
            continue
    return rows


def save_plan(plan):
    plan = model.check_plan(copy.deepcopy(plan))
    # Device settings are kept exclusively in ignored eda.local.json.
    eda.write_json(STATE/'settings.json', dict(plan=plan))
    return plan


def save_dut(config, version):
    path = eda.HERE/'dut.json'
    if eda.digest(path) != version:
        raise ValueError('DUT 配置已被外部修改，请重新加载')
    config = model.dut_config.validate(config)
    eda.write_json(STATE/'backups'/('dut_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6]+'.json'), model.dut_config.load())
    eda.write_json(path, config)
    return eda.digest(path)


def import_rtl(paths, base=None):
    paths = [Path(p).resolve() for p in paths]
    if not paths or len(paths) > 1000:
        raise ValueError('一次导入 1..1000 个 RTL / 头文件')
    base = Path(base).resolve() if base else Path(os.path.commonpath([str(p.parent) for p in paths]))
    if any(not p.is_file() or p.suffix.lower() not in ('.sv', '.v', '.vh', '.svh', '.f') for p in paths):
        raise ValueError('导入只接受 .sv / .v / .vh / .svh / .f 文件')
    if sum(p.stat().st_size for p in paths) > 128*1024**2:
        raise ValueError('一次导入超过 128 MB')
    folder = eda.HERE/'dut_sources'/('import_'+eda.stamp()+'_'+eda.uuid.uuid4().hex[:6])
    copied = []
    for src in paths:
        dst = folder/src.relative_to(base)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(src), str(dst)); copied.append(dst)
    return dict(files=[p.relative_to(ROOT).as_posix() for p in copied if p.suffix.lower() in ('.v', '.sv')],
                include_dirs=sorted({p.parent.relative_to(ROOT).as_posix() for p in copied}))


def make_command(arguments):
    if sys.platform != 'linux':
        raise RuntimeError('EDA 任务需要在安装 VCS / Verdi 的 Linux 设备上运行。当前窗口可编辑工程和查看已有 VCD。')
    if not shutil.which('make'):
        raise RuntimeError('未找到 GNU Make，请在 Linux 上安装 make')
    # Make expands $ in command-line variables even with shell=False. Reject it
    # (and quotes/backticks/newlines used by Make recipes) before invoking a tool.
    for arg in arguments:
        if any(ch in str(arg) for ch in ('$', '`', '"', '\n', '\r', '\0')):
            raise ValueError('Make 参数路径不能含 $、反引号、双引号或换行，请改名后重试')
    return ['make', '--no-print-directory', 'PYTHON='+sys.executable]+list(arguments)


class Jobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.current = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def require_idle(self):
        with self.lock:
            if self.current and self.current['state'] == 'RUNNING':
                raise ValueError('当前任务仍在运行，请等待完成后再修改工程或启动下一任务')

    def update(self, job, **values):
        with self.lock:
            job.update(values)

    def log(self, job, text):
        with self.lock:
            job['log'] = (job['log']+text)[-300000:]
        with (STATE/'jobs'/(job['id']+'.log')).open('a', encoding='utf-8') as f:
            f.write(text)

    def invoke(self, job, arguments, check=True):
        cmd = make_command(arguments)
        env = os.environ.copy()
        env.update(PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', EDA_NO_OPEN='1', EDA_MODE='local')
        # Old GUI environments must never influence the execution host.
        for key in ('EDA_VM_HOST', 'EDA_VM_ROOT', 'EDA_SKIP_SYNC'):
            env.pop(key, None)
        self.log(job, '$ make '+' '.join(arguments)+'\n')
        process = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            self.log(job, line)
            batch = re.search(r'BATCH (\S+) TOTAL (\d+)', line)
            progress = re.search(r'PROGRESS .*? (\d+)/(\d+)', line)
            npi = re.search(r'NPI_PROGRESS (\d+)/(\d+)', line)
            if batch:
                self.update(job, batch=batch.group(1), total=int(batch.group(2)))
            if progress or npi:
                match = progress or npi
                self.update(job, completed=int(match.group(1)), total=int(match.group(2)))
        rc = process.wait()
        with self.lock:
            job['commands'].append(dict(argv=cmd, returncode=rc))
        if rc and check:
            raise RuntimeError('make '+arguments[0]+' 失败（退出码 '+str(rc)+'），详见任务日志')
        return rc

    def start(self, kind, payload=None):
        payload = copy.deepcopy(payload or {})
        with self.lock:
            self.require_idle()
            allowed = ('detect', 'compile', 'doctor', 'regress', 'coverage', 'wave', 'wave_file', 'wave_index',
                       'fsdb_inspect', 'fsdb_formal', 'import_fsdb', 'import_rtl')
            if kind not in allowed:
                raise ValueError('未知任务')
            if kind not in ('detect', 'import_fsdb', 'import_rtl', 'wave_index'):
                make_command([kind])
            if kind == 'regress':
                payload['plan'] = save_plan(payload['plan'])
            if kind in ('fsdb_inspect', 'fsdb_formal'):
                formal_model.selections(payload)
                if kind == 'fsdb_inspect' and len(payload['sources']) != 1:
                    raise ValueError('每次选择一个 FSDB 解析层级')
            if kind in ('coverage', 'wave'):
                if not (batch_path(payload['batch'])/'summary.json').is_file():
                    raise ValueError('找不到回归结果')
                if kind == 'wave' and not (result_path(payload['batch'], payload['run'])/'waves.fsdb').is_file():
                    raise ValueError('该用例未生成 FSDB；请开启波形后重新回归')
            job = dict(id=eda.stamp()+'_'+eda.uuid.uuid4().hex[:8], kind=kind, state='RUNNING',
                       started=time.time(), completed=0, total=0, stage='准备', log='', commands=[])
            (STATE/'jobs').mkdir(parents=True, exist_ok=True)
            self.current = job
            threading.Thread(target=self.work, args=(job, payload), daemon=True).start()
            return self.snapshot()

    def work(self, job, payload):
        try:
            kind = job['kind']
            progress = lambda message: self.update(job, stage=message)
            if kind == 'detect':
                progress('自动识别本机工具')
                if sys.platform == 'linux':
                    path = STATE/'tools.json'
                    self.invoke(job, ['env-info', 'INFO='+str(path)])
                    self.update(job, detected=eda.read_json(path))
                else:
                    self.update(job, detected=eda.platform_config.describe())
            elif kind in ('compile', 'doctor'):
                progress('VCS 编译' if kind == 'compile' else '检查本机工具链')
                self.invoke(job, [kind])
            elif kind == 'regress':
                plan = payload['plan']
                path = STATE/'plans'/(job['id']+'.json'); eda.write_json(path, plan)
                progress('运行回归 / 合并覆盖率')
                rc = self.invoke(job, ['regress', 'PLAN='+str(path), 'JOBS='+str(plan['jobs']),
                                      'TIMEOUT='+str(plan['timeout']), 'WAVES='+str(int(plan['waves']))], check=False)
                if job.get('batch'):
                    self.update(job, verdict=eda.read_json(batch_path(job['batch'])/'summary.json')['status'])
                if rc:
                    raise RuntimeError('回归包含失败用例或工具错误，请检查结果和任务日志')
            elif kind in ('wave', 'coverage'):
                progress('打开本机 Verdi')
                arguments = [kind, 'REGRESSION='+payload['batch']]
                if kind == 'wave':
                    arguments.append('RUN_ID='+payload['run'])
                self.invoke(job, arguments)
            elif kind == 'wave_file':
                progress('打开本机 Verdi')
                self.invoke(job, ['wave-file', 'FSDB='+str(Path(payload['path']).resolve())])
            elif kind == 'import_rtl':
                progress('导入 RTL 文件')
                self.update(job, imported=import_rtl(payload['paths'], payload.get('base')))
            elif kind == 'import_fsdb':
                imported = [formal_model.import_file(p, progress) for p in payload['paths']]
                self.update(job, imported=imported)
            elif kind in ('fsdb_inspect', 'fsdb_formal'):
                settings = model.state_read()
                if kind == 'fsdb_formal':
                    eda.write_json(STATE/'formal_selection.json', payload)
                request = formal_model.stage(payload, settings, job['id'], progress)
                progress('NPI 解析 FSDB' if kind == 'fsdb_inspect' else '逐拍提取并生成 Formal')
                self.invoke(job, ['fsdb-inspect' if kind == 'fsdb_inspect' else 'fsdb-formal', 'REQUEST='+request])
                progress('校验生成文件')
                self.update(job, formal_id=formal_model.fetch(settings, job['id']))
            elif kind == 'wave_index':
                path = Path(payload['path']).resolve()
                if path.suffix.lower() == '.fsdb':
                    progress('FSDB 转换为内置查看器的 VCD')
                    key = eda.hashlib.sha256((str(path)+str(path.stat().st_size)+str(path.stat().st_mtime_ns)).encode()).hexdigest()[:24]
                    dest = eda.OUT/'wave_cache'/(key+'.vcd')
                    if not dest.exists():
                        self.invoke(job, ['wave-convert', 'FSDB='+str(path), 'OUTPUT='+str(dest)])
                    path = dest
                if path.suffix.lower() != '.vcd':
                    raise ValueError('请选择 .vcd 或 .fsdb 波形')
                progress('建立四态波形索引')
                database = vcd.index(path, STATE/'vcd_index')
                self.update(job, database=str(database), wave_name=Path(payload['path']).name)
            self.update(job, state='DONE', stage='已完成')
        except Exception as exc:
            self.log(job, '\n'+str(exc)+'\n')
            self.update(job, state='ERROR', stage='执行失败', error=str(exc))
        finally:
            self.update(job, ended=time.time())
            with self.lock:
                receipt = copy.deepcopy(job)
            eda.write_json(STATE/'jobs'/(job['id']+'.json'), receipt)
