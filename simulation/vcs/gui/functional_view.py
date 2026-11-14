"""Edit shared properties and inspect search and replay evidence in Tk."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import backend
import functional_properties as properties
from widgets import CodeEditor, tree


eda = backend.eda
GOAL_STATES = {'WITNESS': '已找到', 'NOT_FOUND_WITHIN_DEPTH': '深度内未找到',
               'TIMEOUT': '超时', 'UNKNOWN': '未知', 'REPLAY_REJECTED': '轨迹提取失败'}
SEARCH_STATES = {'COMPLETE': '已找到全部', 'PARTIAL': '已找到部分', 'TIMEOUT': '超时',
                 'NO_WITNESSES': '未找到可回放轨迹', 'UNKNOWN': '未知', 'ERROR': '工具错误'}


class FunctionalPage:
    def __init__(self, studio, page):
        self.studio = studio
        commands = studio.bar(page)
        studio.button(commands, '保存属性', self.save)
        studio.button(commands, '重载属性', self.reload)
        studio.button(commands, 'Formal 找用例', lambda: self.search('formal_cover'), primary=True)
        studio.button(commands, '有界检查 Assert', lambda: self.search('formal_bmc'))
        self.depth = tk.StringVar(value='48')
        self.timeout = tk.StringVar(value='600')
        for title, variable in [('搜索深度', self.depth), ('求解秒数（0 不限）', self.timeout)]:
            ttk.Label(commands, text=title).pack(side='left', padx=(9, 4))
            ttk.Entry(commands, textvariable=variable, width=7).pack(side='left')

        tabs = ttk.Notebook(page)
        tabs.pack(fill='both', expand=True)
        edit = ttk.Frame(tabs, padding=12)
        results = ttk.Frame(tabs, padding=12)
        tabs.add(edit, text='用户属性')
        tabs.add(results, text='覆盖结果')
        self.tabs, self.results_tab = tabs, results
        ttk.Label(edit, text='沿前采样。支持单拍表达式、固定拍数蕴含和 cover 的 ##N 序列。'
                           '可以使用 DUT 端口及 ar_fire、issue_fire、r_fire、past_valid。',
                  style='Muted.TLabel', wraplength=920).pack(anchor='w', pady=(0, 8))
        self.editor = CodeEditor(edit, studio.mono, height=15)
        self.editor.pack(fill='both', expand=True)
        self.saved = properties.USER_FILE.read_bytes()
        self.editor.set(self.saved.decode('utf-8-sig').replace('\r\n', '\n'))

        toolbar = studio.bar(results)
        self.campaign = tk.StringVar()
        self.history = ttk.Combobox(toolbar, textvariable=self.campaign, width=28, state='readonly')
        self.history.pack(side='left', padx=(0, 8))
        self.history.bind('<<ComboboxSelected>>', lambda event: studio.guard(self.load))
        studio.button(toolbar, '刷新', self.refresh)
        self.replay_button = studio.button(toolbar, 'VCS 回放', self.replay, primary=True)
        self.joint_button = studio.button(toolbar, '联合最小集', self.joint)
        studio.button(toolbar, '求解日志', self.log)
        self.cex_button = studio.button(toolbar, '反例波形', self.counterexample)
        self.info = tk.StringVar()
        ttk.Label(results, textvariable=self.info, wraplength=925, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        frame, self.points = tree(results, [('goal', '属性标签', 245), ('source', '行', 52),
                                           ('discovery', 'Formal 状态', 137), ('runs', 'VCS 命中用例数', 138)],
                                  height=9)
        frame.pack(fill='both', expand=True)
        self.points.tag_configure('hit', foreground='#16836c')
        self.points.tag_configure('miss', foreground='#8a6118')
        self.detail = tk.StringVar(value='回放使用回归计划的并行数、波形和检查设置。')
        ttk.Label(results, textvariable=self.detail, style='Muted.TLabel',
                  wraplength=925).pack(anchor='w', pady=(9, 0))
        self.record = None
        self.batch = None
        self.cex = None
        self.refresh()

    def contents(self):
        return self.editor.get().replace('\r\n', '\n').rstrip('\n') + '\n'

    def dirty(self):
        return self.saved.decode('utf-8-sig').replace('\r\n', '\n').rstrip('\n') + '\n' != self.contents()

    def save(self):
        if not self.dirty():
            return
        self.studio.jobs.require_editable_sources()
        raw = self.contents().encode('utf-8')
        properties.manifest(raw)
        if properties.USER_FILE.read_bytes() != self.saved:
            raise ValueError('功能属性文件已被其他程序修改。请先核对磁盘版本，避免覆盖。')
        temporary = properties.USER_FILE.with_name(properties.USER_FILE.name + '.' + eda.uuid.uuid4().hex + '.tmp')
        temporary.write_bytes(raw)
        os.replace(str(temporary), str(properties.USER_FILE))
        self.saved = raw
        self.studio.status.set('功能属性已保存，后续编译及 Formal 使用此版本')

    def reload(self):
        if self.dirty():
            if not messagebox.askyesno('重新读取属性', '用磁盘版本替换当前未保存的修改？', parent=self.studio.root):
                return
        self.saved = properties.USER_FILE.read_bytes()
        self.editor.set(self.saved.decode('utf-8-sig').replace('\r\n', '\n'))

    def search(self, kind):
        self.studio.idle()
        self.save()
        depth, timeout = int(self.depth.get()), int(self.timeout.get())
        if not 1 <= depth <= 1000000 or timeout < 0:
            raise ValueError('深度须为 1–1000000，求解秒数须非负')
        self.studio.launch(kind, dict(depth=depth, timeout=timeout))

    def refresh(self, selected=None):
        paths = sorted((eda.OUT/'functional_cover').glob('*/result.json'), reverse=True)
        ids = [path.parent.name for path in paths]
        self.history.configure(values=ids)
        selected = selected or self.campaign.get()
        self.campaign.set(selected if selected in ids else ids[0] if ids else '')
        self.load()

    def load(self):
        identifier = self.campaign.get()
        path = eda.OUT/'functional_cover'/backend.identifier(identifier) if identifier else None
        self.record = eda.read_json(path/'result.json') if path else None
        self.points.delete(*self.points.get_children())
        self.batch = None
        self.cex = None
        self.replay_button.configure(state='disabled')
        self.joint_button.configure(state='disabled')
        self.cex_button.configure(state='disabled')
        if self.record is None:
            self.info.set('保存属性后点击「Formal 找用例」。也可从「结果与波形」查看任意新回归的功能覆盖率。')
            self.detail.set('回放使用回归计划的并行数、波形和检查设置。')
            return
        record = self.record
        sha = record['functional']['sha256'][:12]
        if record['action'] == 'bmc':
            self.info.set('有界 Assert 检查 · {} · 深度 {} · 属性 {}'.format(
                record['status'], record['max_body_cycles'], sha))
            failed = [row for row in record.get('checks', []) if row['status'] == 'FAIL']
            self.detail.set('失败属性 ' + '、'.join(row['name'].split('.')[-1] for row in failed) if failed else
                            '有界检查结果对应已执行的范围。模型和日志保存在该任务目录。')
            for row in failed:
                trace = (path / (row.get('trace') or '')).resolve()
                if path.resolve() in trace.parents and trace.suffix == '.vcd' and trace.is_file():
                    self.cex = trace
                    self.cex_button.configure(state='normal')
                    break
            return
        candidate_count = sum('frames' in item for item in record.get('candidates', []))
        self.replay_button.configure(state='normal' if candidate_count else 'disabled')
        rows = [row for row in backend.history() if row['formal_campaign'] == identifier]
        hits = {}
        if rows:
            self.batch = next((row['id'] for row in rows if row['coverage']), rows[0]['id'])
            result = eda.read_json(backend.batch_path(self.batch)/'summary.json')
            hits = result['functional']['hits']
            self.joint_button.configure(state='normal' if result['coverage']['status'] == 'PASS' else 'disabled')
        self.info.set('Formal {} · {} 条可回放轨迹 · 深度 {} · 属性 {}'.format(
            SEARCH_STATES.get(record['status'], record['status']), candidate_count, record['max_body_cycles'], sha))
        for p in record['functional']['properties']:
            if p['kind'] != 'cover':
                continue
            name, line = p['name'], p['line']
            discovery = GOAL_STATES.get(record['goals'][name], record['goals'][name])
            self.points.insert('', 'end', values=(name, line, discovery, hits.get(name, '—')),
                               tags=('hit' if hits.get(name) else 'miss',))
        self.detail.set('回放批次 {} · 功能覆盖 {}/{} · {} / {} 条通过'.format(
            self.batch, sum(bool(value) for value in hits.values()), len(hits), result['passed'], result['total']) if self.batch else
            '回放使用回归计划的并行数、波形和检查设置。')

    def present(self, path):
        self.refresh(Path(path).parent.name)
        self.tabs.select(self.results_tab)
        self.studio.show('functional')

    def replay(self):
        self.studio.idle()
        self.save()
        if not self.record or properties.manifest(properties.USER_FILE.read_bytes()) != self.record['functional']:
            raise ValueError('属性已修改。请为当前属性重新找用例。')
        policy = self.studio.plan_value()
        if policy['jobs'] < 1 or policy['timeout'] < 0:
            raise ValueError('请在回归计划中填写有效的并行数和仿真超时')
        self.studio.launch('formal_replay', dict(campaign=self.record['id'], policy=policy))

    def joint(self):
        self.studio.choose_coverage_batches([self.batch], joint=True)

    def log(self):
        if not self.record:
            raise ValueError('请选择已有 Formal 任务')
        path = eda.OUT/'functional_cover'/backend.identifier(self.record['id'])/'sby.log'
        with path.open(encoding='utf-8', errors='replace') as stream:
            text = stream.read(120000)
        self.studio.preview('开源 Formal 求解日志', text)

    def counterexample(self):
        if self.cex:
            self.studio.launch('wave_index', dict(path=str(self.cex)))
