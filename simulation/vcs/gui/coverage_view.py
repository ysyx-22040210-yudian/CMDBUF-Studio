"""Tk coverage explorer for saved NPI results and URG reports."""
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, ttk

from code_coverage.engine import json_lines
from code_coverage.set_cover import popcount
import model
from widgets import ACCENT, CodeEditor, style, tree


METRICS = (('LINE', '行'), ('COND', '条件'), ('TOGGLE', '翻转'), ('BRANCH', '分支'),
           ('FSM', '状态机'), ('ASSERT', '断言'), ('GROUP', '覆盖组'))
PAGE_SIZE = 250
REPORTS = {'概览': 'dashboard.txt', '实例层级': 'hierarchy.txt', '模块': 'modlist.txt',
           '模块明细': 'modinfo.txt', '用例': 'tests.txt', '覆盖组': 'groups.txt'}


def score(value):
    return str(value)+'%' if value not in (None, '--') else '—'


def state(row):
    flags = max(0, row['status'])
    if flags & (4 | 64):
        return '排除'
    if flags & 1:
        return '不可达'
    if flags & 2:
        return '非法'
    if row['total'] < 1:
        return '—'
    if row['covered'] == row['total']:
        return '已覆盖'
    return '部分覆盖' if row['covered'] else '未覆盖'


