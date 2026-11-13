"""Native coverage workspace with linked scopes, source lines, and bins."""
from bisect import bisect_left, bisect_right
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, ttk

from code_coverage.engine import json_lines
from code_coverage.set_cover import popcount
import model
from coverage_data import (METRICS, COVERED, PARTIAL, MISSED, EXCLUDED, UNREACHABLE, ILLEGAL,
                           CoverageData, item_state, line_coverage, urg_scores)
from widgets import ACCENT, INK, MUTED, CodeEditor, style, tree


PAGE_SIZE = 200
REPORTS = {'概览': 'dashboard.txt', '实例层级': 'hierarchy.txt', '模块': 'modlist.txt',
           '模块明细': 'modinfo.txt', '用例': 'tests.txt', '覆盖组': 'groups.txt'}
COLORS = {COVERED: '#42b795', PARTIAL: '#e8aa54', MISSED: '#ed7788', EXCLUDED: '#8895aa',
          UNREACHABLE: '#8895aa', ILLEGAL: '#ed7788'}


def score(value):
    return str(value)+'%' if value not in (None, '--') else '—'


class CoverageSource(CodeEditor):
    def __init__(self, parent, mono, on_line):
        self.marks = {}
        self.selected_line = None
        super().__init__(parent, mono, readonly=True, height=9)
        self.gutter.configure(width=72)
        self.text.configure(spacing1=1, spacing3=1)
        self.text.tag_configure('missed_coverage', background='#3a2634')
        self.text.tag_configure('partial_coverage', background='#3a3226')
        self.text.tag_configure('coverage_line', background='#304e79')
        def select(event):
            on_line(int(self.text.index('@0,{}'.format(event.y)).split('.')[0]))
        self.text.bind('<Button-1>', select, add='+')
        self.gutter.bind('<Button-1>', select)
        self.text.bind('<Configure>', lambda event: self.ensure_focus(), add='+')

    def ensure_focus(self):
        if self.selected_line:
            self.text.see('{}.0'.format(self.selected_line))

    def set_marks(self, marks):
        self.marks = marks
        for tag in ('missed_coverage', 'partial_coverage'):
            self.text.tag_remove(tag, '1.0', 'end')
        for line, state in marks.items():
            tag = {MISSED: 'missed_coverage', PARTIAL: 'partial_coverage'}.get(state)
            if tag:
                index = '{}.0'.format(line)
                self.text.tag_add(tag, index, index+' lineend+1c')
        self.redraw_gutter()

    def focus_line(self, line):
        self.selected_line = line
        self.text.tag_remove('coverage_line', '1.0', 'end')
        if line:
            index = '{}.0'.format(line)
            self.text.tag_add('coverage_line', index, index+' lineend+1c')
            self.text.tag_raise('coverage_line')
            self.text.see(index)
        self.redraw_gutter()

    def redraw_gutter(self):
        self.gutter.delete('all')
        index = self.text.index('@0,0')
        while True:
            box = self.text.dlineinfo(index)
            if box is None:
                break
            line = int(index.split('.')[0])
            y, height = box[1], box[3]
            if line == self.selected_line:
                self.gutter.create_rectangle(0, y, 72, y+height, fill='#304e79', width=0)
            color = COLORS.get(self.marks.get(line))
            if color:
                self.gutter.create_rectangle(9, y+5, 16, y+height-5, fill=color, width=0,
                                             tags=('coverage_marker',))
            self.gutter.create_text(62, y+2, text=str(line), anchor='ne', fill=color or '#8297b3',
                                    font=('TkFixedFont', 10))
            following = self.text.index(index+'+1line')
            if following == index:
                break
            index = following


