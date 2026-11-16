"""Native Tk controls and history for local commercial Formal adapters."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk

import backend
import formal_model
from formal_view import active, archived_file, preview_text, summary_status, wave
from widgets import tree


eda = backend.eda
MODES = {'DUT Cover': 'cover', 'DUT Assert / BMC': 'bmc', 'FSDB 验证包': 'prove'}
VERDICTS = {
    'PREPARED': '任务已准备', 'REACHED': '已命中', 'UNREACHED_IN_BOUND': '深度内未命中',
    'UNREACHABLE': '已证明不可达', 'PROVEN': '已证明', 'BOUNDED_PASS': '有界通过',
    'COUNTEREXAMPLE': '发现反例', 'VACUOUS': '空触发',
}


class CommercialPage:
    def __init__(self, functional, parent):
        self.functional = functional
        self.studio = studio = functional.studio
        self.mode = tk.StringVar(value=next(iter(MODES)))
        self.adapter = tk.StringVar(value=eda.platform_config.load().get('formal_adapter', ''))
        self.package = tk.StringVar()
        self.targets = tk.StringVar()
        self.defines = tk.StringVar()
        self.depth = tk.StringVar()
        self.timeout = tk.StringVar(value='600')
        self.history = tk.StringVar()
        self.info = tk.StringVar()
        self.record, self.folder = {}, None

        form = ttk.Frame(parent); form.pack(fill='x', pady=(0, 8))
        form.columnconfigure(1, weight=1)
        for i, (label, variable) in enumerate((
                ('任务', self.mode), ('本机适配器', self.adapter), ('已解压验证包', self.package),
                ('目标（留空选全部）', self.targets), ('附加编译宏（可选）', self.defines))):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', padx=(0, 12), pady=4)
            if i == 0:
                widget = ttk.Combobox(form, textvariable=variable, values=list(MODES), state='readonly', width=24)
                widget.bind('<<ComboboxSelected>>', lambda event: self.mode_changed())
            else:
                widget = ttk.Entry(form, textvariable=variable)
            widget.grid(row=i, column=1, sticky='w' if i == 0 else 'ew', pady=4)
            if i == 2:
                self.package_field = widget
            if i == 3:
                self.targets_field = widget
        ttk.Button(form, text='选择…', command=self.choose_adapter).grid(row=1, column=2, padx=8)
        self.choose_package_button = ttk.Button(form, text='选择…', command=self.choose_package)
        self.choose_package_button.grid(row=2, column=2, padx=8)
        self.use_package_button = ttk.Button(form, text='使用当前包', command=lambda: studio.guard(self.use_package))
        self.use_package_button.grid(row=2, column=3, padx=(0, 8))
        bar = studio.bar(parent)
        for label, variable in (('深度（留空用默认）', self.depth), ('求解超时（秒，0 不限）', self.timeout)):
            ttk.Label(bar, text=label).pack(side='left', padx=(0, 5))
            ttk.Entry(bar, textvariable=variable, width=8).pack(side='left', padx=(0, 12))
        self.run_button = studio.button(bar, '运行商业 Formal', lambda: self.launch(False), True)
        self.prepare_button = studio.button(bar, '只准备任务', lambda: self.launch(True))

        bar = studio.bar(parent)
        self.runs = ttk.Combobox(bar, textvariable=self.history, width=29, state='readonly')
        self.runs.pack(side='left', padx=(0, 8))
        self.runs.bind('<<ComboboxSelected>>', lambda event: studio.guard(self.load))
        studio.button(bar, '刷新历史', self.refresh)
        self.request_button = studio.button(bar, '查看请求', lambda: self.preview('request'))
        self.log_button = studio.button(bar, '查看适配器日志', lambda: self.preview('log'))
        self.wave_button = studio.button(bar, '查看选中 VCD', self.open_wave)
        ttk.Label(parent, textvariable=self.info, style='Muted.TLabel', wraplength=920).pack(fill='x', pady=(0, 8))
        frame, self.rows = tree(parent, [('name', '属性 / 实例', 345), ('kind', '类型', 65),
                                         ('status', '结论', 160), ('depth', '深度', 70),
                                         ('trace', '波形', 70)], height=8)
        frame.pack(fill='both', expand=True)
        self.rows.bind('<<TreeviewSelect>>', lambda event: self.poll())
        self.rows.bind('<Double-1>', lambda event: studio.guard(self.open_wave))
        self.mode_changed()
        self.refresh()

    def mode_changed(self):
        package = MODES[self.mode.get()] == 'prove'
        for widget in (self.package_field, self.choose_package_button, self.use_package_button):
            active(widget, package)
        active(self.targets_field, not package)

    def choose_adapter(self):
        value = filedialog.askopenfilename(title='选择本机可执行适配器', parent=self.studio.root)
        if value:
            self.adapter.set(value)

    def choose_package(self):
        value = filedialog.askdirectory(title='选择已解压的 FSDB 验证包', parent=self.studio.root)
        if value:
            self.package.set(value)

    def use_package(self):
        key = self.functional.package.key.get()
        if not key:
            raise ValueError('请先在「验证包证明」中选择一个包')
        self.package.set(str(formal_model.artifact(key, 'summary.json').parent))

    def launch(self, prepare):
        self.studio.idle()
        mode = MODES[self.mode.get()]
        depth = int(self.depth.get()) if self.depth.get().strip() else None
        timeout = int(self.timeout.get())
        if (depth is not None and not 1 <= depth <= 1000000) or timeout < 0:
            raise ValueError('深度须为 1–1000000，求解超时须为非负整数')
        adapter = self.adapter.get().strip()
        package = self.package.get().strip() if mode == 'prove' else ''
        if mode == 'prove' and not package:
            raise ValueError('请选择已解压的 FSDB 验证包')
        if not prepare and not adapter and not eda.platform_config.load().get('formal_adapter'):
            raise ValueError('请选择一个商业 Formal 可执行适配器')
        if mode != 'prove':
            self.functional.save(native=True)
        self.studio.launch('formal_commercial', dict(mode=mode, depth=depth, timeout=timeout,
                            adapter=adapter, package=package, defines=self.defines.get().strip(),
                            targets=self.targets.get().strip() if mode != 'prove' else '', prepare=prepare))

    def refresh(self, selected=None):
        root = eda.OUT / 'commercial_formal'
        paths = sorted(root.glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        ids = [path.parent.name for path in paths]
        self.runs.configure(values=ids)
        desired = selected or self.history.get()
        self.history.set(desired if desired in ids else next(iter(ids), ''))
        self.load()

    def load(self):
        key = self.history.get()
        self.folder = eda.OUT / 'commercial_formal' / backend.identifier(key) if key else None
        self.record = eda.read_json(self.folder / 'result.json') if self.folder else {}
        self.rows.delete(*self.rows.get_children())
        for i, row in enumerate(self.record.get('properties', [])):
            self.rows.insert('', 'end', iid=str(i), values=(
                row['name'], row['kind'], VERDICTS.get(row['status'], summary_status(row['status'])),
                row['depth'] if row['depth'] is not None else '—', '有' if row['trace'] else '—'))
        if self.record:
            state = self.record['status']
            engine = self.record.get('engine', {})
            title = ' · '.join(filter(None, [VERDICTS.get(state, summary_status(state)),
                                            engine.get('name'), engine.get('version')]))
            detail = self.record.get('error') or self.record.get('message')
            missing = self.record.get('missing', [])
            self.info.set('\n'.join(filter(None, [title, str(self.folder), detail,
                                                 '缺少结果：' + '、'.join(missing[:10]) if missing else ''])))
        else:
            self.info.set('选择本机适配器运行，或先准备原生 RTL / SVA 与请求文件。')
        self.poll()

    def preview(self, key):
        path = archived_file(self.folder, self.record.get(key))
        if path:
            self.studio.preview(path.name, preview_text(path, tail=key == 'log'))

    def selected_trace(self):
        selected = self.rows.selection()
        if not selected:
            return None, ''
        row = self.record['properties'][int(selected[0])]
        return archived_file(self.folder, row['trace']), row['name']

    def open_wave(self):
        path, name = self.selected_trace()
        if not self.studio.jobs.busy():
            wave(self.studio, path, name)

    def poll(self):
        available = sys.platform == 'linux' and not self.studio.jobs.busy()
        active(self.run_button, available)
        active(self.prepare_button, available)
        active(self.request_button, bool(archived_file(self.folder, self.record.get('request'))))
        active(self.log_button, bool(archived_file(self.folder, self.record.get('log'))))
        active(self.wave_button, not self.studio.jobs.busy() and bool(self.selected_trace()[0]))
