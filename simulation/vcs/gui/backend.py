"""Local, asynchronous Make jobs shared by the native Tk views. No network service."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import model
import formal_model
import vcd

eda, ROOT, STATE = model.eda, model.ROOT, model.STATE
COVERAGE_JOBS = ('coverage_details', 'coverage_minimize', 'coverage_formal')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('无效的结果编号')
    return value


def batch_path(batch):
    return model.results_root()/identifier(batch)


def result_path(batch, run):
    folder = batch_path(batch)
    path = folder/'cases'/identifier(run)
    summary = folder/'summary.json'
    if (path/'result.json').is_file() or (summary.is_file() and any(
            r['id'] == run for r in eda.read_json(summary)['results'])):
        return path
    raise ValueError('所选回归中没有该用例的完整结果')


def progress_row(batch):
    progress = eda.read_json(batch_path(batch)/'progress.json')
    return dict(id=batch, status=progress['state'], total=progress['total'],
                completed=progress['completed'], passed=progress.get('passed', 0),
                dut=progress.get('dut', ''), waves=progress.get('waves_enabled', True), partial=True)


def completed_runs(batch, offset, count):
    # progress.json commits the count only after the journal has been flushed.
    with (batch_path(batch)/'completed_runs.jsonl').open('rb') as stream:
        stream.seek(offset)
        rows = [json.loads(stream.readline().decode('utf-8')) for _ in range(count)]
        return rows, stream.tell()


def summary_row(summary):
    results = summary['results']
    coverage = summary.get('coverage', {})
    return dict(id=summary['id'], status=summary['status'], total=summary.get('total', len(results)),
                completed=len(results), passed=summary['passed'], partial=False,
                coverage=coverage.get('metrics_percent', {}) if coverage.get('status') == 'PASS' else {},
                waves=summary.get('waves_enabled', True), dut=summary.get('dut', {}).get('name', ''),
                created=summary.get('created_at', ''), seconds=sum(r.get('seconds', 0) for r in results))


def history(include_interrupted=False):
    rows = []
    root = model.results_root()
    folders = {path.parent for path in root.glob('*/summary.json')}
    if include_interrupted:
        folders.update(path.parent for path in root.glob('*/progress.json'))
    for folder in sorted(folders, reverse=True)[:100]:
        try:
            path = folder/'summary.json'
            if path.is_file():
                rows.append(summary_row(eda.read_json(path)))
            elif include_interrupted:
                row = progress_row(folder.name)
                if row['status'] == 'ERROR' and (folder/'completed_runs.jsonl').is_file():
                    rows.append(row)
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
        self.coverage_process = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def require_idle(self):
        with self.lock:
            if self.current and self.current['state'] == 'RUNNING':
                raise ValueError('当前任务仍在运行，结束后可启动下一任务')

    def require_editable_sources(self):
        with self.lock:
            job = self.current
            if (job and job['state'] == 'RUNNING' and job['kind'] in ('compile', 'regress')
                    and not job.get('sources_ready')):
                raise ValueError('正在固定本次编译的源码，请稍后保存或导入')

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
        if eda.platform_config.simple_path(eda.OUT):
            env['EDA_RESULT_ROOT'] = str(eda.OUT)
        # Old GUI environments must never influence the execution host.
        for key in ('EDA_VM_HOST', 'EDA_VM_ROOT', 'EDA_SKIP_SYNC'):
            env.pop(key, None)
        if job['kind'] == 'regress':
            env['CMDBUF_STUDIO_REGRESSION_STOP'] = str(self.regression_stop_file(job))
        self.log(job, '$ make '+' '.join(arguments)+'\n')
        coverage_task = job['kind'] in COVERAGE_JOBS
        process = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8',
                                   errors='replace', start_new_session=coverage_task)
        if coverage_task:
            with self.lock:
                self.coverage_process = process
                if job.get('cancel_requested'):
                    self.stop_coverage_process()
        for line in process.stdout:
            self.log(job, line)
            notice = line.strip()
            if notice == 'EDA_SOURCES_READY':
                self.update(job, sources_ready=True)
            elif notice == 'REGRESSION_FINALIZING':
                self.update(job, finalizing=True, stage='正在汇总用例 / 合并覆盖率')
            elif notice == 'REGRESSION_STOPPED_BEFORE_SIMULATION':
                self.update(job, stopped_before_simulation=True)
            if line.startswith('COVERAGE_STAGE '):
                self.update(job, stage=line[len('COVERAGE_STAGE '):].strip(), total=0, completed=0)
            if line.startswith('COVERAGE_RESULT '):
                self.update(job, coverage_record=line[len('COVERAGE_RESULT '):].strip())
            if line.startswith('EDA_PROCESS '):
                try:self.process_status(job,json.loads(line[len('EDA_PROCESS '):]))
                except (ValueError,KeyError,TypeError):pass
            batch = re.search(r'BATCH (\S+) TOTAL (\d+)', line)
            progress = re.search(r'PROGRESS .*? (\d+)/(\d+)', line)
            npi = re.search(r'(?:NPI|COVERAGE)_PROGRESS (\d+)/(\d+)', line)
            if batch:
                self.update(job, batch=batch.group(1), total=int(batch.group(2)), sources_ready=True)
            if progress or npi:
                match = progress or npi
                self.update(job, completed=int(match.group(1)), total=int(match.group(2)))
        rc = process.wait()
        with self.lock:
            if coverage_task:
                self.coverage_process = None
            job['commands'].append(dict(argv=cmd, returncode=rc))
        if rc and check:
            raise RuntimeError('make '+arguments[0]+' 失败（退出码 '+str(rc)+'），详见任务日志')
        return rc

    def stop_coverage_process(self):
        if self.coverage_process is not None:
            try:
                os.killpg(self.coverage_process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def cancel_coverage(self):
        with self.lock:
            job = self.current
            if not job or job['state'] != 'RUNNING' or job['kind'] not in COVERAGE_JOBS:
                return
            job.update(cancel_requested=True, stage='正在取消覆盖率分析')
            self.stop_coverage_process()

    def regression_stop_file(self, job):
        return (STATE/'jobs'/(job['id']+'.stop')).resolve()

    def stop_regression(self):
        with self.lock:
            job = self.current
            if (not job or job['kind'] != 'regress' or job['state'] != 'RUNNING'
                    or job.get('finalizing') or job.get('stop_requested')):
                return
            self.regression_stop_file(job).touch()
            job.update(stop_requested=True, stage='正在停止回归，保留已完成用例')

    def process_status(self,job,event):
        with self.lock:
            active=job.setdefault('processes',{})
            key=event['key']
            if event['state'] in ('FINISHED','TIMEOUT','INTERRUPTED','LAUNCH_ERROR'):
                active.pop(key,None)
            else:active[key]=event
            rows=list(active.values())
            if job.get('stop_requested') and not job.get('finalizing'):
                stage='正在停止回归，保留已完成用例'
            elif any(r['phase']=='compile' for r in rows):stage='VCS 编译 / 等待 License（不限时）'
            elif any(r['phase']=='coverage' for r in rows):stage='合并覆盖率 / 等待 License（不限时）'
            elif job.get('finalizing'):stage='正在汇总用例 / 合并覆盖率'
            elif any(r['phase']=='legacy_simulation' for r in rows):stage='旧归档重放 / 等待 License（全程不限时）'
            elif job.get('kind') not in COVERAGE_JOBS or any(r['phase']=='simulation' for r in rows):
                waiting=sum(r['state']=='WAITING_FOR_START' for r in rows)
                running=sum(r['phase']=='simulation' and r['state']=='RUNNING' for r in rows)
                stage=('仿真运行 %d · 等待 License / 启动 %d（等待不限时）'%(running,waiting)
                       if waiting or running else '整理结果 / 后续处理')
            else:
                return
            job['stage']=stage

    def start(self, kind, payload=None):
        payload = copy.deepcopy(payload or {})
        with self.lock:
            self.require_idle()
            allowed = ('detect', 'compile', 'doctor', 'regress', 'coverage', 'wave', 'wave_file', 'wave_index',
                       'fsdb_inspect', 'fsdb_formal', 'import_fsdb', 'import_rtl') + COVERAGE_JOBS
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
                if kind == 'coverage' and payload.get('analysis'):
                    identifier(payload['analysis'])
                elif not (batch_path(payload['batch'])/'summary.json').is_file():
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
                if not job.get('stop_requested'):
                    progress('运行回归 / 合并覆盖率')
                rc = self.invoke(job, ['regress', 'PLAN='+str(path), 'JOBS='+str(plan['jobs']),
                                      'TIMEOUT='+str(plan['timeout']), 'WAVES='+str(int(plan['waves'])),
                                      'CHECK_MODE='+plan['check_mode'],
                                      'ASSERT_FATAL='+str(int(plan['assert_fatal'])),
                                      'ENV_FATAL='+str(int(plan['env_fatal']))], check=False)
                if job.get('batch'):
                    summary = batch_path(job['batch'])/'summary.json'
                    if summary.is_file():
                        result = eda.read_json(summary)
                        self.update(job, verdict=result['status'])
                        if result['status'] == 'CANCELLED':
                            coverage = result['coverage']['status']
                            stage = {'PASS': '回归已停止 · 已合并通过用例的覆盖率',
                                     'NO_PASSING_CASES': '回归已停止 · 暂无通过用例可统计覆盖率'}.get(
                                         coverage, '回归已停止 · 覆盖率合并失败，详见日志')
                            self.update(job, state='CANCELLED', stage=stage)
                            return
                    else:
                        path = summary.with_name('progress.json')
                        progress = eda.read_json(path)
                        if progress['state'] != 'ERROR':
                            eda.write_json(path, dict(progress, state='ERROR'))
                        raise RuntimeError('回归中断，已完成的用例保留在结果窗口，详见任务日志')
                elif job.get('stopped_before_simulation'):
                    self.update(job, state='CANCELLED', stage='回归已停止 · 仿真尚未开始')
                    return
                if rc:
                    raise RuntimeError('回归包含失败、未完成验证的用例或工具错误，请查看检查范围和日志')
            elif kind in ('wave', 'coverage'):
                progress('打开本机 Verdi')
                arguments = [kind, 'COVERAGE_SET='+payload['analysis']] if payload.get('analysis') else [
                    kind, 'REGRESSION='+payload['batch']]
                if kind == 'wave':
                    arguments.append('RUN_ID='+payload['run'])
                self.invoke(job, arguments)
            elif kind in ('coverage_details', 'coverage_minimize'):
                batches = [identifier(batch) for batch in payload['batches']]
                if not batches or any(not (batch_path(batch) / 'summary.json').is_file() for batch in batches):
                    raise ValueError('找不到所选历史回归')
                arguments = ['coverage-details' if kind == 'coverage_details' else 'coverage-minimize',
                             'REGRESSIONS='+','.join(batches)]
                if kind == 'coverage_minimize':
                    arguments.append('SOLVE_SECONDS='+str(payload.get('seconds', 0)))
                self.invoke(job, arguments)
                if not job.get('coverage_record'):
                    raise RuntimeError('覆盖率分析没有生成记录')
            elif kind == 'coverage_formal':
                analysis = identifier(payload['analysis'])
                limit = formal_model.core.number(payload['max_edges'], '采样沿上限', 1, 1000000)
                style = formal_model.core.formal_format(payload.get('format', 'cycle'))
                self.invoke(job, ['coverage-formal', 'COVERAGE_SET='+analysis, 'FORMAL_ID='+job['id'],
                                  'MAX_EDGES='+str(limit), 'FORMAL_FORMAT='+style])
                progress('校验选集 Formal 生成包')
                self.update(job, formal_id=formal_model.fetch(model.state_read(), job['id']))
            elif kind == 'wave_file':
                progress('打开本机 Verdi')
                arguments = ['wave-file', 'FSDB='+str(Path(payload['path']).resolve())]
                if payload.get('scope'):
                    arguments.append('DUT_SCOPE='+payload['scope'])
                self.invoke(job, arguments)
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
            if job.get('cancel_requested'):
                self.update(job, state='CANCELLED', stage='已取消覆盖率分析')
            else:
                self.log(job, '\n'+str(exc)+'\n')
                self.update(job, state='ERROR', stage='执行失败', error=str(exc))
        finally:
            if job['kind'] == 'regress':
                try:
                    self.regression_stop_file(job).unlink()
                except OSError:
                    pass
            self.update(job, ended=time.time())
            with self.lock:
                receipt = copy.deepcopy(job)
            eda.write_json(STATE/'jobs'/(job['id']+'.json'), receipt)