class CoverageWindow(tk.Toplevel):
    def __init__(self, parent, record_path, minimize, open_verdi, guard, progress, busy, cancel, generate_formal):
        super().__init__(parent)
        self.record = model.eda.read_json(record_path)
        self.guard = guard
        self.generate_formal = generate_formal
        self.title('CMDBUF Studio · 代码覆盖率')
        self.geometry('{}x{}'.format(min(1460, self.winfo_screenwidth()-40),
                                      min(930, self.winfo_screenheight()-65)))
        self.minsize(1040, 680)
        self.ui, self.mono = style(self)
        self.data = CoverageData(json_lines(self.record['items']))
        self.scores = self.record['baseline']['metrics_percent']
        self.metrics = [(key, name) for key, name in METRICS if key in self.scores or key in self.data.metrics]
        report = Path(self.record['baseline']['report']).parent
        self.scope_scores = {'实例层级': urg_scores(report/'hierarchy.txt', hierarchy=True),
                             '模块': urg_scores(report/'modlist.txt')}
        self.cases = self.record['candidates']
        self.masks = {}
        if self.record.get('verified'):
            self.masks = {row['key']: int(row['covered'], 16) for row in json_lines(self.record['hits'])
                          if row['key'] != 'baseline'}
        self.item_page = self.case_page = 0
        self.case_bit = self.refresh_id = None
        self.source_file, self.current_line = None, None
        self.metric = tk.StringVar(value=next((key for key, _ in self.metrics if self.data.metrics[key]), 'LINE'))
        box = ttk.Frame(self, padding=(18, 14))
        box.pack(fill='both', expand=True)
        head = ttk.Frame(box)
        head.pack(fill='x')
        ttk.Label(head, text='代码覆盖率', font=(self.ui, 20, 'bold')).pack(side='left')
        ttk.Button(head, text='计算最小用例集', style='Primary.TButton',
                   command=lambda: guard(lambda: minimize(self.record['batches']))).pack(side='right')
        ttk.Button(head, text='Verdi 查看选集' if self.record.get('verified') else 'Verdi 查看基准',
                   command=lambda: guard(open_verdi)).pack(side='right', padx=8)
        header = '{} 个历史批次 · {:,} 个有效用例 · 分析 {}'.format(
            len(self.record['batches']), self.record['case_count'], self.record['id'])
        ttk.Label(box, text=header, style='Muted.TLabel').pack(anchor='w', pady=(4, 10))
        self.build_metric_bar(box)
        footer = ttk.Frame(box)
        footer.pack(side='bottom', fill='x', pady=(7, 0))
        ttk.Label(footer, textvariable=progress, style='Muted.TLabel').pack(side='left')
        stop = ttk.Button(footer, text='取消任务', command=cancel)
        def update_cancel(*args):
            if busy.get() and not stop.winfo_manager():
                stop.pack(side='right')
            elif not busy.get() and stop.winfo_manager():
                stop.pack_forget()
        trace = busy.trace_add('write', update_cancel)
        def cleanup(event):
            if event.widget == self:
                busy.trace_remove('write', trace)
                if self.refresh_id:
                    self.after_cancel(self.refresh_id)
        self.bind('<Destroy>', cleanup)
        update_cancel()
        theme = ttk.Style(self)
        theme.configure('Coverage.TButton', padding=(10, 5))
        theme.configure('Coverage.Treeview', rowheight=26)
        theme.configure('Coverage.Treeview.Heading', padding=(6, 4))
        theme.configure('Coverage.TNotebook.Tab', padding=(13, 6), background='#e8eef8')
        theme.map('Coverage.TNotebook.Tab', background=[('selected', 'white')],
                  foreground=[('selected', ACCENT)])
        self.tabs = ttk.Notebook(box, style='Coverage.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        self.build_items()
        self.build_overview()
        self.build_cases()
        self.build_reports()
        if self.record.get('verified'):
            self.build_minimum()

    def build_metric_bar(self, box):
        bar = ttk.Frame(box)
        bar.pack(fill='x', pady=(0, 9))
        self.metric_buttons = {}
        for index, (metric, name) in enumerate(self.metrics):
            card = tk.Frame(bar, bg='white', highlightthickness=1, highlightbackground='#dce4ef')
            card.grid(row=0, column=index, sticky='ew', padx=(0, 6))
            bar.columnconfigure(index, weight=1, uniform='metric')
            value = self.scores.get(metric)
            button = tk.Button(card, text=name+'  '+score(value), font=(self.ui, 12, 'bold'), anchor='w',
                               padx=12, pady=8, bd=0, cursor='hand2', bg='white', fg=INK,
                               activebackground='#edf3ff', command=lambda key=metric: self.pick_metric(key))
            button.pack(fill='x')
            self.metric_buttons[metric] = (card, button)
            rail = tk.Canvas(card, width=1, height=3, bg='#e9eef5', highlightthickness=0)
            rail.pack(fill='x')
            fraction = float(value)/100 if value not in (None, '--') else 0
            def draw(event, ratio=fraction):
                event.widget.delete('score')
                event.widget.create_rectangle(0, 0, event.width*ratio, 3, fill='#56aa9d', width=0, tags=('score',))
            rail.bind('<Configure>', draw)
            rail.bind('<Button-1>', lambda event, key=metric: self.pick_metric(key))
        self.mark_metric()

    def mark_metric(self):
        for key, (card, button) in self.metric_buttons.items():
            active = key == self.metric.get()
            card.configure(highlightbackground=ACCENT if active else '#dce4ef')
            button.configure(bg='#edf3ff' if active else 'white', fg=ACCENT if active else INK)

    def pick_metric(self, metric):
        self.metric.set(metric)
        self.mark_metric()
        self.tabs.select(self.source_tab)
        self.refresh_scopes()
        self.refresh_source_context()

    def page(self, name):
        frame = ttk.Frame(self.tabs, padding=(10, 9))
        self.tabs.add(frame, text=name)
        return frame

    def build_items(self):
        self.source_tab = self.page('源码覆盖')
        panes = ttk.Panedwindow(self.source_tab, orient='horizontal')
        panes.pack(fill='both', expand=True)
        sidebar = ttk.Frame(panes, width=323, padding=(0, 0, 8, 0))
        sidebar.pack_propagate(False)
        panes.add(sidebar, weight=0)
        self.scope_mode = tk.StringVar(value='实例层级')
        picker = ttk.Combobox(sidebar, state='readonly', textvariable=self.scope_mode, values=('实例层级', '模块'))
        picker.pack(fill='x', pady=(0, 6))
        picker.bind('<<ComboboxSelected>>', lambda event: self.change_scope_mode())
        frame, self.scopes = tree(sidebar, [('score', '分数', 66), ('missing', '缺口项', 63)])
        frame.pack(fill='both', expand=True)
        self.scopes.configure(show='tree headings', style='Coverage.Treeview')
        self.scopes.heading('#0', text='覆盖范围')
        self.scopes.column('#0', width=165, minwidth=130)
        self.scopes.column('score', anchor='e', stretch=False)
        self.scopes.column('missing', anchor='e', stretch=False)
        self.scopes.bind('<<TreeviewSelect>>', lambda event: self.refresh_source_context())

        main = ttk.Panedwindow(panes, orient='vertical')
        panes.add(main, weight=1)
        editor_pane = ttk.Frame(main)
        main.add(editor_pane, weight=3)
        editor_pane.columnconfigure(0, weight=1)
        editor_pane.rowconfigure(1, weight=1)
        source_tools = ttk.Frame(editor_pane)
        source_tools.grid(row=0, column=0, sticky='ew', pady=(0, 5))
        self.file_name = tk.StringVar()
        self.file_picker = ttk.Combobox(source_tools, textvariable=self.file_name, state='readonly', width=32)
        self.file_picker.pack(side='left', fill='x', expand=True)
        self.file_picker.bind('<<ComboboxSelected>>', lambda event: self.select_file())
        self.prev_gap = ttk.Button(source_tools, text='上一缺口', style='Coverage.TButton', command=lambda: self.goto_gap(-1))
        self.prev_gap.pack(side='left', padx=(6, 4))
        self.next_gap = ttk.Button(source_tools, text='下一缺口', style='Coverage.TButton', command=lambda: self.goto_gap(1))
        self.next_gap.pack(side='left')
        self.source = CoverageSource(editor_pane, self.mono, self.click_source_line)
        self.source.grid(row=1, column=0, sticky='nsew')
        legend = ttk.Frame(editor_pane)
        legend.grid(row=2, column=0, sticky='ew', pady=(5, 6))
        for text, color in ((COVERED, '#127960'), (PARTIAL, '#916115'), (MISSED, '#aa3b4e'), ('排除 / 不可达', MUTED)):
            ttk.Label(legend, text='● '+text, foreground=color).pack(side='left', padx=(0, 12))
        self.source_info = tk.StringVar()
        ttk.Label(legend, textvariable=self.source_info, style='Muted.TLabel').pack(side='right')

        detail = ttk.Frame(main)
        main.add(detail, weight=2)
        main.bind('<Configure>', lambda event: main.sashpos(0, int(event.height*.56)) if event.height > 300 else None)
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(1, weight=1)
        filters = ttk.Frame(detail)
        filters.grid(row=0, column=0, sticky='ew', pady=(3, 6))
        self.list_context = tk.StringVar(value='本行')
        self.item_state = tk.StringVar(value='全部状态')
        self.query = tk.StringVar()
        self.groups = tk.BooleanVar(value=False)
        for value, choices, width in (
            (self.list_context, ('本行', '本文件', '当前范围'), 8),
            (self.item_state, ('全部状态', '有缺口', COVERED, PARTIAL, MISSED, EXCLUDED, UNREACHABLE, ILLEGAL), 10),
        ):
            combo = ttk.Combobox(filters, textvariable=value, values=choices, width=width, state='readonly')
            combo.pack(side='left', padx=(0, 5))
            combo.bind('<<ComboboxSelected>>', lambda event: self.filter_items())
        ttk.Checkbutton(filters, text='显示分组', variable=self.groups, command=self.filter_items).pack(side='left', padx=5)
        ttk.Label(filters, text='搜索').pack(side='left', padx=(6, 4))
        ttk.Entry(filters, textvariable=self.query).pack(side='left', fill='x', expand=True)
        self.query.trace_add('write', self.schedule_filter)
        frame, self.item_table = tree(detail, [('name', '覆盖点', 330), ('bin', '覆盖项', 113),
                                               ('loc', '源码行', 95), ('state', '状态', 82),
                                               ('bins', '命中 / 总项', 94), ('count', '次数', 59)], height=4)
        self.item_table.configure(style='Coverage.Treeview')
        frame.grid(row=1, column=0, sticky='nsew')
        self.item_table.bind('<<TreeviewSelect>>', lambda event: self.guard(self.select_item))
        self.item_info = tk.StringVar()
        bar = ttk.Frame(detail)
        bar.grid(row=2, column=0, sticky='ew', pady=(5, 0))
        self.hit_button = ttk.Button(bar, text='查看命中用例', state='disabled', style='Coverage.TButton',
                                     command=lambda: self.guard(self.show_hit_cases))
        self.hit_button.pack(side='right')
        self.item_pager = self.pager(bar, self.item_info, self.change_item_page)
        self.bind('<F8>', lambda event: self.goto_gap(1))
        self.bind('<Shift-F8>', lambda event: self.goto_gap(-1))
        self.refresh_scopes()
        self.refresh_source_context()

    def scope_tree(self, widget, mode, values, select='@all'):
        widget.delete(*widget.get_children())
        widget.insert('', 'end', iid='@all', text='全部范围', values=values('@all'), open=True)
        if mode == '模块':
            for module in sorted({row['module'] for row in self.data.instances.values() if row['module']}):
                widget.insert('@all', 'end', iid=module, text=module, values=values(module))
        else:
            for name, row in self.data.instances.items():
                parent = row['parent_instance']
                if not widget.exists(parent) or not parent:
                    parent = '@all'
                widget.insert(parent, 'end', iid=name, text=name.rsplit('.', 1)[-1],
                              values=values(name), open=True)
        widget.selection_set(select if widget.exists(select) else '@all')

    def scores_for(self, mode, scope):
        return self.scores if scope == '@all' else self.scope_scores[mode].get(scope, {})

    def refresh_scopes(self, selected=None):
        mode, metric = self.scope_mode.get(), self.metric.get()
        def values(scope):
            percentages = self.scores_for(mode, scope)
            group = '全部范围' if scope == '@all' else mode
            gaps = self.data.missing[group, scope, metric]
            return score(percentages.get(metric)), '{:,}'.format(gaps)
        current = selected or next(iter(self.scopes.selection()), '@all')
        self.scope_tree(self.scopes, mode, values, current)

    def change_scope_mode(self):
        self.refresh_scopes('@all')
        self.refresh_source_context()

    def refresh_source_context(self):
        scope = next(iter(self.scopes.selection()), '@all')
        self.scope_rows = list(self.data.scoped_items(self.scope_mode.get(), scope, self.metric.get()))
        self.lines = line_coverage(self.scope_rows)
        self.gaps = sorted(location for location, row in self.lines.items() if row[2] in (MISSED, PARTIAL))
        files = sorted({row['file'] for row in self.scope_rows})
        self.file_labels = {path: self.record['sources'].get(path, {}).get('relative', Path(path).name or '无源码位置')
                            for path in files}
        if len(set(self.file_labels.values())) != len(self.file_labels):
            self.file_labels = {path: path or '无源码位置' for path in files}
        self.file_picker.configure(values=list(self.file_labels.values()))
        path = self.source_file if self.source_file in self.file_labels else (self.gaps[0][0] if self.gaps else next(iter(files), None))
        self.open_source(path, self.first_line(path))
        for button in (self.prev_gap, self.next_gap):
            button.configure(state='normal' if self.gaps else 'disabled')
        self.filter_items()

    def first_line(self, path):
        gaps = (line for file, line in self.gaps if file == path)
        return next(gaps, min((line for file, line in self.lines if file == path), default=None))

    def select_file(self):
        path = next((path for path, label in self.file_labels.items() if label == self.file_name.get()), None)
        self.open_source(path, self.first_line(path))
        self.filter_items()

    def open_source(self, path, line):
        self.file_name.set(self.file_labels.get(path, '没有对应源码'))
        if path != self.source_file or not hasattr(self, 'source_valid'):
            self.source_file = path
            record = self.record['sources'].get(path)
            self.source_valid = bool(record and Path(record['path']).is_file()
                                     and model.eda.digest(record['path']) == record['sha256'])
            if self.source_valid:
                code = Path(record['path']).read_text(encoding='utf-8', errors='replace')
            elif path:
                code = '历史源码缺少可校验的副本。\n'+path
            else:
                code = '当前范围没有可定位的源码覆盖项。'
            self.source_lines = code.splitlines() if self.source_valid else []
            self.source.set(code)
        self.source.set_marks({n: row[2] for (file, n), row in self.lines.items()
                               if file == path and n <= len(self.source_lines)})
        self.current_line = line
        self.source.focus_line(line if line and line <= len(self.source_lines) else None)
        row = self.lines.get((path, line))
        label = '第 {} 行 · {} · {}/{}'.format(line, row[2], row[0], row[1]) if row else (
                '第 {} 行'.format(line) if line else '—')
        self.source_info.set(label)

    def click_source_line(self, line):
        if self.source_valid:
            self.focus_gap_or_line(self.source_file, line)

    def focus_gap_or_line(self, path, line):
        self.list_context.set('本行')
        self.item_state.set('全部状态')
        if self.query.get():
            self.query.set('')
        self.open_source(path, line)
        self.filter_items()

    def goto_gap(self, direction):
        if not self.gaps:
            return
        self.tabs.select(self.source_tab)
        current = (self.source_file or '', self.current_line or 0)
        index = bisect_right(self.gaps, current) if direction > 0 else bisect_left(self.gaps, current)-1
        self.focus_gap_or_line(*self.gaps[index % len(self.gaps)])

    def schedule_filter(self, *args):
        if self.refresh_id:
            self.after_cancel(self.refresh_id)
        self.refresh_id = self.after(130, self.filter_items)

    def filter_items(self):
        if self.refresh_id:
            self.after_cancel(self.refresh_id)
            self.refresh_id = None
        query, status = self.query.get().strip().casefold(), self.item_state.get()
        context = self.list_context.get()
        rows = []
        for row in self.scope_rows:
            if context != '当前范围' and row['file'] != self.source_file:
                continue
            if context == '本行' and self.current_line is not None and row['line'] != self.current_line:
                continue
            if row['bin'] < 0 and not self.groups.get() and row['metric'] not in ('ASSERT', 'GROUP'):
                continue
            actual = item_state(row)
            if status != '全部状态' and actual not in ((MISSED, PARTIAL) if status == '有缺口' else (status,)):
                continue
            if query:
                text = ' '.join(self.display_item(row))
                context_text = '{} {} {}:{} {}'.format(text, self.data.labels[row['id']],
                                                       row['file'], row['line'], row['instance'])
                if query not in context_text.casefold():
                    continue
            rows.append(row)
        self.visible_items = rows
        self.item_page = 0
        self.change_item_page(0)

    def pager(self, bar, info, move):
        prev = ttk.Button(bar, text='上一页', style='Coverage.TButton', command=lambda: move(-1))
        next_page = ttk.Button(bar, text='下一页', style='Coverage.TButton', command=lambda: move(1))
        prev.pack(side='left')
        next_page.pack(side='left', padx=5)
        ttk.Label(bar, textvariable=info, style='Muted.TLabel').pack(side='left', padx=(4, 0))
        return prev, next_page

    def display_item(self, row):
        name, value = self.data.labels[row['id']], '—'
        if row['kind'] in ('vector', 'outcome', 'direction', 'state', 'transition', 'sequence'):
            parent = self.data.items.get(row['parent'])
            if parent:
                name, value = parent['name'], row['name'].replace(' -> ', ' → ')
        elif row['kind'] == 'statement' and row['file'] == self.source_file and 0 < row['line'] <= len(self.source_lines):
            name = self.source_lines[row['line']-1].strip()
            value = '语句'
        return name, value

    def change_item_page(self, delta):
        count = len(self.visible_items)
        self.item_page = min(max(0, (count-1)//PAGE_SIZE), max(0, self.item_page+delta))
        begin = self.item_page*PAGE_SIZE
        self.item_table.delete(*self.item_table.get_children())
        for row in self.visible_items[begin:begin+PAGE_SIZE]:
            name, value = self.display_item(row)
            status = item_state(row)
            ratio = '{}/{}'.format(row['covered'], row['total']) if row['total'] >= 0 else '—'
            loc = '{}:{}'.format(Path(row['file']).name, row['line']) if row['line'] > 0 else '—'
            if row['file'] == self.source_file and row['line'] > 0:
                loc = str(row['line'])
            self.item_table.insert('', 'end', iid=str(row['id']),
                                   values=(name, value, loc, status, ratio, row['count'] if row['count'] >= 0 else '—'),
                                   tags=({COVERED:'PASS', MISSED:'FAIL', PARTIAL:'INCOMPLETE'}.get(status, ''),))
        self.hit_button.configure(state='disabled')
        self.item_pager[0].configure(state='normal' if begin else 'disabled')
        self.item_pager[1].configure(state='normal' if begin+PAGE_SIZE < count else 'disabled')
        shown = '{}–{} / {:,} 项'.format(begin+1, min(begin+PAGE_SIZE, count), count) if count else '当前筛选无覆盖项'
        self.item_info.set(shown+'  ·  当前范围 {:,} 处源码缺口'.format(len(self.gaps)))

    def select_item(self):
        if not self.item_table.selection():
            return
        item = self.data.items[int(self.item_table.selection()[0])]
        self.hit_button.configure(state='normal' if self.masks and item['bin'] >= 0 else 'disabled')
        self.open_source(item['file'], item['line'] if item['line'] > 0 else None)

    def build_overview(self):
        overview = self.page('层级总览')
        controls = ttk.Frame(overview)
        controls.pack(fill='x', pady=(0, 7))
        self.overview_mode = tk.StringVar(value='实例层级')
        picker = ttk.Combobox(controls, state='readonly', textvariable=self.overview_mode, values=('实例层级','模块'), width=12)
        picker.pack(side='left')
        picker.bind('<<ComboboxSelected>>', lambda event: self.refresh_overview())
        ttk.Label(controls, text='URG 分数 · 双击指标跳转源码', style='Muted.TLabel').pack(side='left', padx=10)
        ttk.Button(controls, text='查看源码', command=self.open_overview_source).pack(side='right')
        columns = [('SCORE', '总分', 85)] + [(key, label, 92) for key, label in self.metrics]
        frame, self.overview = tree(overview, columns, height=12)
        self.overview.configure(show='tree headings', style='Coverage.Treeview')
        self.overview.heading('#0', text='层级 / 模块')
        self.overview.column('#0', width=290, minwidth=200)
        for key, _, _ in columns:
            self.overview.column(key, anchor='e')
        frame.pack(fill='both', expand=True)
        self.overview.bind('<Double-1>', self.open_overview_source)
        self.overview.bind('<Return>', self.open_overview_source)
        self.refresh_overview()

    def refresh_overview(self):
        mode = self.overview_mode.get()
        def values(scope):
            scores = self.scores_for(mode, scope)
            return [score(scores.get(key)) for key in ['SCORE'] + [key for key, _ in self.metrics]]
        self.scope_tree(self.overview, mode, values)

    def open_overview_source(self, event=None):
        selection = self.overview.selection()
        scope = self.overview.identify_row(event.y) if event and event.type == tk.EventType.ButtonPress else ''
        scope = scope or (selection[0] if selection else '@all')
        metric = self.metric.get()
        if event and event.type == tk.EventType.ButtonPress:
            column = self.overview.identify_column(event.x)
            index = int(column[1:])-2 if column else -1
            if 0 <= index < len(self.metrics):
                metric = self.metrics[index][0]
        self.scope_mode.set(self.overview_mode.get())
        self.metric.set(metric)
        self.mark_metric()
        self.refresh_scopes(scope)
        self.tabs.select(self.source_tab)
        self.refresh_source_context()

    def build_cases(self):
        self.cases_tab = self.page('历史用例')
        bar = ttk.Frame(self.cases_tab)
        bar.pack(fill='x', pady=(0, 6))
        self.case_info = tk.StringVar()
        ttk.Button(bar, text='显示全部', command=self.all_cases).pack(side='right')
        self.case_pager = self.pager(bar, self.case_info, self.change_case_page)
        frame, self.case_table = tree(self.cases_tab, [('batch','回归批次',270), ('id','用例 ID',250),
                                                       ('seed','种子',95), ('hits','命中覆盖项',130)], height=12)
        frame.pack(fill='both', expand=True)
        self.all_cases()

    def show_hit_cases(self):
        item = self.data.items[int(self.item_table.selection()[0])]
        self.case_bit = 1 << item['bin']
        self.visible_cases = [row for row in self.cases if self.masks[row['key']] & self.case_bit]
        self.case_page = 0
        self.change_case_page(0)
        self.tabs.select(self.cases_tab)

    def all_cases(self):
        self.case_bit = None
        self.visible_cases = self.cases
        self.case_page = 0
        self.change_case_page(0)

    def change_case_page(self, delta):
        count = len(self.visible_cases)
        self.case_page = min(max(0, (count-1)//PAGE_SIZE), max(0, self.case_page+delta))
        begin = self.case_page*PAGE_SIZE
        self.case_table.delete(*self.case_table.get_children())
        for row in self.visible_cases[begin:begin+PAGE_SIZE]:
            self.case_table.insert('', 'end', values=(row['batch'], row['id'], row['seed'],
                                                       popcount(self.masks[row['key']]) if self.masks else '—'))
        shown = '{}–{} / {:,}'.format(begin+1, min(begin+PAGE_SIZE, count), count) if count else '0'
        self.case_pager[0].configure(state='normal' if begin else 'disabled')
        self.case_pager[1].configure(state='normal' if begin+PAGE_SIZE < count else 'disabled')
        self.case_info.set(('命中所选覆盖项  ' if self.case_bit else '纳入覆盖率合并  ')+shown)

    def build_reports(self):
        tab = self.page('URG 报告')
        report = Path(self.record['baseline']['report']).parent
        selection = tk.StringVar(value='概览')
        combo = ttk.Combobox(tab, textvariable=selection, values=list(REPORTS), state='readonly', width=15)
        combo.pack(anchor='w', pady=(0, 6))
        editor = CodeEditor(tab, self.mono, readonly=True)
        editor.pack(fill='both', expand=True)
        def load():
            path = report / REPORTS[selection.get()]
            editor.set(path.read_text(encoding='utf-8', errors='replace') if path.is_file() else '此批次未生成该 URG 报告。')
        combo.bind('<<ComboboxSelected>>', lambda event: self.guard(load))
        load()

    def build_minimum(self):
        tab = self.page('最小用例集')
        solver = self.record['solver']
        title = ('已证明最小' if solver['optimal'] else
                 '已验证候选 · 最优值介于 {} 与 {}'.format(solver['lower_bound'], solver['upper_bound']))
        ttk.Label(tab, text=title+'   {:,} → {:,} 个用例'.format(self.record['case_count'], solver['upper_bound']),
                  style='Heading.TLabel').pack(anchor='w', pady=(0, 4))
        ttk.Label(tab, text='URG 重新合并已通过 · {:,} 个命中覆盖项一致'.format(self.record['covered']),
                  style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        frame, report = tree(tab, [('metric','覆盖率指标',200), ('before','历史基准',160), ('after','选集重新合并',180)], height=5)
        frame.pack(fill='x')
        for metric, name in METRICS:
            if metric in self.record['baseline']['metrics_percent']:
                report.insert('', 'end', values=(name+' '+metric, score(self.record['baseline']['metrics_percent'][metric]),
                                                  score(self.record['selected_coverage']['metrics_percent'].get(metric))))
        frame, selected = tree(tab, [('pattern','pattern',80), ('batch','回归批次',270),
                                     ('id','选中用例',245), ('seed','种子',95)], height=6)
        frame.pack(fill='both', expand=True, pady=8)
        for pattern, row in enumerate(self.record['selected']):
            selected.insert('', 'end', values=(pattern, row['batch'], row['id'], row['seed']))
        bar = ttk.Frame(tab)
        bar.pack(fill='x')
        ttk.Button(bar, text='另存用例清单 CSV', command=lambda: self.guard(self.save_csv)).pack(side='left')
        ttk.Button(bar, text='生成选集 Formal', style='Primary.TButton',
                   state='normal' if self.record['selected'] else 'disabled',
                   command=lambda: self.guard(self.generate_formal)).pack(side='right')
        ttk.Label(tab, text='使用存档 FSDB。缺少波形的选中用例会用原仿真器重放。',
                  style='Muted.TLabel').pack(anchor='e', pady=(5, 0))
        self.tabs.select(tab)

    def save_csv(self):
        path = filedialog.asksaveasfilename(parent=self, initialfile='selected_tests.csv', defaultextension='.csv',
                                            filetypes=[('CSV', '*.csv')])
        if path and Path(path).resolve() != Path(self.record['selected_csv']).resolve():
            shutil.copyfile(self.record['selected_csv'], path)
