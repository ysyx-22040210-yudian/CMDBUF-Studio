"""Controls and run history for the local open-source Formal tools."""
import datetime
from pathlib import Path
import re
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, ttk

import backend
import formal_model
from code_editor import CodeEditor
from widgets import tree


eda = backend.eda
STATUS = {
    'RUNNING': '运行中', 'PASS': '通过', 'FAIL': '失败', 'COMPLETE': '目标已找到',
    'PARTIAL': '部分目标已找到', 'TIMEOUT': '超时', 'UNKNOWN': '未确定', 'ERROR': '工具错误',
    'CANCELLED': '已取消', 'INCOMPLETE': '未完成', 'NOT_RUN': '未运行',
    'NO_WITNESSES': '未找到轨迹', 'NOT_FOUND_WITHIN_DEPTH': '深度内未找到',
    'NOT_SELECTED': '未选择', 'WITNESS': '已找到轨迹', 'REPLAY_REJECTED': '轨迹提取失败',
    'UNSUPPORTED': '当前模型不支持',
}
TASKS = {'cover': 'Cover 搜索', 'bmc': 'Assert 有界检查', 'proof': '验证包证明'}


def active(widget, enabled):
    widget.state(['!disabled' if enabled else 'disabled'])


def summary_status(status):
    return STATUS.get(status, status)


def archived_file(folder, relative):
    if not folder or not relative:
        return None
    path = (folder/relative).resolve()
    return path if folder.resolve() in path.parents and path.is_file() else None