class CoverageWindow(tk.Toplevel):
    def __init__(self, parent, record_path, minimize, open_verdi, guard, progress, busy, cancel):
        super().__init__(parent)
        self.record = model.eda.read_json(record_path)
        self.guard = guard
        self.title('CMDBUF Studio · 代码覆盖率')
        self.geometry('{}x{}'.format(min(1460, self.winfo_screenwidth()-40),
                                      min(930, self.winfo_screenheight()-65)))
        self.minsize(1000, 650)
        _, self.mono = style(self)
        self.instances, self.items, self.labels = {}, {}, {}
        for row in json_lines(self.record['items']):
            if 'id' not in row:
                self.instances[row['instance']] = row
            else:
                self.items[row['id']] = row
                parent_label = self.labels.get(row['parent'])
                self.labels[row['id']] = parent_label+'  /  '+row['name'] if parent_label else row['name']
        self.cases = self.record['candidates']
        self.masks = {}
        if self.record.get('verified'):
            self.masks = {row['key']: int(row['covered'], 16) for row in json_lines(self.record['hits'])
                          if row['key'] != 'baseline'}
        self.item_page = self.case_page = 0
        self.case_bit = self.refresh_id = None
        self.source_file = None
        box = ttk.Frame(self, padding=(18, 14))
        box.pack(fill='both', expand=True)
        head = ttk.Frame(box)
        head.pack(fill='x')
        ttk.Label(head, text='代码覆盖率', style='Title.TLabel').pack(side='left')
        ttk.Button(head, text='计算最小用例集', style='Primary.TButton',
                   command=lambda: guard(lambda: minimize(self.record['batches']))).pack(side='right')
        ttk.Button(head, text='Verdi 查看选集' if self.record.get('verified') else 'Verdi 查看基准',
                   command=lambda: guard(open_verdi)).pack(side='right', padx=8)
        header = '{} 个历史批次 · {:,} 个有效用例 · 分析 {}'.format(
            len(self.record['batches']), self.record['case_count'], self.record['id'])
        ttk.Label(box, text=header, style='Muted.TLabel').pack(anchor='w', pady=(4, 10))
        cards = ttk.Frame(box)
        cards.pack(fill='x', pady=(0, 10))
        for metric, name in METRICS:
            values = self.record['baseline']['metrics_percent']
            if metric not in values:
                continue
            card = ttk.Frame(cards, padding=(12, 8), style='Card.TFrame')
            card.pack(side='left', expand=True, fill='x', padx=(0, 6))
            ttk.Label(card, text=name+' '+metric, style='Card.TLabel').pack(anchor='w')
            ttk.Label(card, text=score(values[metric]), style='Card.TLabel',
                      font=(self.mono, 17, 'bold')).pack(anchor='w', pady=(4, 0))
        footer = ttk.Frame(box)
        footer.pack(side='bottom', fill='x', pady=(7, 0))
        ttk.Label(footer, textvariable=progress, style='Muted.TLabel').pack(side='left')
        stop = ttk.Button(footer, text='取消覆盖率分析', command=cancel)
        def update_cancel(*args):
            if busy.get() and not stop.winfo_manager():
                stop.pack(side='right')
            elif not busy.get() and stop.winfo_manager():
                stop.pack_forget()
        trace = busy.trace_add('write', update_cancel)
        self.bind('<Destroy>', lambda event: busy.trace_remove('write', trace) if event.widget == self else None)
        update_cancel()
        theme = ttk.Style(self)
        theme.configure('Coverage.TNotebook.Tab', padding=(14, 7), background='#e8eef8')
        theme.map('Coverage.TNotebook.Tab', background=[('selected', 'white')],
                  foreground=[('selected', ACCENT)])
        self.tabs = ttk.Notebook(box, style='Coverage.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        self.build_items()
        self.build_cases()
        self.build_reports()
        if self.record.get('verified'):
            self.build_minimum()

    def page(self, name):
        frame = ttk.Frame(self.tabs, padding=(10, 9))
        self.tabs.add(frame, text=name)
        return frame

    def build_items(self):
        panes = ttk.Panedwindow(self.page('覆盖项与源码'), orient='horizontal')
        panes.pack(fill='both', expand=True)
        scope = ttk.Frame(panes, width=276, padding=(0, 0, 8, 0))
        scope.pack_propagate(False)
        panes.add(scope, weight=0)
        self.scope_mode = tk.StringVar(value='实例层级')
        pick = ttk.Combobox(scope, state='readonly', textvariable=self.scope_mode, values=('实例层级', '模块'))
        pick.pack(fill='x', pady=(0, 5))
        pick.bind('<<ComboboxSelected>>', lambda event: self.populate_scopes())
        horizontal = ttk.Scrollbar(scope, orient='horizontal')
        horizontal.pack(side='bottom', fill='x')
        scroll = ttk.Scrollbar(scope)
        scroll.pack(side='right', fill='y')
        self.scopes = ttk.Treeview(scope, show='tree', yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.scopes.column('#0', width=280, minwidth=160)
        self.scopes.pack(fill='both', expand=True)
        scroll.configure(command=self.scopes.yview)
        horizontal.configure(command=self.scopes.xview)
        self.scopes.bind('<<TreeviewSelect>>', lambda event: self.filter_items())

        main = ttk.Frame(panes)
        panes.add(main, weight=1)
        filters = ttk.Frame(main)
        filters.pack(fill='x', pady=(0, 6))
        self.metric_names = {name+' '+key: key for key, name in METRICS
                             if key in ('LINE','COND','TOGGLE','BRANCH','FSM')
                             or any(r['metric'] == key for r in self.items.values())}
        self.metric = tk.StringVar(value='行 LINE')
        self.item_state = tk.StringVar(value='全部状态')
        self.query = tk.StringVar()
        self.groups = tk.BooleanVar(value=False)
        for value, choices in (
            (self.metric, list(self.metric_names)),
            (self.item_state, ('全部状态', '已覆盖', '未覆盖', '部分覆盖', '排除', '不可达', '非法')),
        ):
            combo = ttk.Combobox(filters, state='readonly', width=12, textvariable=value, values=choices)
            combo.pack(side='left', padx=(0, 6))
            combo.bind('<<ComboboxSelected>>', lambda event: self.filter_items())
        ttk.Checkbutton(filters, text='含分组', variable=self.groups, command=self.filter_items).pack(side='left', padx=5)
        ttk.Label(filters, text='搜索').pack(side='left', padx=(7, 4))
        ttk.Entry(filters, textvariable=self.query).pack(side='left', fill='x', expand=True)
        self.query.trace_add('write', self.schedule_filter)
        split = ttk.Panedwindow(main, orient='vertical')
        split.pack(fill='both', expand=True)
        table = ttk.Frame(split)
        split.add(table, weight=3)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        frame, self.item_table = tree(table, [('name','覆盖项',390), ('loc','源码位置',150),
                                             ('state','状态',90), ('bins','覆盖 / 总项',94), ('count','计数',65)], height=8)
        frame.grid(row=0, column=0, sticky='nsew')
        self.item_table.bind('<<TreeviewSelect>>', lambda event: self.guard(self.select_item))
        self.item_info = tk.StringVar()
        bar = ttk.Frame(table)
        bar.grid(row=1, column=0, sticky='ew', pady=4)
        self.hit_button = ttk.Button(bar, text='查看命中用例', state='disabled',
                                     command=lambda: self.guard(self.show_hit_cases))
        self.hit_button.pack(side='right', padx=(6, 0))
        self.pager(bar, self.item_info, self.change_item_page)
        source = ttk.Frame(split)
        split.add(source, weight=2)
        self.source_label = tk.StringVar(value='选择覆盖项查看对应源码')
        ttk.Label(source, textvariable=self.source_label, style='Muted.TLabel', wraplength=830).pack(anchor='w', pady=(0, 5))
        self.source = CodeEditor(source, self.mono, readonly=True, height=8)
        self.source.pack(fill='both', expand=True)
        self.source.text.tag_configure('coverage_line', background='#304e79')
        self.populate_scopes()

    def populate_scopes(self):
        self.scopes.delete(*self.scopes.get_children())
        self.scopes.insert('', 'end', iid='@all', text='全部范围', open=True)
        if self.scope_mode.get() == '模块':
            for module in sorted({row['module'] for row in self.instances.values()}):
                self.scopes.insert('@all', 'end', iid=module, text=module)
        else:
            for name, row in self.instances.items():
                parent = row['parent_instance'] or '@all'
                self.scopes.insert(parent, 'end', iid=name,
                                   text='{}  ·  {}'.format(name.rsplit('.', 1)[-1], row['module']), open=True)
        self.scopes.selection_set('@all')
        self.filter_items()

    def schedule_filter(self, *args):
        if self.refresh_id:
            self.after_cancel(self.refresh_id)
        self.refresh_id = self.after(130, self.filter_items)

    def filter_items(self):
        self.refresh_id = None
        scope = next(iter(self.scopes.selection()), '@all')
        mode = self.scope_mode.get()
        metric = self.metric_names[self.metric.get()]
        query = self.query.get().strip().casefold()
        status = self.item_state.get()
        matching = [row for row in self.items.values() if row['metric'] == metric
                    and (scope == '@all' or (mode == '模块' and self.instances[row['instance']]['module'] == scope)
                         or (mode == '实例层级' and (row['instance'] == scope or row['instance'].startswith(scope+'.'))))]
        leaves = [row for row in matching if row['bin'] >= 0]
        self.metric_counts = (sum(row['covered'] for row in leaves), sum(row['total'] for row in leaves))
        self.visible_items = [row for row in matching if
                              (self.groups.get() or row['bin'] >= 0 or metric in ('ASSERT', 'GROUP'))
                              and (status == '全部状态' or state(row) == status)
                              and (not query or query in (self.labels[row['id']]+' '+row['file']+':'+
                                                          str(row['line'])+' '+row['instance']).casefold())]
        self.item_page = 0
        self.change_item_page(0)

    def pager(self, bar, info, move):
        ttk.Button(bar, text='上一页', command=lambda: move(-1)).pack(side='left')
        ttk.Button(bar, text='下一页', command=lambda: move(1)).pack(side='left', padx=5)
        ttk.Label(bar, textvariable=info, style='Muted.TLabel').pack(side='left', padx=(4, 0))

    def change_item_page(self, delta):
        count = len(self.visible_items)
        self.item_page = min(max(0, (count-1)//PAGE_SIZE), max(0, self.item_page+delta))
        begin = self.item_page*PAGE_SIZE
        self.item_table.delete(*self.item_table.get_children())
        for row in self.visible_items[begin:begin+PAGE_SIZE]:
            label = self.labels[row['id']]
            status = state(row)
            ratio = '{}/{}'.format(row['covered'], row['total']) if row['total'] >= 0 else '—'
            self.item_table.insert('', 'end', iid=str(row['id']),
                                   values=(label, '{}:{}'.format(Path(row['file']).name, row['line']), status,
                                           ratio, row['count'] if row['count'] >= 0 else '—'),
                                   tags=({'已覆盖':'PASS', '未覆盖':'FAIL', '部分覆盖':'INCOMPLETE'}.get(status, ''),))
        self.hit_button.configure(state='disabled')
        if self.source_file is not None:
            self.source_file = None
            self.source_label.set('选择覆盖项查看对应源码')
            self.source.set('')
        shown = '{}–{} / {:,}'.format(begin+1, min(begin+PAGE_SIZE, count), count) if count else '无匹配项'
        self.item_info.set(shown+'  ·  当前范围逐项覆盖 {:,} / {:,}'.format(*self.metric_counts))

    def select_item(self):
        if not self.item_table.selection():
            return
        item = self.items[int(self.item_table.selection()[0])]
        self.hit_button.configure(state='normal' if self.masks and item['bin'] >= 0 else 'disabled')
        location = '{}:{}'.format(item['file'], item['line'])
        details = '{}  ·  {}  ·  {}'.format(item['instance'], item['kind'], state(item))
        if item['goal'] >= 0:
            details += '  ·  目标次数 '+str(item['goal'])
        source = self.record['sources'].get(item['file'])
        if self.source_file != item['file']:
            self.source_file = item['file']
            self.source_valid = bool(source and Path(source['path']).is_file()
                                     and model.eda.digest(source['path']) == source['sha256'])
            self.source.set(Path(source['path']).read_text(encoding='utf-8', errors='replace') if self.source_valid
                            else '此历史文件没有可校验的源码副本。覆盖项和源位置仍来自 VDB。')
        self.source_label.set(details+'\n'+(source['relative']+':'+str(item['line']) if self.source_valid else location))
        self.source.text.tag_remove('coverage_line', '1.0', 'end')
        if self.source_valid and item['line'] > 0:
            start = '{}.0'.format(item['line'])
            self.source.text.tag_add('coverage_line', start, start+' lineend+1c')
            self.source.text.see(start)

    def build_cases(self):
        self.cases_tab = self.page('历史用例')
        bar = ttk.Frame(self.cases_tab)
        bar.pack(fill='x', pady=(0, 6))
        self.case_info = tk.StringVar()
        ttk.Button(bar, text='显示全部', command=self.all_cases).pack(side='right')
        self.pager(bar, self.case_info, self.change_case_page)
        frame, self.case_table = tree(self.cases_tab, [('batch','回归批次',270), ('id','用例 ID',250),
                                                       ('seed','种子',95), ('hits','命中覆盖项',130)], height=12)
        frame.pack(fill='both', expand=True)
        self.all_cases()

    def show_hit_cases(self):
        item = self.items[int(self.item_table.selection()[0])]
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
        frame, selected = tree(tab, [('batch','回归批次',270), ('id','选中用例',245), ('seed','种子',95)], height=6)
        frame.pack(fill='both', expand=True, pady=8)
        for row in self.record['selected']:
            selected.insert('', 'end', values=(row['batch'], row['id'], row['seed']))
        bar = ttk.Frame(tab)
        bar.pack(fill='x')
        ttk.Button(bar, text='另存用例清单 CSV', command=lambda: self.guard(self.save_csv)).pack(side='left')
        self.tabs.select(tab)

    def save_csv(self):
        path = filedialog.asksaveasfilename(parent=self, initialfile='selected_tests.csv', defaultextension='.csv',
                                            filetypes=[('CSV', '*.csv')])
        if path and Path(path).resolve() != Path(self.record['selected_csv']).resolve():
            shutil.copyfile(self.record['selected_csv'], path)
