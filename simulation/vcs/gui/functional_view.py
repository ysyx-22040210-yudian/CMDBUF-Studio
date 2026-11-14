"""Native property editor, Cover search and witness replay."""
import csv
import os
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import backend
import dut_config
from formal_view import (Activity, PackagePage, TASKS, active, archived_file, preview_text,
                         save_file, source_lines, summary_status, wave)
import functional_properties as props
from widgets import CodeEditor, tree


eda = backend.eda
KINDS = {'assert': 'Assert', 'assume': 'Assume', 'cover': 'Cover'}
SKELETONS = {'Assert': ('A_PENDING_LIMIT', "pending_cnt <= 4'd9"),
             'Assume': ('E_AR_ID', "arvalid |-> arid != 8'hff"),
             'Cover': ('C_RETRY', '((|req) && !issue_fire) ##1 issue_fire')}


class FunctionalPage:
    def __init__(self, studio, parent):
        self.studio = studio
        self.activity = Activity(studio, parent)
        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill='both', expand=True)
        edit = self.tab('属性与任务')
        history = self.tab('搜索与回放结果')
        package = self.tab('验证包证明')
        self.result_tab = history
        self.saved = props.USER_FILE.read_bytes()
        self.build_editor(edit)
        self.build_history(history)
        self.package = PackagePage(studio, package)
        self.package_tab = package
        self.refresh()

    def tab(self, name):
        frame = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(frame, text=name)
        return frame

    def build_editor(self, parent):
        bar = self.studio.bar(parent)
        self.search_button = self.studio.button(bar, '搜索全部 Cover', lambda: self.search('formal_cover'), True)
        self.search_selected = self.studio.button(bar, '只搜选中 Cover', self.search_selection)
        self.bmc_button = self.studio.button(bar, '检查 Assert', lambda: self.search('formal_bmc'))
        self.studio.button(bar, '保存属性', self.save, side='right')
        self.studio.button(bar, '重载', self.reload, side='right')
        controls = ttk.Frame(parent)
        controls.pack(fill='x', pady=(0, 10))
        settings = self.studio.settings.get('open_formal', {})
        self.depth = tk.StringVar(value=settings.get('depth', '48'))
        self.timeout = tk.StringVar(value=settings.get('timeout', '600'))
        for title, variable in [('深度（拍）', self.depth), ('超时（秒，0 不限）', self.timeout)]:
            ttk.Label(controls, text=title).pack(side='left', padx=(0, 5))
            entry = ttk.Entry(controls, textvariable=variable, width=7)
            entry.pack(side='left', padx=(0, 14))
            if variable is self.depth:
                self.depth_entry = entry
        ttk.Label(controls, text='Cover 深度含 8 拍收尾 · Assert 使用 BMC', style='Muted.TLabel').pack(side='left')
        pane = ttk.Panedwindow(parent, orient='horizontal')
        pane.pack(fill='both', expand=True)
        index = ttk.Frame(pane, padding=(0, 0, 8, 0)); pane.add(index, weight=1)
        editor = ttk.Frame(pane); pane.add(editor, weight=4)
        self.index_heading = tk.StringVar(value='属性导航')
        ttk.Label(index, textvariable=self.index_heading, style='Heading.TLabel').pack(anchor='w', pady=(0, 7))
        self.filter = tk.StringVar(value='全部')
        combo = ttk.Combobox(index, textvariable=self.filter, state='readonly', width=14,
                             values=('全部', 'Cover', 'Assert', 'Assume'))
        combo.pack(fill='x', pady=(0, 6))
        combo.bind('<<ComboboxSelected>>', lambda event: self.update_index())
        frame, self.index = tree(index, [('property', '标签', 170), ('kind', '类型', 70),
                                         ('line', '行', 54)], height=8, selectmode='extended')
        frame.pack(fill='both', expand=True)
        self.index.bind('<<TreeviewSelect>>', lambda event: self.set_buttons())
        self.index.bind('<Double-1>', self.jump_to_property)
        bar = ttk.Frame(editor); bar.pack(fill='x', pady=(0, 6))
        self.template = tk.StringVar(value='Cover')
        ttk.Combobox(bar, textvariable=self.template, values=list(SKELETONS), state='readonly',
                     width=10).pack(side='left', padx=(0, 8))
        self.studio.button(bar, '插入模板', self.insert_template)
        self.studio.button(bar, '内置约束', self.show_contract, side='right')
        self.editor = CodeEditor(editor, self.studio.mono, height=10, on_change=self.update_index)
        self.editor.pack(fill='both', expand=True)
        self.parse_status = tk.StringVar()
        ttk.Label(parent, textvariable=self.parse_status, style='Muted.TLabel', wraplength=875).pack(
            anchor='w', fill='x', pady=(7, 0))
        self.editor.set(self.saved.decode('utf-8-sig').replace('\r\n', '\n'))
        self.update_index()

    def contents(self):
        return self.editor.get().rstrip('\n')+'\n'

    def dirty(self):
        return self.saved.decode('utf-8-sig').replace('\r\n', '\n').rstrip('\n')+'\n' != self.contents()

    def update_index(self):
        selected = self.index.selection()
        self.index.delete(*self.index.get_children())
        try:
            self.authored = {p['name']: p for p in props.user_properties(self.contents())}
        except ValueError as exc:
            self.authored = {}
            self.parse_status.set(str(exc))
        else:
            self.parse_status.set('未保存的修改' if self.dirty() else
                '双击标签定位源码。属性在 clk 上升沿采样，复位期间禁用。')
        self.index_heading.set('属性导航  ·  {}'.format(len(self.authored)))
        for name, item in self.authored.items():
            kind = KINDS[item['kind']]
            if self.filter.get() in ('全部', kind):
                self.index.insert('', 'end', iid=name, values=(name, kind, item['line']))
        self.index.selection_set([name for name in selected if self.index.exists(name)])
        self.set_buttons()

    def jump_to_property(self, event):
        name = self.index.identify_row(event.y)
        if name in self.authored:
            index = '{}.0'.format(self.authored[name]['line'])
            self.editor.text.mark_set('insert', index)
            self.editor.text.see(index)
            self.editor.text.focus_set()

    def insert_template(self):
        kind = self.template.get()
        base, expression = SKELETONS[kind]
        name = base
        number = 2
        while name in self.authored:
            name = '{}_{}'.format(base, number)
            number += 1
        self.editor.text.insert('end', '\n{}: {} property (@(posedge clk) disable iff (!rst_n)\n    {});\n'.format(
            name, kind.lower(), expression))
        self.editor.text.see('end')
        self.editor.text.focus_set()

    def show_contract(self):
        checker = eda.ROOT/eda.PROJECT['checker']
        builtin = props.builtin_properties(checker.read_text(encoding='utf-8-sig'))
        text = '{}\n\n'.format(dut_config.load()['name']) + '\n\n'.join(
            '{}  {}\n{}'.format(p['kind'].upper(), p['name'], p['predicate']) for p in builtin)
        self.studio.preview('当前 DUT 的 Formal 端口约束', text)

    def save(self):
        if not self.dirty():
            return
        self.studio.jobs.require_editable_sources()
        raw = self.contents().encode('utf-8')
        props.user_properties(raw.decode('utf-8'))
        if props.USER_FILE.read_bytes() != self.saved:
            raise ValueError('属性文件已在外部修改，请重载后再保存。')
        temporary = props.USER_FILE.with_name(props.USER_FILE.name+'.'+eda.uuid.uuid4().hex+'.tmp')
        temporary.write_bytes(raw)
        os.replace(str(temporary), str(props.USER_FILE))
        self.saved = raw
        self.update_index()
        self.studio.status.set('功能属性已保存')

    def reload(self):
        if self.dirty() and not messagebox.askyesno('重载属性', '用磁盘版本替换未保存的修改？', parent=self.studio.root):
            return
        self.saved = props.USER_FILE.read_bytes()
        self.editor.set(self.saved.decode('utf-8-sig').replace('\r\n', '\n'))
        self.update_index()

    def search_selection(self):
        selected = self.index.selection()
        self.update_index()
        targets = tuple(name for name in selected if self.authored.get(name, {}).get('kind') == 'cover')
        if targets:
            self.search('formal_cover', targets)

    def search(self, kind, targets=()):
        self.studio.idle()
        self.save()
        depth, timeout = int(self.depth.get()), int(self.timeout.get())
        if not 1 <= depth <= 1000000 or timeout < 0:
            raise ValueError('深度须为 1–1000000，超时须为非负整数')
        self.studio.launch(kind, dict(depth=depth, timeout=timeout, targets=targets))
        self.tabs.select(self.result_tab)

    def build_history(self, parent):
        bar = self.studio.bar(parent)
        self.campaign = tk.StringVar()
        self.history = ttk.Combobox(bar, textvariable=self.campaign, width=28, state='readonly')
        self.history.pack(side='left', padx=(0, 8))
        self.history.bind('<<ComboboxSelected>>', lambda event: self.studio.guard(self.load))
        self.studio.button(bar, '刷新历史', self.refresh)
        self.rerun_button = self.studio.button(bar, '复用运行设置', self.reuse)
        self.info = tk.StringVar()
        ttk.Label(parent, textvariable=self.info, wraplength=900, style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        self.results = ttk.Notebook(parent)
        self.results.pack(fill='both', expand=True)
        goals = ttk.Frame(self.results, padding=8); self.results.add(goals, text='属性结果')
        traces = ttk.Frame(self.results, padding=8); self.results.add(traces, text='轨迹与回放')
        files = ttk.Frame(self.results, padding=8); self.results.add(files, text='日志与模型')
        self.goals_tab, self.traces_tab = goals, traces

        bar = ttk.Frame(goals); bar.pack(fill='x', pady=(0, 6))
        self.goal_source = self.studio.button(bar, '查看属性', self.open_goal_source)
        self.goal_wave = self.studio.button(bar, '查看求解波形', self.open_goal_wave, True)
        self.goal_trace = self.studio.button(bar, '转到回放轨迹', self.open_goal_trace)
        self.missing_button = self.studio.button(bar, '设置重搜未命中', self.retry_missing, side='right')
        frame, self.points = tree(goals, [('name', '属性', 230), ('kind', '类型', 65), ('state', '结论', 151),
                                         ('step', '求解步', 65), ('hits', 'VCS 命中', 82), ('trace', '轨迹', 80)], height=7)
        frame.pack(fill='both', expand=True)
        self.points.bind('<<TreeviewSelect>>', lambda event: self.set_buttons())
        self.points.bind('<Double-1>', lambda event: self.studio.guard(self.open_goal_wave))

        bar = ttk.Frame(traces); bar.pack(fill='x', pady=(0, 6))
        self.trace_wave = self.studio.button(bar, '打开求解轨迹', self.open_trace_wave)
        self.trace_pins = self.studio.button(bar, '逐拍输入', self.show_inputs)
        self.replay_button = self.studio.button(bar, '回放选中', self.replay, True, side='right')
        self.replay_all = self.studio.button(bar, '回放全部', lambda: self.replay(all_traces=True), side='right')
        frame, self.traces = tree(traces, [('trace', '轨迹', 80), ('depth', '拍数', 60), ('goals', '求解命中', 265),
                                           ('sim', 'VCS 结果', 98), ('batch', '回放批次', 220)], height=3,
                                  selectmode='extended')
        self.traces.bind('<<TreeviewSelect>>', lambda event: self.set_buttons())
        self.traces.bind('<Double-1>', lambda event: self.studio.guard(self.open_trace_wave))
        controls = ttk.Frame(traces); controls.pack(fill='x', pady=(8, 4))
        self.jobs = tk.StringVar(value='3')
        self.sim_timeout = tk.StringVar(value='120')
        self.waves = tk.BooleanVar(value=True)
        for title, variable in [('并行数', self.jobs), ('仿真超时（秒）', self.sim_timeout)]:
            ttk.Label(controls, text=title).pack(side='left', padx=(0, 5))
            ttk.Entry(controls, textvariable=variable, width=6).pack(side='left', padx=(0, 10))
        ttk.Checkbutton(controls, text='生成波形', variable=self.waves).pack(side='left')
        self.studio.button(controls, '读取回归计划设置', self.use_plan, side='right')
        self.detail = tk.StringVar(value='检查模式和 fatal 使用当前回归计划。')
        ttk.Label(traces, textvariable=self.detail, style='Muted.TLabel', wraplength=850).pack(anchor='w', pady=(0, 4))
        bar = ttk.Frame(traces); bar.pack(fill='x')
        self.run_button = self.studio.button(bar, '查看 VCS 结果', self.open_run)
        self.vcs_wave = self.studio.button(bar, 'VCS 波形', self.open_vcs_wave)
        self.joint_button = self.studio.button(bar, '联合最小集', self.joint, side='right')
        frame.pack(fill='both', expand=True, pady=(8, 0))

        bar = self.studio.bar(files)
        self.file = tk.StringVar()
        self.file_menu = ttk.Combobox(bar, textvariable=self.file, width=26, state='readonly')
        self.file_menu.pack(side='left', padx=(0, 8))
        self.file_menu.bind('<<ComboboxSelected>>', lambda event: self.studio.guard(self.preview))
        self.studio.button(bar, '另存文件', lambda: save_file(self.studio, self.files().get(self.file.get())))
        self.preview_code = CodeEditor(files, self.studio.mono, readonly=True, height=8)
        self.preview_code.pack(fill='both', expand=True)
        self.folder = self.record = None
        self.displayed_campaign = None
        self.goal_rows = {}
        self.trace_rows = {}
        self.replays = {}
        self.batches = []

    def refresh(self, selected=None):
        paths = sorted((eda.OUT/'functional_cover').glob('*/result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        ids = [p.parent.name for p in paths]
        self.history.configure(values=ids)
        choice = selected or self.campaign.get()
        self.campaign.set(choice if choice in ids else ids[0] if ids else '')
        self.load()

    def load(self):
        identifier = self.campaign.get()
        self.folder = eda.OUT/'functional_cover'/backend.identifier(identifier) if identifier else None
        self.record = eda.read_json(self.folder/'result.json') if self.folder else None
        goal_selection = self.goal().get('name') if self.displayed_campaign == identifier else None
        trace_selection = self.traces.selection() if self.displayed_campaign == identifier else ()
        self.displayed_campaign = identifier
        self.points.delete(*self.points.get_children()); self.goal_rows.clear()
        self.traces.delete(*self.traces.get_children()); self.trace_rows.clear()
        self.replays = {}
        self.batches = []
        if self.record:
            record = self.record
            self.load_replays(identifier)
            self.show_goals(record)
            self.show_traces(record)
            duration = record.get('finished_at', 0)-record.get('started_at', 0)
            time_text = ' · {:.1f} 秒'.format(duration) if duration > 0 else ''
            self.info.set('{}   ·   {}   ·   深度 {}   ·   {}   ·   属性 {}{}'.format(
                TASKS[record['action']], summary_status(record['status']), record['max_body_cycles'],
                record.get('engine', 'smtbmc yices'), record['functional']['sha256'][:12], time_text))
            if record.get('error'):
                self.info.set(self.info.get()+'\n'+record['error'])
        else:
            self.info.set('尚无运行记录。可从「属性与任务」开始 Cover 搜索或检查 Assert。')
        self.detail.set('已关联 {} 个通过覆盖合并的回放批次。检查模式和 fatal 使用当前回归计划。'.format(len(self.batches)))
        paths = self.files()
        self.file_menu.configure(values=list(paths))
        self.file.set('sby.log' if 'sby.log' in paths else next(iter(paths), ''))
        self.preview()
        self.points.selection_set([key for key, row in self.goal_rows.items() if row['name'] == goal_selection])
        self.traces.selection_set([key for key in trace_selection if self.traces.exists(key)])
        self.set_buttons()

    def load_replays(self, identifier):
        for row in backend.history():
            if row['formal_campaign'] != identifier:
                continue
            batch = row['id']
            if row['coverage']:
                self.batches.append(batch)
            summary = eda.read_json(backend.batch_path(batch)/'summary.json')
            for run in summary['results']:
                trace = run.get('formal_origin', {}).get('trace')
                if trace:
                    self.replays.setdefault(trace, []).append((batch, run))

    def show_goals(self, record):
        authored = {p['name']: p for p in record['functional']['properties']}
        if record['action'] == 'cover':
            traces = {label: item for item in record.get('candidates', []) if not item.get('duplicate_of')
                      for label in item['goals']}
            hits = {}
            for runs in self.replays.values():
                for _, run in runs:
                    if run['status'] == 'PASS':
                        for label in run.get('functional', {}).get('hits', []):
                            hits[label] = hits.get(label, 0) + 1
            for name, prop in authored.items():
                if prop['kind'] != 'cover':
                    continue
                item = traces.get(name, {})
                status = record.get('goals', {}).get(name, record['status'])
                step = item.get('hit_steps', item.get('goals', {})).get(name)
                self.add_goal(name, 'Cover', status, prop, item.get('path'), step,
                              hits.get(name, 0) if self.replays else '—', trace_id=item.get('trace'))
        else:
            checks = {row['name'].split('.')[-1].lstrip('\\'): row for row in record.get('checks', [])}
            assertions = dict.fromkeys(p['name'] for p in record.get('builtin', []) if p['kind'] == 'assert')
            assertions.update({name: prop for name, prop in authored.items() if prop['kind'] == 'assert'})
            for name, prop in assertions.items():
                row = checks.get('FC_USER_'+name if prop else name, {})
                status = row.get('status', 'UNKNOWN' if record['status'] == 'FAIL' else record['status'])
                self.add_goal(name, 'Assert', status, prop, row.get('trace'), row.get('depth'), '—', row.get('location'))

    def add_goal(self, name, kind, status, prop, trace, step, hits, location=None, trace_id=None):
        row = self.points.insert('', 'end', tags=(status,), values=(
            name, kind, summary_status(status), step if step is not None else '—', hits, Path(trace).stem if trace else '—'))
        self.goal_rows[row] = dict(name=name, source=prop, trace=trace, step=step, location=location, trace_id=trace_id)

    def show_traces(self, record):
        for entry in record.get('candidates', []):
            name = entry['trace']
            self.trace_rows[name] = entry
            batch, run = next(iter(self.replays.get(name, [])), ('—', {}))
            status = run.get('status', 'NOT_RUN')
            text = ('复用 '+entry['duplicate_of'] if entry.get('duplicate_of') else
                    '提取失败' if entry.get('failures') else summary_status(status))
            self.traces.insert('', 'end', iid=name, tags=(status,), values=(
                name, entry.get('body_cycles', '—'), '、'.join(entry['goals']), text, batch))

    def files(self):
        if not self.folder:
            return {}
        items = ('sby.log', 'result.json', 'properties.jsonl', 'user_properties.sv', 'formal_top.sv', 'run.sby', 'port_contract.sv')
        return {name: path for name in items for path in [archived_file(self.folder, name)] if path}

    def preview(self):
        path = self.files().get(self.file.get())
        self.preview_code.set(preview_text(path, tail=path.suffix == '.log') if path else '求解日志和归档模型将在此显示。')

    def present(self, path):
        self.refresh(Path(path).parent.name)
        self.results.select(self.goals_tab)
        if self.studio.page == 'functional':
            self.tabs.select(self.result_tab)

    def reuse(self):
        if self.record:
            self.depth.set(str(self.record['max_body_cycles']))
            self.timeout.set(str(self.record['timeout']))
            self.tabs.select(self.tabs.tabs()[0])

    def settings(self):
        return dict(depth=self.depth.get(), timeout=self.timeout.get(), package_timeout=self.package.timeout.get())

    def retry_targets(self):
        if not self.record:
            return ()
        return tuple(name for name, state in self.record.get('goals', {}).items()
                     if state not in ('WITNESS', 'NOT_SELECTED'))

    def retry_missing(self):
        targets = self.retry_targets()
        if targets:
            self.depth.set(str(self.record['max_body_cycles']))
            self.timeout.set(str(self.record['timeout']))
            self.filter.set('Cover')
            self.update_index()
            self.index.selection_set([name for name in targets if self.index.exists(name)])
            self.tabs.select(self.tabs.tabs()[0])
            self.depth_entry.focus_set()

    def goal(self):
        return self.goal_rows.get((self.points.selection() or [''])[0], {})

    def trace(self):
        return self.trace_rows.get((self.traces.selection() or [''])[0], {})

    def open_goal_source(self):
        row = self.goal()
        prop = row.get('source')
        if prop:
            path = archived_file(self.folder, 'user_properties.sv')
            line = prop['line']
        else:
            path = archived_file(self.folder, 'formal_top.sv')
            match = re.search(r'formal_top\.sv:(\d+)', row.get('location') or '')
            line = int(match[1]) if match else None
        if path:
            source_lines(self.studio, path, row.get('name', path.name), line)

    def open_goal_wave(self):
        row = self.goal()
        wave(self.studio, archived_file(self.folder, row.get('trace')), row.get('name'), row.get('step'))

    def open_goal_trace(self):
        trace = self.goal().get('trace_id')
        if trace in self.trace_rows:
            self.results.select(self.traces_tab)
            self.traces.selection_set(trace)
            self.traces.see(trace)

    def open_trace_wave(self):
        row = self.trace()
        wave(self.studio, archived_file(self.folder, row.get('path')), row.get('trace'),
             max(row['goals'].values()) if row.get('goals') else None)

    def show_inputs(self):
        entry = self.trace()
        path = archived_file(self.folder, entry.get('frames'))
        if path is None:
            return
        if eda.digest(path) != entry['frames_sha256']:
            raise ValueError('逐拍输入与归档校验不匹配')
        frames = eda.read_json(path)['frames']
        dialog = tk.Toplevel(self.studio.root)
        dialog.title(entry['trace']+' · 逐拍输入')
        dialog.geometry('1100x630')
        box = ttk.Frame(dialog, padding=16); box.pack(fill='both', expand=True)
        columns = [('cycle','回放拍',65)]+[(name, name, max(69, width//4*9)) for name, width in props.INPUTS]
        frame, table = tree(box, columns, height=12); frame.pack(fill='both', expand=True)
        first = tk.IntVar(value=0); caption = tk.StringVar()
        foot = ttk.Frame(box); foot.pack(fill='x', pady=(8, 0))
        ttk.Label(foot, textvariable=caption).pack(side='left')

        def unpack(value):
            number = int(value, 16)
            fields = []
            for _, width in reversed(props.INPUTS):
                fields.append('{:0{}x}'.format(number & ((1 << width)-1), (width+3)//4))
                number >>= width
            return tuple(reversed(fields))

        def page(change=0):
            offset = max(0, min((len(frames)-1)//200*200, first.get()+change))
            first.set(offset)
            table.delete(*table.get_children())
            for index, value in enumerate(frames[offset:offset+200], offset+1):
                table.insert('', 'end', values=(index,)+unpack(value))
            caption.set('第 {}–{} / {} 拍 · 输入值为十六进制'.format(offset+1, min(len(frames), offset+200), len(frames)))

        def export():
            target = filedialog.asksaveasfilename(parent=dialog, initialfile=entry['trace']+'.csv', defaultextension='.csv')
            if target:
                with Path(target).open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow([key for key, _, _ in columns])
                    for index, value in enumerate(frames, 1):
                        writer.writerow((index,)+unpack(value))

        self.studio.button(foot, '导出 CSV', export, side='right')
        self.studio.button(foot, '下一页', lambda: page(200), side='right')
        self.studio.button(foot, '上一页', lambda: page(-200), side='right')
        page()

    def use_plan(self):
        self.jobs.set(self.studio.jobs_var.get())
        self.sim_timeout.set(self.studio.timeout_var.get())
        self.waves.set(self.studio.waves_var.get())

    def replay(self, all_traces=False):
        self.studio.idle()
        self.save()
        if not self.record or props.manifest(props.USER_FILE.read_bytes()) != self.record['functional']:
            raise ValueError('当前属性与求解归档不同，请先搜索新轨迹。')
        plan = dict(jobs=int(self.jobs.get()), timeout=int(self.sim_timeout.get()), waves=self.waves.get(),
                    check_mode=self.studio.check_modes[self.studio.check_mode_var.get()],
                    env_fatal=self.studio.env_fatal_var.get(), assert_fatal=self.studio.assert_fatal_var.get())
        if plan['jobs'] < 1 or plan['timeout'] < 0:
            raise ValueError('并行数至少为 1，仿真超时须为非负整数。')
        traces = [] if all_traces else [name for name in self.traces.selection() if self.trace_rows[name].get('frames')]
        if all_traces or traces:
            self.studio.launch('formal_replay', dict(campaign=self.record['id'], policy=plan, traces=traces))

    def last_replay(self):
        return next(iter(self.replays.get(self.trace().get('trace'), [])), (None, {}))

    def open_run(self):
        batch, run = self.last_replay()
        if batch:
            self.studio.refresh_results(batch)
            self.studio.show('results')
            self.studio.run_tree.selection_set(run['id'])
            self.studio.run_tree.see(run['id'])

    def open_vcs_wave(self):
        batch, run = self.last_replay()
        if batch:
            folder = backend.result_path(batch, run['id'])
            path = next((folder/name for name in ('waves.vcd', 'waves.fsdb') if (folder/name).is_file()), None)
            wave(self.studio, path, run['id'])

    def joint(self):
        self.studio.choose_coverage_batches([self.batches[0]], joint=True)

    def set_buttons(self, busy=None):
        busy = self.studio.jobs.busy() if busy is None else busy
        native = not busy and sys.platform == 'linux'
        active(self.bmc_button, native)
        active(self.search_button, native and any(p['kind'] == 'cover' for p in self.authored.values()))
        active(self.search_selected, native and any(self.authored.get(name, {}).get('kind') == 'cover' for name in self.index.selection()))
        if not hasattr(self, 'goal_rows'):
            return
        goal, trace = self.goal(), self.trace()
        _, run = self.last_replay()
        active(self.goal_source, bool(goal))
        active(self.goal_wave, not busy and bool(goal.get('trace')))
        active(self.goal_trace, bool(goal.get('trace_id')))
        active(self.trace_wave, not busy and bool(trace.get('path')))
        active(self.trace_pins, bool(trace.get('frames')))
        active(self.rerun_button, bool(self.record))
        active(self.missing_button, native and bool(self.retry_targets()))
        active(self.replay_all, native and any(item.get('frames') for item in self.trace_rows.values()))
        active(self.replay_button, native and any(self.trace_rows[name].get('frames') for name in self.traces.selection()))
        active(self.run_button, bool(run))
        active(self.vcs_wave, not busy and bool(run.get('wave_artifacts')))
        active(self.joint_button, native and bool(self.batches))
        if hasattr(self, 'package'):
            self.package.set_buttons(busy)

    def poll(self, job):
        self.activity.show(job)
        self.set_buttons(bool(job and job['state'] == 'RUNNING'))

    def show_package(self, identifier=None, run=None):
        self.package.refresh_exports(identifier)
        if run:
            self.package.refresh_runs(run)
        self.tabs.select(self.package_tab)
        self.studio.show('functional')