def preview_text(path, limit=30000, tail=False):
    with path.open('rb') as stream:
        size = path.stat().st_size
        if tail:
            stream.seek(max(0, size-limit))
        text = stream.read(limit).decode('utf-8', errors='replace')
    if size > limit:
        indicator = '[预览末尾 {} KB，完整文件可另存]\n\n'.format(limit//1000)
        text = indicator + text if tail else text + '\n\n[仅预览前 {} KB]'.format(limit//1000)
    return text


def save_file(studio, path):
    if path is None:
        return
    name = filedialog.asksaveasfilename(initialfile=path.name, title='另存运行文件', parent=studio.root)
    if name and Path(name).resolve() != path:
        shutil.copyfile(str(path), name)
        studio.status.set('已保存 '+name)


def wave(studio, path, name, step=None):
    if path:
        studio.launch('wave_index', dict(path=str(path), name=name,
                                        formal_step=step if isinstance(step, int) and step >= 0 else None))


def job_title(job):
    return {'formal_cover': 'Cover 搜索', 'formal_bmc': 'Assert 有界检查', 'formal_prove': '验证包证明',
            'formal_doctor': '工具链自检', 'formal_replay': 'VCS 回放',
            'formal_commercial': '商业 Formal'}.get(job['kind'], job['kind'])


class Activity:
    """The current Formal job remains visible while the user browses history."""
    def __init__(self, studio, parent):
        self.studio = studio
        self.frame = ttk.Frame(parent, padding=(14, 9), style='Card.TFrame')
        self.frame.pack(fill='x', pady=(0, 12))
        self.frame.columnconfigure(0, weight=1)
        self.title = tk.StringVar(value='任务状态')
        self.phase = tk.StringVar(value='就绪 · SymbiYosys / Yosys-Slang')
        ttk.Label(self.frame, textvariable=self.title, style='Card.TLabel', font=(studio.ui, 11, 'bold')).grid(
            row=0, column=0, sticky='w')
        ttk.Label(self.frame, textvariable=self.phase, style='Card.TLabel', wraplength=690).grid(
            row=1, column=0, sticky='w', pady=(5, 0))
        self.stop = ttk.Button(self.frame, text='取消任务', command=self.cancel)
        self.stop.grid(row=0, column=1, rowspan=2, padx=(10, 0))
        self.log_button = ttk.Button(self.frame, text='实时日志', command=studio.show_log)
        self.log_button.grid(row=0, column=2, rowspan=2, padx=(8, 0))
        self.doctor = ttk.Button(self.frame, text='工具链自检',
                                 command=lambda: studio.guard(lambda: studio.launch('formal_doctor')))
        self.doctor.grid(row=0, column=3, rowspan=2, padx=(8, 0))
        self.job = None
        active(self.stop, False)
        active(self.log_button, False)

    def show(self, job):
        related = job and job['kind'] in backend.FORMAL_JOBS + ('formal_replay',)
        self.job = job if related else None
        running = bool(related and job['state'] == 'RUNNING')
        active(self.doctor, sys.platform == 'linux' and not (job and job['state'] == 'RUNNING'))
        active(self.log_button, related)
        active(self.stop, running and not (job.get('cancel_requested') or job.get('stop_requested') or job.get('finalizing')))
        if not related:
            self.title.set('任务状态')
            self.phase.set('就绪 · SymbiYosys / Yosys-Slang')
            return
        elapsed = int((job.get('ended') or datetime.datetime.now().timestamp())-job['started'])
        outcome = job.get('verdict') or ({'DONE': 'PASS', 'ERROR': 'ERROR'}.get(job['state'], job['state']))
        self.title.set('{}   ·   {}   ·   {} 秒'.format(job_title(job), summary_status(outcome), elapsed))
        segments = [job.get('error') or job['stage']]
        activity = job.get('formal_activity') or {}
        if activity and running:
            segments.append('{} · 求解器第 {} 步'.format(activity['phase'], activity['step']))
        if job.get('formal_hits') and running:
            segments.append('已发现 {} 个目标'.format(len(job['formal_hits'])))
        if job.get('total') and running:
            segments.append('已完成 {} / {}'.format(job.get('completed', 0), job['total']))
        self.phase.set('   ·   '.join(segments))
        self.stop.configure(text='停止回放' if job['kind'] == 'formal_replay' else '取消任务')

    def cancel(self):
        if self.job and self.job['kind'] == 'formal_replay':
            self.studio.jobs.stop_regression()
        else:
            self.studio.jobs.cancel_current()


class PackagePage:
    """Run and inspect proof history for a selected immutable FSDB export."""
    def __init__(self, studio, parent):
        self.studio = studio
        self.key = tk.StringVar()
        self.timeout = tk.StringVar(value=studio.settings.get('open_formal', {}).get('package_timeout', '600'))
        self.info = tk.StringVar()
        self.record_note = tk.StringVar()
        self.exports = {}
        self.records = {}
        self.rows = {}
        self.folder = self.record = None
        bar = studio.bar(parent)
        ttk.Label(bar, text='验证包').pack(side='left', padx=(0, 6))
        self.packages = ttk.Combobox(bar, textvariable=self.key, width=29, state='readonly')
        self.packages.pack(side='left', padx=(0, 8))
        self.packages.bind('<<ComboboxSelected>>', lambda event: studio.guard(self.refresh_runs))
        studio.button(bar, '导入 ZIP', studio.import_formal)
        studio.button(bar, '转到 FSDB 导出', lambda: studio.show('formal'))
        self.run_button = studio.button(bar, '运行证明', self.run, True, side='right')
        controls = ttk.Frame(parent)
        controls.pack(fill='x', pady=(0, 9))
        ttk.Label(controls, text='每阶段超时（秒）').pack(side='left', padx=(0, 6))
        ttk.Entry(controls, textvariable=self.timeout, width=8).pack(side='left')
        ttk.Label(controls, text='0 不限时 · 留空用包内值', style='Muted.TLabel').pack(side='left', padx=8)
        studio.button(controls, '刷新历史', self.refresh_runs, side='right')
        ttk.Label(parent, textvariable=self.info, wraplength=830, style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        frame, self.runs = tree(parent, [('id', '运行编号', 225), ('state', '总结果', 92), ('bmc', 'BMC', 95),
                                         ('cover', '终点 Cover', 105), ('depth', '深度', 60), ('time', '用时', 72)], height=3)
        frame.pack(fill='x')
        self.runs.bind('<<TreeviewSelect>>', lambda event: studio.guard(self.select_run))
        ttk.Label(parent, textvariable=self.record_note, wraplength=850, style='Muted.TLabel').pack(anchor='w', pady=(8, 8))

        tabs = ttk.Notebook(parent)
        tabs.pack(fill='both', expand=True)
        properties_tab = ttk.Frame(tabs, padding=8); tabs.add(properties_tab, text='属性与终点')
        artifacts_tab = ttk.Frame(tabs, padding=8); tabs.add(artifacts_tab, text='运行文件')
        actions = ttk.Frame(properties_tab); actions.pack(fill='x', pady=(0, 6))
        self.wave_button = studio.button(actions, '打开所选波形', self.open_wave, True, side='right')
        studio.button(actions, '查看属性源码', self.open_source, side='right')
        self.result_filter = tk.StringVar(value='全部属性')
        filter_box = ttk.Combobox(actions, textvariable=self.result_filter, width=14, state='readonly',
                                  values=('全部属性', '未通过 Assert', 'Cover 终点', '有波形'))
        filter_box.pack(side='left')
        filter_box.bind('<<ComboboxSelected>>', lambda event: self.studio.guard(self.select_run))
        frame, self.properties = tree(properties_tab, [('name', '属性或终点', 247), ('category', '类型', 75),
            ('state', '状态', 95), ('pattern', 'Pattern / 区间', 110), ('step', '求解步', 75), ('artifact', '波形', 68)], height=5)
        frame.pack(fill='both', expand=True)
        self.properties.bind('<<TreeviewSelect>>', lambda event: self.set_buttons())
        self.properties.bind('<Double-1>', lambda event: studio.guard(self.open_wave))
        artifact_bar = studio.bar(artifacts_tab)
        self.artifact = tk.StringVar()
        self.files = ttk.Combobox(artifact_bar, textvariable=self.artifact, width=30, state='readonly')
        self.files.pack(side='left', padx=(0, 8))
        self.files.bind('<<ComboboxSelected>>', lambda event: studio.guard(self.preview))
        studio.button(artifact_bar, '另存文件', lambda: save_file(studio, self.selected_file()))
        self.code = CodeEditor(artifacts_tab, studio.mono, readonly=True, height=8)
        self.code.pack(fill='both', expand=True)
        self.refresh_exports()

    def refresh_exports(self, selected=None):
        self.exports = {row['id']: row for row in formal_model.state()['exports']}
        self.packages.configure(values=list(self.exports))
        choice = selected or self.key.get()
        self.key.set(choice if choice in self.exports else next(iter(self.exports), ''))
        self.refresh_runs()

    def refresh_runs(self, preferred=None):
        choice = self.key.get()
        package = self.exports.get(choice, {})
        counts = package.get('counts', {})
        self.info.set('{} · {} 个 pattern · {} 拍 · {} 个终点'.format(
            package.get('dut_name', 'DUT'), counts.get('patterns', '—'), counts.get('cycles', '—'),
            counts.get('terminal_covers', '—')) if package else '导入 Studio 验证包，或在「FSDB → Formal」生成一个验证包。')
        selection = preferred or (self.runs.selection() or [''])[0]
        self.records = {record['id']: (path, record) for path, record in formal_model.proof_history(choice)} if package else {}
        self.runs.delete(*self.runs.get_children())
        for identifier, (_, record) in self.records.items():
            status = record['status']
            seconds = record.get('finished_at', 0)-record.get('started_at', 0)
            self.runs.insert('', 'end', iid=identifier, tags=(status,), values=(
                identifier, summary_status(status), summary_status(record['bmc']['status']),
                summary_status(record['cover']['status']), record.get('depth', '—'), '{:.1f} s'.format(seconds) if seconds > 0 else '—'))
        selection = selection if selection in self.records else next(iter(self.records), None)
        if selection:
            self.runs.selection_set(selection)
        self.select_run()

    def select_run(self):
        key = (self.runs.selection() or [''])[0]
        self.folder, self.record = self.records.get(key, (None, None))
        self.properties.delete(*self.properties.get_children())
        self.rows.clear()
        known_waves = set()
        if self.record:
            record = self.record
            covered = len(record.get('terminal_covers', []))-len(record.get('unreached_covers', []))
            self.record_note.set(record.get('error') or '已到达终点 {} / {} · BMC 深度 {} · {}'.format(
                covered, len(record.get('terminal_covers', [])), record.get('depth', '—'),
                '有界检查' if record['status'] != 'RUNNING' else '查看上方实时进度'))
            known = set()
            for mode in ('bmc', 'cover'):
                for row in record[mode].get('properties', []):
                    name = row['name'].split('.')[-1].lstrip('\\')
                    known.add(name)
                    status = row['status']
                    if status == 'UNKNOWN' and record[mode]['status'] == 'TIMEOUT':
                        status = 'TIMEOUT'
                    if mode == 'cover' and status == 'FAIL':
                        status = 'NOT_FOUND_WITHIN_DEPTH'
                    if row.get('trace'):
                        known_waves.add(row['trace'])
                    self.add_property(name, row['kind'], status, row.get('depth'), row.get('trace'), row.get('location'))
            for name in record.get('terminal_covers', []):
                if name not in known:
                    cover = record['cover']
                    status = 'PASS' if name in cover.get('reached', []) else cover['status']
                    if status == 'PASS' and name not in cover.get('reached', []):
                        status = 'UNKNOWN'
                    if status == 'FAIL':
                        status = 'NOT_FOUND_WITHIN_DEPTH'
                    self.add_property(name, 'COVER', status)
            if not record['bmc'].get('properties'):
                traces = record['bmc'].get('vcds', [])
                for index, failure in enumerate(record['bmc'].get('failed_assertions', [])):
                    match = re.search(r'failed assertion\s+(\S+).*?step\s+(\d+)\s*$', failure)
                    self.add_property(match[1].split('.')[-1] if match else failure, 'ASSERT', 'FAIL',
                                      int(match[2]) if match else None, traces[index] if index < len(traces) else None)
                    if index < len(traces):
                        known_waves.add(traces[index])
            for mode, kind in [('bmc', 'ASSERT'), ('cover', 'COVER')]:
                for path in record[mode].get('vcds', []):
                    if path not in known_waves:
                        self.add_property(Path(path).name, kind, record[mode]['status'], trace=path)
        else:
            self.record_note.set('选择一个已完成的运行查看各条输出断言、终点和对应波形。')
        paths = self.artifact_files()
        self.files.configure(values=list(paths))
        self.artifact.set(next(iter(paths), ''))
        self.preview()
        self.set_buttons()

    def add_property(self, name, kind, status, step=None, trace=None, location=None):
        choice = self.result_filter.get()
        if (choice == '未通过 Assert' and (kind != 'ASSERT' or status == 'PASS') or
                choice == 'Cover 终点' and kind != 'COVER' or choice == '有波形' and not trace):
            return
        match = re.search(r'(?:^|_)P(\d+)_S(\d+)', name)
        item = self.properties.insert('', 'end', tags=(status,), values=(
            name, 'Assert' if kind == 'ASSERT' else 'Cover', summary_status(status),
            ' / '.join(match.groups()) if match else '—', step if step is not None else '—', 'VCD' if trace else '—'))
        self.rows[item] = dict(name=name, trace=trace, step=step, location=location)

    def selected_property(self):
        return self.rows.get((self.properties.selection() or [''])[0], {})

    def open_wave(self):
        row = self.selected_property()
        path = archived_file(self.folder, row.get('trace'))
        wave(self.studio, path, row.get('name'), row.get('step'))

    def open_source(self):
        if not self.key.get():
            raise ValueError('请选择验证包')
        path = formal_model.artifact(self.key.get(), 'sby_properties.sv')
        row = self.selected_property()
        match = re.search(r'sby_properties\.sv:(\d+)', row.get('location') or '')
        source_lines(self.studio, path, row.get('name', '求解属性源码'), int(match[1]) if match else None)

    def artifact_files(self):
        if not self.folder:
            return {}
        files = ('proof.json', 'bmc.log', 'cover.log', 'bmc_properties.jsonl', 'cover_properties.jsonl',
                 'source/effective_bmc.sby', 'source/effective_cover.sby',
                 'source/run_bmc.sby', 'source/run_cover.sby')
        return {name: path for name in files for path in [archived_file(self.folder, name)] if path}

    def selected_file(self):
        return self.artifact_files().get(self.artifact.get())

    def preview(self):
        path = self.selected_file()
        self.code.set(preview_text(path, tail=path.suffix == '.log') if path else '运行日志和生效的 SBY 配置将在此显示。',
                      filename=path.name if path else '运行归档')

    def set_buttons(self, busy=None):
        busy = self.studio.jobs.busy() if busy is None else busy
        active(self.run_button, bool(self.key.get()) and not busy and sys.platform == 'linux')
        active(self.wave_button, bool(self.selected_property().get('trace')) and not busy)

    def run(self):
        timeout = self.timeout.get().strip()
        if timeout and not re.fullmatch(r'[0-9]+', timeout):
            raise ValueError('超时须为非负整数秒，0 表示不限时')
        self.studio.launch('formal_prove', dict(export_id=self.key.get(), timeout=timeout))


def source_lines(studio, path, title, line, end_line=None):
    if line is None:
        studio.preview(title, preview_text(path, limit=120000))
        return
    from itertools import islice
    start = max(1, line-3)
    with path.open(encoding='utf-8', errors='replace') as stream:
        lines = list(islice(stream, start-1, max(line, end_line or line)+5))
    studio.preview(title, ''.join('{:>6}  {}'.format(start+i, value) for i, value in enumerate(lines)))
