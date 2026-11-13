#!/usr/bin/env python3
"""CMDBUF Studio: native Tk/ttk desktop, local Linux EDA, Python stdlib only."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
except ImportError:
    print('CMDBUF Studio 需要 Python Tk。Debian/Ubuntu: sudo apt install python3-tk；RHEL/CentOS: sudo yum install python3-tkinter。', file=sys.stderr)
    raise SystemExit(2)

import backend
import model
import formal_model
from coverage_view import CoverageWindow
from widgets import (BG, INK, MUTED, ACCENT, DARK, CodeEditor, WaveWindow, ScrollPage,
                     style, tree, multiline, formal_format_field)

eda, ROOT, STATE = model.eda, model.ROOT, model.STATE


class Studio:
    def __init__(self, root):
        self.root = root; self.ui, self.mono = style(root)
        root.title('CMDBUF Studio · 本机 EDA 工作台')
        root.geometry('%dx%d'%(min(1460, max(1180, root.winfo_screenwidth()-70)), min(950, max(700, root.winfo_screenheight()-85))))
        root.minsize(1180, 700)
        root.configure(background=BG); root.protocol('WM_DELETE_WINDOW', self.close)
        self.sva_window = None
        self.jobs = backend.Jobs(); self.last_finished = None; self.job_window = None; self.job_log = None
        self.rtl_import = None
        self.current_tc = None; self.current_batch = None; self.formal_export = None
        self.batch_rows = {}; self.final_results = None
        self.run_offset = 0; self.displayed_runs = 0
        self.dirty = False; self.dut_baseline = None; self.plan_rows = []; self.formal_items = []
        self.status = tk.StringVar(value='就绪 · 当前设备本机执行')
        self.coverage_busy = tk.BooleanVar(value=False)
        self.pages = {}; self.page_shells = {}; self.nav = {}; self.settings = model.state_read()
        self.build_shell()
        for key, title, subtitle, method in [
            ('home', '验证工作台', '本机运行 · VCS 仿真 · Verdi 调试 · FSDB 转 Formal', self.build_home),
            ('dut', 'DUT 集成', '导入其他 CMDBUF RTL，维护编译顺序与 dut 实例端口连接', self.build_dut),
            ('cases', '用例编辑', '只编辑 main_phase；在每次时钟上升沿后，用过程赋值与 if / else 实现激励约束', self.build_cases),
            ('regression', '回归计划', '按用例设置次数、起始种子与周期数。仿真期间保存的工程和计划修改用于后续回归。', self.build_regression),
            ('results', '结果与波形', '用例结束后自动显示结果与运行记录，可在本机 Verdi 中打开波形和覆盖率', self.build_results),
            ('formal', 'FSDB → Formal', 'Verdi NPI 直接逐拍提取；一份 FSDB 对应一个 pattern', self.build_formal),
            ('settings', '本机工具', '自动识别当前设备、工程目录、EDA 命令和图形会话', self.build_settings)]:
            shell = ScrollPage(self.content); shell.grid(row=0, column=0, sticky='nsew')
            self.page_shells[key] = shell; frame = shell.body; self.pages[key] = frame
            ttk.Label(frame, text=title, style='Title.TLabel').pack(anchor='w')
            ttk.Label(frame, text=subtitle, style='Muted.TLabel', wraplength=1080).pack(anchor='w', pady=(7, 20))
            method(frame)
        self.refresh_cases(); self.load_dut(); self.load_plan(); self.refresh_results(); self.restore_formal()
        self.show('home'); self.poll_id = root.after(200, self.poll)
        self.detect_timer = root.after(100, self.auto_detect)
        root.bind('<Control-s>', lambda event: self.guard(self.save_active))
        root.report_callback_exception = lambda cls, exc, tb: self.error(str(exc))

    def build_shell(self):
        self.sidebar = tk.Frame(self.root, background=DARK, width=214)
        self.sidebar.pack(side='left', fill='y'); self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text='C / S', bg=DARK, fg='#7da5ff', font=(self.mono, 26, 'bold')).pack(anchor='w', padx=24, pady=(26, 6))
        tk.Label(self.sidebar, text='CMDBUF\nSTUDIO', bg=DARK, fg='white', justify='left', font=(self.ui, 18, 'bold')).pack(anchor='w', padx=24)
        tk.Label(self.sidebar, text='NATIVE EDA WORKSPACE', bg=DARK, fg='#8095b5', font=(self.ui, 8)).pack(anchor='w', padx=24, pady=(9, 28))
        for key, title in [('home', '01   工作台'), ('dut', '02   DUT 集成'), ('cases', '03   用例编辑'), ('regression', '04   回归计划'),
                           ('results', '05   结果与波形'), ('formal', '06   FSDB → Formal'), ('settings', '07   本机工具')]:
            button = tk.Button(self.sidebar, text=title, anchor='w', padx=20, pady=13, bd=0,
                               background=DARK, foreground='#aabbd3', activebackground='#243b60', activeforeground='white',
                               font=(self.ui, 11), highlightthickness=0, cursor='hand2', command=lambda k=key: self.show(k))
            button.pack(fill='x', padx=12, pady=3); self.nav[key] = button
        tk.Label(self.sidebar, text='Tk / ttk  ·  本机执行\nVCS  /  VERDI  /  NPI', bg=DARK, fg='#8195b2',
                 justify='left', font=(self.ui, 9)).pack(side='bottom', anchor='w', padx=24, pady=24)
        body = ttk.Frame(self.root); body.pack(side='left', fill='both', expand=True)
        top = tk.Frame(body, bg='white', height=52); top.pack(fill='x'); top.pack_propagate(False)
        tk.Label(top, text='CMDBUF  /  Verification workspace', bg='white', fg=MUTED, font=(self.ui, 10)).pack(side='left', padx=26)
        label = '●  LINUX · LOCAL' if sys.platform == 'linux' else '●  WINDOWS · 工程编辑'
        tk.Label(top, text=label, bg='#edf7f3', fg='#20866f', font=(self.ui, 9, 'bold'), padx=14, pady=5).pack(side='right', padx=24, pady=9)
        self.content = ttk.Frame(body); self.content.pack(fill='both', expand=True)
        self.content.rowconfigure(0, weight=1); self.content.columnconfigure(0, weight=1)
        footer = ttk.Frame(body, padding=(22, 9)); footer.pack(side='bottom', fill='x', before=self.content)
        ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', width=69).pack(side='left', fill='x', expand=True)
        self.progress = ttk.Progressbar(footer, length=150); self.progress.pack(side='left', padx=12)
        self.cancel_coverage_button = ttk.Button(footer, text='取消任务', command=self.jobs.cancel_coverage)
        self.stop_regression_button = ttk.Button(footer, text='停止回归',
                                                 command=lambda: self.guard(self.jobs.stop_regression))
        ttk.Button(footer, text='任务日志', command=self.show_log).pack(side='right')

    def button(self, parent, text, action, primary=False, side='left'):
        b = ttk.Button(parent, text=text, command=lambda: self.guard(action), style='Primary.TButton' if primary else 'TButton')
        b.pack(side=side, padx=(0, 8), pady=3); return b

    def bar(self, parent):
        f = ttk.Frame(parent); f.pack(fill='x', pady=(0, 12)); return f

    def error(self, text):
        self.status.set(text)
        messagebox.showerror('CMDBUF Studio', text, parent=self.root)

    def guard(self, action):
        try:
            return action()
        except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
            self.error(str(exc))

    def idle(self):
        self.jobs.require_idle()
        self.require_rtl_import_idle()

    def require_rtl_import_idle(self):
        if self.rtl_import is not None:
            raise ValueError('RTL 文件正在导入，请等待导入完成')

    def show(self, key):
        self.page = key; self.page_shells[key].tkraise()
        for name, button in self.nav.items():
            button.configure(bg='#263e64' if key == name else DARK, fg='white' if key == name else '#aabbd3')

    def notice(self, parent, title, text):
        card = tk.Frame(parent, background='#eaf0ff', padx=18, pady=14)
        card.pack(fill='x', pady=(0, 18))
        tk.Label(card, text=title, background='#eaf0ff', foreground='#3158a7', font=(self.ui, 11, 'bold'), anchor='w').pack(fill='x')
        tk.Label(card, text=text, background='#eaf0ff', foreground='#526b96', font=(self.ui, 10), justify='left',
                 wraplength=980, anchor='w').pack(fill='x', pady=(6, 0))

    def build_home(self, page):
        self.notice(page, '从目标设备直接开始', '在装有 EDA 工具的 Linux 桌面运行 make gui。Studio 通过本机 Make 调用 VCS、Verdi 和 NPI，使用当前 shell 的工具与许可证环境。')
        metrics = ttk.Frame(page); metrics.pack(fill='x', pady=(0, 22))
        self.home_metrics = []
        for title, initial in [('SV 用例', '—'), ('回归批次', '—'), ('最近结果', '—')]:
            card = tk.Frame(metrics, bg='white', padx=24, pady=18, highlightbackground='#e0e6ef', highlightthickness=1)
            card.pack(side='left', fill='x', expand=True, padx=(0, 12))
            tk.Label(card, text=title, bg='white', fg=MUTED, font=(self.ui, 10)).pack(anchor='w')
            value = tk.StringVar(value=initial); self.home_metrics.append(value)
            tk.Label(card, textvariable=value, bg='white', fg=INK, font=(self.ui, 24, 'bold')).pack(anchor='w', pady=(7, 0))
        ttk.Label(page, text='验证流程', style='Heading.TLabel').pack(anchor='w', pady=(0, 12))
        for index, title, detail, key in [('1', '集成 DUT', '导入 RTL，核对编译文件和端口例化', 'dut'),
                                           ('2', '编写激励', '只修改 main_phase，使用 if 分支满足输入约束', 'cases'),
                                           ('3', '配置回归', '设置每个用例的运行次数与 seed，选择是否生成波形', 'regression'),
                                           ('4', '分析结果', '原生波形画布、Verdi 覆盖率以及逐拍 Formal 导出', 'results')]:
            card = tk.Frame(page, bg='white', padx=18, pady=13); card.pack(fill='x', pady=(0, 8))
            tk.Label(card, text=index, bg='#eaf0ff', fg=ACCENT, font=(self.mono, 14, 'bold'), width=3, pady=5).pack(side='left', padx=(0, 16))
            texts = tk.Frame(card, bg='white'); texts.pack(side='left')
            tk.Label(texts, text=title, bg='white', fg=INK, font=(self.ui, 11, 'bold')).pack(anchor='w')
            tk.Label(texts, text=detail, bg='white', fg=MUTED, font=(self.ui, 9)).pack(anchor='w', pady=(4, 0))
            ttk.Button(card, text='打开  →', command=lambda k=key: self.show(k)).pack(side='right')
        bar = self.bar(page)
        self.button(bar, '检查本机工具', lambda: self.launch('doctor'), True)
        self.button(bar, 'VCS 编译', lambda: self.launch('compile'))
        ttk.Label(page, text='工程目录：'+str(ROOT)+'\n结果目录：'+str(eda.OUT), style='Muted.TLabel', wraplength=1050).pack(anchor='w', pady=8)

    def build_dut(self, page):
        bar = self.bar(page)
        self.button(bar, '导入 RTL 文件', lambda: self.import_rtl(False))
        self.button(bar, '导入 RTL 目录', lambda: self.import_rtl(True))
        self.button(bar, '保存 DUT', self.save_dut, True)
        self.button(bar, '编辑端口 SVA', self.edit_sva)
        self.button(bar, '编译', lambda: self.compile_dut())
        self.button(bar, '重新加载', self.reload_dut)
        self.dut_name = tk.StringVar()
        row = self.bar(page); ttk.Label(row, text='配置名称').pack(side='left', padx=(0, 10))
        ttk.Entry(row, textvariable=self.dut_name, width=45).pack(side='left')
        pane = ttk.Panedwindow(page, orient='horizontal'); pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane); right = ttk.Frame(pane); pane.add(left, weight=1); pane.add(right, weight=3)
        ttk.Label(left, text='编译文件 · 每行一个，相对工程目录').pack(anchor='w', pady=(0, 8))
        f, self.dut_files = multiline(left, self.mono, 9); f.pack(fill='both', expand=True)
        ttk.Label(left, text='Include 目录 · 每行一个').pack(anchor='w', pady=(12, 6))
        f, self.dut_includes = multiline(left, self.mono, 3); f.pack(fill='x')
        ttk.Label(left, text='宏定义 · 每行一个 NAME 或 NAME=value').pack(anchor='w', pady=(12, 6))
        f, self.dut_defines = multiline(left, self.mono, 3); f.pack(fill='x')
        ttk.Button(left, text='查看环境接口', command=self.ports_help).pack(fill='x', pady=(12, 0))
        ttk.Label(right, text='DUT 例化 / 端口适配 · 实例名保留 dut').pack(anchor='w', pady=(0, 8))
        self.dut_code = CodeEditor(right, self.mono); self.dut_code.pack(fill='both', expand=True)
        ttk.Label(page, text='导入文件会复制进当前工程；检查依赖顺序后保存。换用同协议 RTL 时，检查器与用例继续复用。', style='Muted.TLabel').pack(anchor='w', pady=(10, 0))

    def text_set(self, widget, value):
        widget.delete('1.0', 'end'); widget.insert('1.0', value)

    def load_dut(self):
        cfg = model.dut_config.load(); self.dut_version = eda.digest(eda.HERE/'dut.json')
        self.dut_name.set(cfg['name']); self.dut_code.set(cfg['instance'])
        for key, widget in [('files', self.dut_files), ('include_dirs', self.dut_includes), ('defines', self.dut_defines)]:
            self.text_set(widget, '\n'.join(cfg[key]))
        self.dut_baseline = self.dut_value()

    def dut_value(self):
        cfg = dict(name=self.dut_name.get(), instance=self.dut_code.get())
        for key, widget in [('files', self.dut_files), ('include_dirs', self.dut_includes), ('defines', self.dut_defines)]:
            cfg[key] = [s.strip() for s in widget.get('1.0', 'end').splitlines() if s.strip()]
        return cfg

    def reload_dut(self):
        if self.dut_value() != self.dut_baseline and not messagebox.askyesno('重新加载', '放弃尚未保存的 DUT 修改？', parent=self.root):
            return
        self.load_dut()

    def save_dut(self):
        self.jobs.require_editable_sources()
        cfg = self.dut_value(); self.dut_version = backend.save_dut(cfg, self.dut_version)
        self.dut_baseline = copy.deepcopy(cfg); self.status.set('DUT 配置已保存；下一次编译将使用新配置')

    def edit_sva(self):
        if self.sva_window and self.sva_window.winfo_exists():
            self.sva_window.lift()
            return
        self.sva_snapshot = model.load_sva()
        window = tk.Toplevel(self.root)
        self.sva_window = window
        window.title('端口检查器 · cmdbuf_rd_port_sva.sv')
        window.geometry('1160x760')
        window.minsize(790, 490)
        window.configure(background=BG)
        window.transient(self.root)
        window.protocol('WM_DELETE_WINDOW', lambda: self.guard(self.close_sva))
        toolbar = ttk.Frame(window, padding=(16, 12))
        toolbar.pack(fill='x')
        self.button(toolbar, '保存 SVA', self.save_sva, True)
        self.button(toolbar, '关闭', self.close_sva)
        self.sva_note = tk.StringVar(value='Ctrl + S 保存')
        ttk.Label(toolbar, textvariable=self.sva_note, style='Muted.TLabel').pack(side='right')
        ttk.Label(window, text=self.sva_snapshot['file'], style='Muted.TLabel').pack(anchor='w', padx=16)
        self.sva_editor = CodeEditor(window, self.mono)
        self.sva_editor.pack(fill='both', expand=True, padx=16, pady=(10, 16))
        self.sva_editor.set(self.sva_snapshot['source'])
        def save_shortcut(event):
            self.guard(self.save_sva)
            return 'break'
        window.bind('<Control-s>', save_shortcut)
        self.sva_editor.text.focus_set()

    def sva_dirty(self):
        return bool(self.sva_window and self.sva_window.winfo_exists() and
                    self.sva_editor.get() != self.sva_snapshot['source'])

    def save_sva(self):
        self.jobs.require_editable_sources()
        self.sva_snapshot = model.save_sva(self.sva_editor.get(), self.sva_snapshot['version'])
        self.sva_note.set('已保存 · '+self.sva_snapshot['version'][:12])
        self.status.set('端口 SVA 已保存，下次编译使用此版本')

    def close_sva(self):
        if not self.sva_window or not self.sva_window.winfo_exists():
            return True
        if self.sva_dirty():
            choice = messagebox.askyesnocancel('保存 SVA', '关闭前保存端口 SVA 的修改？', parent=self.sva_window)
            if choice is None:
                return False
            if choice:
                self.save_sva()
        self.sva_window.destroy()
        self.sva_window = None
        return True

    def compile_dut(self):
        self.idle()
        self.save_dut(); self.launch('compile')

    def import_rtl(self, directory):
        self.require_rtl_import_idle()
        if directory:
            base = filedialog.askdirectory(title='选择 RTL 目录', parent=self.root)
            if not base: return
            paths = [str(p) for p in sorted(Path(base).rglob('*')) if p.is_file() and p.suffix.lower() in ('.v', '.sv', '.vh', '.svh', '.f')]
        else:
            paths = list(filedialog.askopenfilenames(title='选择 RTL 与头文件', filetypes=[('RTL', '*.sv *.v *.svh *.vh *.f')], parent=self.root)); base = None
        if paths:
            self.jobs.require_editable_sources()
            task = backend.Jobs()
            task.start('import_rtl', dict(paths=paths, base=base))
            self.rtl_import = task
            self.status.set('正在导入 RTL 文件')

    def ports_help(self):
        text = '\n'.join('%-18s %-7s %s'%p for p in model.dut_config.PORTS)
        self.preview('环境端口 · CMDBUF RD', text+'\n\nrd_mem_status 保持四态 input wire [1:0] rd_mem_status [7:0]。\n在例化窗口将环境信号连接至新 DUT；接口语义变化需要同步检查器。')

    def build_cases(self, page):
        bar = self.bar(page)
        self.button(bar, '新建用例', self.new_case, True)
        self.button(bar, '保存 main_phase', self.save_case)
        self.button(bar, '重新加载', self.reload_case)
        self.button(bar, '编写说明', self.case_help)
        self.case_note = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.case_note, style='Muted.TLabel').pack(side='right')
        pane = ttk.Panedwindow(page, orient='horizontal'); pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane); right = ttk.Frame(pane); pane.add(left, weight=1); pane.add(right, weight=4)
        f, self.case_tree = tree(left, [('name', 'SystemVerilog 用例', 210)], height=15); f.pack(fill='both', expand=True)
        self.case_tree.bind('<<TreeviewSelect>>', self.select_case)
        self.case_editor = CodeEditor(right, self.mono, on_change=self.case_changed); self.case_editor.pack(fill='both', expand=True)
        ttk.Label(page, text='Ctrl + S 保存。框架只校验并原样驱动输入；随机分支必须显式满足约束，非法输入会报错。', style='Muted.TLabel').pack(anchor='w', pady=(12, 0))

    def refresh_cases(self, selected=None):
        values = model.cases(); self.case_tree.delete(*self.case_tree.get_children())
        for case in values:
            self.case_tree.insert('', 'end', iid=case['name'], values=(case['name'],))
        self.home_metrics[0].set(str(len(values)))
        selected = selected or (self.current_tc['name'] if self.current_tc else 'tc_user_example')
        if self.case_tree.exists(selected):
            self.load_case(selected); self.case_tree.selection_set(selected); self.case_tree.see(selected)

    def case_changed(self):
        self.dirty = bool(self.current_tc and self.case_editor.get().strip() != self.current_tc['phase'].strip())
        if self.current_tc:
            self.case_note.set(self.current_tc['name']+('  ·  未保存' if self.dirty else '  ·  已保存'))

    def load_case(self, name):
        self.current_tc = model.case(name); self.case_editor.set(self.current_tc['phase']); self.dirty = False
        self.case_note.set(name+'  ·  已保存')

    def allow_case_change(self):
        if not self.dirty: return True
        choice = messagebox.askyesnocancel('未保存的用例', '先保存当前 main_phase？选择“否”将放弃修改。', parent=self.root)
        if choice is None: return False
        if choice: self.save_case()
        return True

    def select_case(self, event=None):
        selected = self.case_tree.selection()
        if not selected or (self.current_tc and selected[0] == self.current_tc['name']): return
        try:
            if self.allow_case_change(): self.load_case(selected[0])
            else: self.case_tree.selection_set(self.current_tc['name'])
        except Exception as exc:
            if self.current_tc: self.case_tree.selection_set(self.current_tc['name'])
            self.error(str(exc))

    def reload_case(self):
        if self.current_tc and (not self.dirty or messagebox.askyesno('重新加载', '放弃未保存的 main_phase 修改？', parent=self.root)):
            self.load_case(self.current_tc['name'])

    def save_case(self):
        self.jobs.require_editable_sources()
        if not self.current_tc: raise ValueError('请先选择用例')
        self.current_tc = model.save_case(dict(name=self.current_tc['name'], version=self.current_tc['version'], phase=self.case_editor.get()))
        self.dirty = False; self.case_note.set(self.current_tc['name']+'  ·  已保存'); self.status.set('main_phase 已保存，文件其余部分保持原样')

    def new_case(self):
        if not self.allow_case_change(): return
        dialog = tk.Toplevel(self.root); dialog.title('从模板创建 SV 用例'); dialog.geometry('470x245'); dialog.transient(self.root)
        f = ttk.Frame(dialog, padding=24); f.pack(fill='both', expand=True)
        name = tk.StringVar(value='tc_my_case'); template = tk.StringVar(value='tc_user_example')
        for title, widget in [('用例名称', ttk.Entry(f, textvariable=name)), ('模板', ttk.Combobox(f, textvariable=template, values=[c['name'] for c in model.cases()], state='readonly'))]:
            ttk.Label(f, text=title).pack(anchor='w'); widget.pack(fill='x', pady=(5, 12))
        def create():
            self.jobs.require_editable_sources()
            source = model.case(template.get())
            created = model.save_case(dict(name=name.get().strip(), phase=source['phase'], create=True, template=template.get()))
            self.refresh_cases(created['name']); self.reconcile_plan(); dialog.destroy()
        ttk.Button(f, text='创建并编辑 main_phase', style='Primary.TButton', command=lambda: self.guard(create)).pack(anchor='e')
        dialog.grab_set()

    def case_help(self):
        self.preview('main_phase 编写说明', '''每次 @(posedge clk) 在同一仿真时间点驱动一组输入，供下一个上升沿采样。

1. 保留模板的 @(posedge clk) 与 begin_cycle(cycle)。begin_cycle 等待本沿 DUT 更新完成。
2. rst_n 保持 1，设置 rready、clk_low_ns / clk_high_ns / clk_pause_ns。
3. drive_control(); sample_context(); 更新控制并读取同一时间点内稳定的 DUT 反馈。
4. 用普通 if / else 约束 AR 和 Bank 输入，再给数据赋值。
   if (cycle == 8 && arready[0] === 1'b1) begin
       src = 0; arvalid = 1;
   end else begin
       src = 3; arvalid = 0;
   end
   for (int b=0; b<8; b++) begin
       if (req[b]) ar_rd_gnt[b] = 1;
       else ar_rd_gnt[b] = 0;
   end
   src=0/1/2 时对应 arready 必须为 1，空闲拍也要满足。随机时从已 ready 的源中选。
   ar_rd_gnt 只发给同拍有 req 的 Bank。成功发射的 Bank 半区按模板设置 busy。
5. 按模板处理 mem_due / due_data / due_error，保持固定延迟返回。
6. drive_inputs() 检查并驱动输入。用例需要在赋值时满足约束。

定向用例：if (cycle == 8) ... 指定某一拍；其他拍在 else 给明确值。
随机用例：将 $urandom / $urandom_range 写在满足已登记约束的分支内。
时钟：在每拍设置三个 clk_*_ns 控制下一段低/高电平与暂停。
启动时 clk 为 1。默认 5 ns 下降、10 ns 上升，cycle=0 在 10 ns 驱动供 20 ns 采样。

旧模板中的两个固定 #1 等待会在编译副本中迁移。其他过程延时仍按源码执行。
环境在 t=0 拉低 rst_n，#1（1 ns）释放，之后一直为 1。TC 再次拉低会报错。

所有注册约束的正反例与检查器继续使用原环境；不能自动推断任意新 RTL 的全部约束。
详细说明：documents/47_*（沿对齐）、documents/42_*（接口约束）、documents/38_*（启动时序）。''')

    def build_regression(self, page):
        bar = self.bar(page)
        self.button(bar, '开始回归', self.run_regression, True); self.button(bar, '保存计划', self.save_plan)
        self.button(bar, '刷新用例列表', self.reconcile_plan)
        self.plan_count = tk.StringVar(); ttk.Label(bar, textvariable=self.plan_count, style='Muted.TLabel').pack(side='right')
        config = ttk.Frame(page); config.pack(fill='x', pady=(0, 16))
        self.plan_name = tk.StringVar(); self.jobs_var = tk.StringVar(value='2'); self.timeout_var = tk.StringVar(value='120'); self.waves_var = tk.BooleanVar(value=True)
        for title, var, width in [('计划名称', self.plan_name, 23), ('并行任务', self.jobs_var, 5), ('仿真执行超时 / 秒', self.timeout_var, 7)]:
            ttk.Label(config, text=title).pack(side='left', padx=(0, 7)); ttk.Entry(config, textvariable=var, width=width).pack(side='left', padx=(0, 18))
        ttk.Checkbutton(config, text='生成 FSDB + VCD 波形', variable=self.waves_var).pack(side='left')
        ttk.Label(page, text='等待 License 不限时；仿真启动后才计算执行超时，填 0 表示执行也不限时。', style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        checks = ttk.Frame(page); checks.pack(fill='x', pady=(0, 8))
        self.check_modes = {'通用事务检查': 'transaction', '完整时序检查': 'strict'}
        self.check_mode_var = tk.StringVar(value='通用事务检查'); self.assert_fatal_var = tk.BooleanVar(value=True)
        self.env_fatal_var = tk.BooleanVar(value=True)
        ttk.Label(checks, text='验证范围').pack(side='left', padx=(0, 10))
        combo = ttk.Combobox(checks, textvariable=self.check_mode_var, values=list(self.check_modes), state='readonly', width=25)
        combo.pack(side='left', padx=(0, 20))
        combo.bind('<<ComboboxSelected>>', lambda e: self.update_check_note())
        ttk.Checkbutton(checks, text='环境错误立即停止（$fatal）', variable=self.env_fatal_var, command=self.update_check_note).pack(side='left', padx=(0, 14))
        self.assert_fatal_check = ttk.Checkbutton(checks, text='断言失败立即停止', variable=self.assert_fatal_var, command=self.update_check_note)
        self.assert_fatal_check.pack(side='left', padx=(0, 14))
        self.button(checks, '检查范围说明', self.check_help)
        self.check_note = tk.StringVar()
        ttk.Label(page, textvariable=self.check_note, style='Muted.TLabel', wraplength=1060).pack(anchor='w', pady=(0, 12))
        f, self.plan_tree = tree(page, [('enabled', '启用', 65), ('case', '用例', 260), ('runs', '运行次数', 100), ('seed', '起始 seed', 110), ('cycles', '周期数', 100)], height=11)
        f.pack(fill='both', expand=True); self.plan_tree.bind('<<TreeviewSelect>>', self.select_plan_row); self.plan_tree.bind('<Double-1>', self.toggle_plan)
        edit = ttk.LabelFrame(page, text='编辑选中用例', padding=14); edit.pack(fill='x', pady=(14, 10))
        self.row_enabled = tk.BooleanVar(); ttk.Checkbutton(edit, text='启用', variable=self.row_enabled).pack(side='left', padx=(0, 20))
        self.row_fields = {}
        for key, title in [('runs', '运行次数'), ('seed', '起始 seed'), ('cycles', '周期数')]:
            var = tk.StringVar(); self.row_fields[key] = var
            ttk.Label(edit, text=title).pack(side='left', padx=(0, 7)); ttk.Entry(edit, textvariable=var, width=10).pack(side='left', padx=(0, 18))
        self.button(edit, '应用到此用例', self.apply_plan_row)
        ttk.Label(page, text='运行次数 N 对应 seed = 起始值 … 起始值 + N − 1。关闭波形仍保留日志和覆盖率。双击表格可切换启用。', style='Muted.TLabel').pack(anchor='w')

    def load_plan(self):
        plan = self.settings.get('plan', model.default_plan())
        self.plan_name.set(plan.get('name', 'CMDBUF 功能回归')); self.jobs_var.set(str(plan.get('jobs', 2)))
        self.timeout_var.set(str(plan.get('timeout', 120))); self.waves_var.set(plan.get('waves', True))
        mode = plan.get('check_mode', 'strict')
        self.check_mode_var.set(next(title for title,value in self.check_modes.items() if value==mode))
        self.assert_fatal_var.set(plan.get('assert_fatal', True)); self.env_fatal_var.set(plan.get('env_fatal', True)); self.update_check_note()
        self.plan_rows = copy.deepcopy(plan['cases']); self.reconcile_plan()

    def reconcile_plan(self):
        known = {r['case']: r for r in self.plan_rows}
        self.plan_rows = [known.get(c['name'], dict(case=c['name'], enabled=False, runs=1, seed=1, cycles=c['cycles'])) for c in model.cases()]
        self.draw_plan()

    def draw_plan(self):
        old = self.plan_tree.selection(); self.plan_tree.delete(*self.plan_tree.get_children())
        for row in self.plan_rows:
            self.plan_tree.insert('', 'end', iid=row['case'], values=('✓' if row.get('enabled', True) else '—', row['case'], row['runs'], row['seed'], row['cycles']))
        self.plan_count.set('%d 个启用用例  /  %d 次运行'%(sum(r.get('enabled', True) for r in self.plan_rows), sum(r['runs'] for r in self.plan_rows if r.get('enabled', True))))
        if old and self.plan_tree.exists(old[0]): self.plan_tree.selection_set(old[0])
        elif self.plan_rows: self.plan_tree.selection_set(self.plan_rows[0]['case'])

    def select_plan_row(self, event=None):
        names = self.plan_tree.selection()
        if not names: return
        row = next(r for r in self.plan_rows if r['case'] == names[0]); self.row_enabled.set(row.get('enabled', True))
        for key, var in self.row_fields.items(): var.set(str(row[key]))

    def toggle_plan(self, event=None):
        names = self.plan_tree.selection()
        if names:
            row = next(r for r in self.plan_rows if r['case'] == names[0]); row['enabled'] = not row.get('enabled', True); self.draw_plan()

    def apply_plan_row(self):
        names = self.plan_tree.selection()
        if not names: raise ValueError('请先选择用例')
        new = dict(case=names[0], enabled=self.row_enabled.get(), **{k:int(v.get()) for k, v in self.row_fields.items()})
        # Validate values even when the row is disabled.
        model.check_plan(dict(version=1, jobs=1, timeout=120, waves=True, cases=[dict(new, enabled=True)]))
        self.plan_rows = [new if r['case'] == names[0] else r for r in self.plan_rows]; self.draw_plan()

    def plan_value(self):
        return dict(version=1, name=self.plan_name.get(), jobs=int(self.jobs_var.get()), timeout=int(self.timeout_var.get()), waves=self.waves_var.get(),
                    check_mode=self.check_modes[self.check_mode_var.get()], assert_fatal=self.assert_fatal_var.get(), env_fatal=self.env_fatal_var.get(), cases=copy.deepcopy(self.plan_rows))

    def update_check_note(self):
        self.assert_fatal_check.configure(state='normal' if self.env_fatal_var.get() else 'disabled')
        scope = ('通用模式按实际握手核对事务和 pending 寄存递推。精确调度由完整模式检查。'
                 if self.check_modes[self.check_mode_var.get()]=='transaction' else
                 '完整模式核对 pending 参考计数、容量预留、五项窗口和响应调度。')
        policy = ('环境错误立即停止；断言按右侧选项处理。' if self.env_fatal_var.get() else
                  '继续运行：环境检查与断言错误记录后继续，仍判 FAIL；断言停止选项暂不生效。')
        self.check_note.set(scope+' '+policy+' 无效时钟、文件/工具错误等无法继续执行的问题仍终止。')

    def check_help(self):
        self.preview('验证范围与断言处理', '''通用事务检查
以时钟沿前的完整四态快照检查真实握手。检查 ID、port、低地址、数据、错误优先级和单源顺序；MEM 输入仍按发射后 N+4 提供。
检查 pending 寄存递推。精确 ready/req/R 调度、参考待发数和最早 N+5 返回显示 NOT_CHECKED。
同地址发射保留所有可能事务对应关系。候选超过上限或结束时仍有未完成事务，显示 INCOMPLETE，不当作 PASS。

完整时序检查
核对 pending 寄存值与参考待发数，并检查容量预留、五项窗口、RR 和精确响应时序。

环境错误立即停止（总开关）
默认勾选：main_fail 等环境检查失败立即停止；断言按右侧选项处理。
取消：TC 输入约束、驱动时序/赋值检查、协议/事务断言错误均记录后继续执行剩余周期。断言停止选项暂不生效，重新勾选总开关后恢复原选择。
继续模式不修补输入、不删除检查；结果仍 FAIL，失败用例不合并到通过覆盖率。错误后的输出仅作诊断，不能据此认定 RTL 正确或错误。

断言失败立即停止（仅总开关打开时生效）
勾选：记录断言失败后 $fatal。取消：只关闭断言中止，环境检查仍立即停止。

无法继续执行的情况
无效时钟、未实现 main_phase、文件/程序格式错误、随机求解无解、驱动状态损坏、watchdog 和执行超时仍终止。
用户 TC 或 DUT 自己写的 $fatal/$stop/$finish 不由本开关拦截；无限等待也不能靠本开关保证结束。

报告与复现
结果页分别展示接口、事务、完整时序结论。check_frames.csv 保存原采样拍，assertions.jsonl 保存失败标签及原始拍号，check_scope.json 列出范围。
environment_errors.jsonl 保存环境错误，environment_runtime.json 记录是否执行到结尾。结果页区分“执行完成”和“验证通过”。
make replay 使用原用例保存的检查模式、环境 fatal 和断言 fatal 开关。通用模式通过不代表完整时序或 Formal 证明通过。''')

    def save_plan(self):
        if self.plan_tree.selection(): self.apply_plan_row()
        backend.save_plan(self.plan_value()); self.status.set('回归计划已保存')

    def run_regression(self):
        self.idle()
        if self.dirty or self.dut_value() != self.dut_baseline:
            raise ValueError('请先保存用例编辑器或 DUT 窗口中的修改，再开始回归')
        if self.plan_tree.selection(): self.apply_plan_row()
        self.launch('regress', dict(plan=self.plan_value()))
        self.show('results')

    def build_results(self, page):
        bar = self.bar(page)
        self.button(bar, '刷新结果', self.refresh_results)
        self.button(bar, '内置波形', self.open_run_wave, True)
        self.button(bar, 'Verdi 波形', self.open_verdi)
        self.button(bar, 'Verdi 覆盖率', self.open_coverage)
        self.button(bar, '覆盖率明细', self.open_coverage_details)
        self.button(bar, '导入 VCD / FSDB', self.open_wave_file)
        self.button(bar, '加入 Formal', self.add_run_formal)
        f, self.batch_tree = tree(page, [('id', '回归批次', 275), ('dut', 'DUT', 230), ('status', '状态', 80), ('runs', '通过 / 完成 / 总数', 150), ('wave', '波形', 70)], height=4)
        f.pack(fill='x'); self.batch_tree.bind('<<TreeviewSelect>>', lambda e: self.guard(self.select_batch))
        self.batch_tree.tag_configure('RUNNING', foreground=ACCENT)
        coverage_bar = ttk.Frame(page)
        coverage_bar.pack(fill='x', pady=7)
        self.coverage_note = tk.StringVar(value='选择批次查看代码覆盖率与用例')
        ttk.Label(coverage_bar, textvariable=self.coverage_note, style='Muted.TLabel', wraplength=700).pack(side='left')
        self.button(coverage_bar, '最小用例集', self.choose_coverage_batches, side='right')
        self.scope_note = tk.StringVar(value='每个用例单独列出接口、事务与时序结论')
        ttk.Label(page, textvariable=self.scope_note, style='Muted.TLabel', wraplength=1080).pack(anchor='w', pady=(0, 8))
        f, self.run_tree = tree(page, [('id', '用例 / seed', 280), ('status', '状态', 85), ('cycles', '主体周期', 80), ('seconds', '耗时 / 秒', 100), ('waves', '波形', 65)], height=5, selectmode='extended')
        f.pack(fill='x'); self.run_tree.bind('<<TreeviewSelect>>', lambda e: self.guard(self.read_artifact))
        row = self.bar(page); self.artifact_var = tk.StringVar(value='sim.log')
        ttk.Label(row, text='运行记录').pack(side='left', padx=(0, 10))
        combo = ttk.Combobox(row, textvariable=self.artifact_var, state='readonly', width=26,
                             values=['sim.log', 'sim.process.json', 'environment_errors.jsonl', 'environment_runtime.json', 'cmdbuf_rd_port_sva.sv', 'check_scope.json', 'assertions.jsonl', 'check_frames.csv', 'check_runtime.json',
                                     'clock_reset.csv', 'case.sv', 'assigned.csv', 'samples.csv', 'events.csv', 'drive_audit.json', 'constraint_failure.json'])
        combo.pack(side='left'); combo.bind('<<ComboboxSelected>>', lambda e: self.guard(self.read_artifact))
        self.result_code = CodeEditor(page, self.mono, readonly=True, height=8); self.result_code.pack(fill='both', expand=True)

    def refresh_results(self, preferred=None):
        rows = backend.history(include_interrupted=True)
        job = self.jobs.snapshot()
        if job and job.get('batch') and job['state'] == 'RUNNING':
            active = backend.progress_row(job['batch'])
            rows = [active] + [r for r in rows if r['id'] != active['id']]
        self.batch_tree.delete(*self.batch_tree.get_children())
        self.batch_rows.clear()
        for row in reversed(rows):
            self.draw_batch_row(row)
        self.home_metrics[1].set(str(len(rows))); self.home_metrics[2].set(rows[0]['status'] if rows else '未运行')
        if rows:
            pick = next(batch for batch in (preferred, self.current_batch, rows[0]['id']) if batch in self.batch_rows)
            self.batch_tree.selection_set(pick)
            self.batch_tree.see(pick)
            self.select_batch()
        else:
            self.clear_result_runs(None)
            self.coverage_note.set('选择批次查看代码覆盖率与用例')

    def draw_batch_row(self, row):
        batch, status = row['id'], row['status']
        label = {'RUNNING': '运行中', 'FINALIZING': '汇总中', 'STOPPING': '停止中',
                 'CANCELLED': '已中断', 'ERROR': '已中断'}.get(status, status)
        counts = '%d / %d / %d' % (row['passed'], row['completed'], row['total'])
        values = (batch, row['dut'], label, counts, '有' if row['waves'] else '无')
        tags = ({'ERROR': 'FAIL', 'CANCELLED': 'INCOMPLETE', 'STOPPING': 'INCOMPLETE',
                 'FINALIZING': 'RUNNING'}.get(status, status),)
        if self.batch_tree.exists(batch):
            self.batch_tree.item(batch, values=values, tags=tags)
        else:
            self.batch_tree.insert('', 0, iid=batch, values=values, tags=tags)
        self.batch_rows[batch] = row

    def refresh_live_results(self, job):
        batch = job['batch']
        previous = self.batch_rows.get(batch)
        if (previous and previous['completed'] >= job['completed']
                and (not job.get('finalizing') or previous['status'] != 'RUNNING')):
            return
        self.draw_batch_row(backend.progress_row(batch))
        if previous is None:
            self.batch_tree.selection_set(batch)
            self.batch_tree.see(batch)
            self.select_batch()
        elif self.current_batch == batch:
            self.select_batch()

    def clear_result_runs(self, batch):
        self.current_batch = batch
        self.final_results = None
        self.run_offset = 0; self.displayed_runs = 0
        self.run_tree.delete(*self.run_tree.get_children())
        self.scope_note.set('选择用例查看检查结果')
        self.result_code.set('')

    def append_run_row(self, row):
        self.run_tree.insert('', 'end', iid=row['id'], tags=(row['status'],),
                             values=(row['id'], row['status'], row['cycles'], row['seconds'], '有' if row['waves'] else '无'))

    def show_completed_runs(self, row):
        count = row['completed'] - self.displayed_runs
        if count:
            runs, self.run_offset = backend.completed_runs(row['id'], self.run_offset, count)
            for run in runs:
                self.append_run_row(run)
            self.displayed_runs = row['completed']
        if row['status'] == 'ERROR':
            coverage_state = '回归已中断 · 保留已完成用例'
        elif row['status'] == 'STOPPING':
            coverage_state = '正在合并已通过用例的覆盖率'
        elif row['status'] == 'FINALIZING' or row['completed'] == row['total']:
            coverage_state = '正在汇总结果与代码覆盖率'
        else:
            coverage_state = '代码覆盖率待合并'
        self.coverage_note.set('已完成 %d / %d   ·   %s' % (row['completed'], row['total'], coverage_state))
        if not row['completed']:
            self.scope_note.set('暂无已完成用例' if row['status'] == 'ERROR' else '等待首条用例完成')

    def select_batch(self):
        selected = self.batch_tree.selection()
        if not selected:
            return
        batch = selected[0]
        if batch not in self.batch_rows:
            return
        if self.current_batch != batch:
            self.clear_result_runs(batch)
        row = self.batch_rows[batch]
        if row['partial']:
            self.show_completed_runs(row)
        elif self.final_results is None:
            self.show_final_results(eda.read_json(backend.batch_path(batch)/'summary.json'))
        if not self.run_tree.selection():
            first = self.run_tree.get_children()[:1]
            if first:
                self.run_tree.selection_set(first)

    def show_final_results(self, summary):
        self.final_results = {result['id']: result for result in summary['results']}
        for result in summary['results']:
            if not self.run_tree.exists(result['id']):
                self.append_run_row(dict(id=result['id'], status=result['status'], seconds=result.get('seconds', '—'),
                                         cycles=result.get('metrics', {}).get('tc_body_cycles', '—'),
                                         waves=bool(result.get('wave_artifacts', result.get('waves_enabled', True)))))
        coverage = summary.get('coverage', {})
        metrics = coverage.get('metrics_percent', {})
        detail = '  ·  '.join(title+' '+str(metrics[key])+('%' if metrics[key] != '--' else '')
                              for key, title in [('LINE', '行'), ('COND', '条件'), ('TOGGLE', '翻转'), ('BRANCH', '分支'), ('FSM', '状态机')] if key in metrics)
        status = coverage.get('status', '—')
        if status == 'NO_PASSING_CASES':
            self.coverage_note.set('暂无通过用例可统计覆盖率')
        elif summary['status'] == 'CANCELLED' and status == 'PASS':
            self.coverage_note.set('已合并 %d 条 PASS 用例   ·   %s' % (len(coverage['included']), detail))
        else:
            self.coverage_note.set('覆盖率 '+status+'   '+detail)
        if not self.final_results:
            self.scope_note.set('暂无已完成用例')

    def selected_run(self):
        selected = self.run_tree.selection()
        if not self.current_batch or not selected: raise ValueError('请先选择回归批次与用例')
        return self.current_batch, selected[0]

    def read_artifact(self):
        if not self.run_tree.selection(): return
        batch, run = self.selected_run()
        folder = backend.batch_path(batch)/'cases'/backend.identifier(run)
        result = eda.read_json(folder/'result.json') if self.final_results is None else self.final_results[run]
        path = folder/self.artifact_var.get()
        checks = result.get('checks', {})
        if checks:
            labels = {'PASS':'通过', 'FAIL':'失败', 'INCOMPLETE':'未完成', 'NOT_CHECKED':'未检查'}
            checker = checks.get('checker', {})
            sva_note = ('  |  自定义 SVA '+checker['sha256'][:12]) if checker.get('customized') else ''
            self.scope_note.set('  ·  '.join(g['title']+'：'+labels.get(g['status'],g['status']) for g in checks['groups']) +
                                '  |  环境错误 %d 笔 · 断言失败 %d 笔  |  环境 fatal %s · 断言 fatal %s  |  执行%s'%(
                                    len(checks.get('environment', {}).get('failures', [])), len(checks['failures']),
                                    '开' if checks.get('env_fatal', True) else '关',
                                    '开' if checks.get('effective_assert_fatal', checks['assert_fatal']) else '关',
                                    ('完成' if result['completed'] else '中断') if 'completed' in result else '状态未记录') + sva_note)
        else: self.scope_note.set('历史结果未记录分项验证范围，保持原报告结论')
        if path.exists():
            with path.open(encoding='utf-8', errors='replace') as f: text = f.read(120000)
            if path.stat().st_size > 120000: text += '\n\n[预览截断；完整记录位于 '+str(path)+']'
        else: text = '此运行没有 '+path.name
        self.result_code.set(text)

    def open_run_wave(self):
        batch, run = self.selected_run(); folder = backend.result_path(batch, run)
        path = folder/'waves.vcd'
        if not path.exists(): path = folder/'waves.fsdb'
        if not path.exists(): raise ValueError('此运行没有波形，请开启“生成波形”后重新回归')
        self.launch('wave_index', dict(path=str(path)))

    def open_verdi(self):
        batch, run = self.selected_run(); self.launch('wave', dict(batch=batch, run=run))

    def open_coverage(self):
        if not self.current_batch: raise ValueError('请先选择回归批次')
        self.launch('coverage', dict(batch=self.current_batch))

    def open_coverage_details(self):
        if not self.current_batch:
            raise ValueError('请先选择回归批次')
        self.launch('coverage_details', dict(batches=[self.current_batch]))

    def choose_coverage_batches(self, selected=None):
        self.idle()
        selected = selected or ([self.current_batch] if self.current_batch else [])
        history = [row for row in backend.history() if row['coverage']]
        if not history:
            raise ValueError('请选择已合并覆盖率的历史回归')
        dialog = tk.Toplevel(self.root)
        dialog.title('计算最小用例集')
        dialog.geometry('820x515')
        dialog.transient(self.root)
        box = ttk.Frame(dialog, padding=18)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='选择历史回归', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(box, text='可多选同一覆盖率配置的批次，以合并覆盖率为目标。', style='Muted.TLabel').pack(anchor='w', pady=(5, 9))
        frame, batches = tree(box, [('batch', '回归批次', 300), ('dut', 'DUT', 160),
                                     ('count', '通过 / 完成 / 总数', 155)], height=7, selectmode='extended')
        frame.pack(fill='both', expand=True)
        for row in history:
            batches.insert('', 'end', iid=row['id'],
                           values=(row['id'], row['dut'], '{}/{}/{}'.format(row['passed'], row['completed'], row['total'])))
        for identifier in selected:
            if batches.exists(identifier):
                batches.selection_add(identifier)
        seconds = tk.StringVar(value='0')
        controls = ttk.Frame(box)
        controls.pack(fill='x', pady=(10, 0))
        ttk.Label(controls, text='求解秒数（0 不限时）').pack(side='left')
        ttk.Entry(controls, width=8, textvariable=seconds).pack(side='left', padx=8)
        def start():
            ids = list(batches.selection())
            if not ids or not seconds.get().isdigit():
                raise ValueError('请选择历史批次，并填写非负整数秒数')
            self.launch('coverage_minimize', dict(batches=ids, seconds=int(seconds.get())))
            dialog.destroy()
        self.button(controls, '开始计算', start, primary=True, side='right')
        dialog.grab_set()

    def show_coverage_record(self, path):
        identifier = Path(path).parent.name
        return CoverageWindow(self.root, path, self.choose_coverage_batches,
                              lambda: self.launch('coverage', dict(analysis=identifier)), self.guard, self.status,
                              self.coverage_busy, self.jobs.cancel_coverage,
                              lambda: self.launch('coverage_formal', dict(analysis=identifier,
                                                                          max_edges=int(self.max_edges.get()),
                                                                          format=self.selected_formal_format())),
                              self.formal_format_var)

    def open_wave_file(self):
        path = filedialog.askopenfilename(title='在内置查看器打开波形', filetypes=[('波形', '*.vcd *.fsdb')], parent=self.root)
        if path: self.launch('wave_index', dict(path=path))

    def build_formal(self, page):
        bar = self.bar(page)
        self.button(bar, '导入 FSDB', self.import_fsdb)
        self.button(bar, '选择回归波形', lambda: self.show('results'))
        self.button(bar, '解析选中层级', self.inspect_fsdb)
        self.button(bar, '端口映射', self.mapping_dialog)
        self.button(bar, '生成 Formal', self.generate_formal, True)
        f, self.formal_tree = tree(page, [('enabled', '启用', 55), ('name', 'FSDB / 用例', 210), ('pattern', 'pattern', 65), ('scope', 'DUT 实例路径', 255), ('source', '来源', 80)], height=5)
        f.pack(fill='x'); self.formal_tree.bind('<<TreeviewSelect>>', self.select_formal)
        row = self.bar(page); self.formal_enabled = tk.BooleanVar(); self.pattern_var = tk.StringVar(); self.scope_var = tk.StringVar(); self.max_edges = tk.StringVar(value='100000')
        ttk.Checkbutton(row, text='启用', variable=self.formal_enabled).pack(side='left', padx=(0, 12))
        for label, var, width in [('pattern', self.pattern_var, 7), ('实例', self.scope_var, 30)]:
            ttk.Label(row, text=label).pack(side='left', padx=(0, 5)); ttk.Entry(row, textvariable=var, width=width).pack(side='left', padx=(0, 10))
        self.button(row, '应用', self.apply_formal); self.button(row, '移除', self.remove_formal)
        self.button(row, 'Verdi', self.formal_verdi); self.button(row, '内置波形', self.formal_wave)
        row = self.bar(page)
        self.formal_format_var = tk.StringVar(value=formal_model.FORMAT_LABELS['cycle'])
        formal_format_field(row, self.formal_format_var, formal_model.FORMAT_LABELS)
        ttk.Label(row, text='采样沿上限').pack(side='left', padx=(0, 8)); ttk.Entry(row, textvariable=self.max_edges, width=12).pack(side='left')
        ttk.Label(row, text='   复位释放后逐上升沿提取；保留空拍、背压与多次复位区间', style='Muted.TLabel').pack(side='left')
        row = self.bar(page); self.export_var = tk.StringVar(); self.preview_var = tk.StringVar(value='trace_properties.sv')
        self.exports_combo = ttk.Combobox(row, textvariable=self.export_var, state='readonly', width=30); self.exports_combo.pack(side='left', padx=(0, 10))
        self.exports_combo.bind('<<ComboboxSelected>>', lambda e: self.guard(self.choose_export))
        combo = ttk.Combobox(row, textvariable=self.preview_var, state='readonly', width=24,
                             values=['trace_properties.sv', 'formal_top.sv', 'samples.csv', 'transactions.json', 'integration.json', 'run_bmc.sby', 'run_cover.sby', 'summary.json'])
        combo.pack(side='left', padx=(0, 10)); combo.bind('<<ComboboxSelected>>', lambda e: self.guard(self.formal_preview))
        self.button(row, '导出验证包…', self.save_export, True)
        self.formal_note = tk.StringVar(value='导入 FSDB 或从“结果与波形”加入回归用例。')
        ttk.Label(page, textvariable=self.formal_note, style='Muted.TLabel', wraplength=1050).pack(anchor='w', pady=(0, 10))
        self.formal_code = CodeEditor(page, self.mono, readonly=True, height=10); self.formal_code.pack(fill='both', expand=True)

    def restore_formal(self):
        state = formal_model.state(); saved = state.get('selection') or {}
        self.formal_items = [dict(s, enabled=s.get('enabled', True)) for s in saved.get('sources', [])]
        for source in self.formal_items:
            if source['kind'] == 'regression':
                try:
                    source.update(formal_model.regression_source(source))
                except (OSError, ValueError):
                    # Keep unavailable selections so the user can remove or recover them.
                    pass
        self.max_edges.set(str(saved.get('max_edges', 100000)))
        self.formal_format_var.set(formal_model.FORMAT_LABELS.get(saved.get('format'), formal_model.FORMAT_LABELS['cycle']))
        for meta in state['imports']:
            if not any(s.get('id') == meta['id'] for s in self.formal_items):
                self.add_formal_source(dict(kind='import', id=meta['id'], name=meta['name']), enabled=False)
        self.draw_formal(); self.refresh_exports()

    def add_formal_source(self, value, enabled=True):
        key = lambda s: (s['kind'], s.get('id'), s.get('batch'), s.get('run'))
        existing = next((s for s in self.formal_items if key(s) == key(value)), None)
        if existing is not None:
            if 'archive' in value:
                existing['archive'] = value['archive']
                existing['enabled'] = enabled
            return
        pattern = max([s['pattern'] for s in self.formal_items]+[-1])+1
        self.formal_items.append(dict(value, enabled=enabled, pattern=pattern, scope='tb_cmdbuf_rd.dut', mapping={}))

    def add_run_formal(self):
        if not self.current_batch or not self.run_tree.selection(): raise ValueError('请选择带波形的回归用例')
        if self.batch_rows[self.current_batch]['partial']:
            raise ValueError('回归汇总生成后可将用例加入 Formal')
        sources = []
        for run in self.run_tree.selection():
            source = dict(kind='regression', batch=self.current_batch, run=run, name=run)
            sources.append(formal_model.regression_source(source))
        for source in sources:
            self.add_formal_source(source)
        self.draw_formal(); self.show('formal')

    def draw_formal(self):
        old = self.formal_tree.selection(); self.formal_tree.delete(*self.formal_tree.get_children())
        for i, s in enumerate(self.formal_items):
            self.formal_tree.insert('', 'end', iid=str(i), values=('✓' if s.get('enabled', True) else '—', s['name'], s['pattern'], s['scope'], '导入' if s['kind'] == 'import' else '回归'))
        if self.formal_items: self.formal_tree.selection_set(old[0] if old and self.formal_tree.exists(old[0]) else '0')

    def formal_selected(self):
        selected = self.formal_tree.selection()
        if not selected: raise ValueError('请先选择一个 FSDB')
        return self.formal_items[int(selected[0])]

    def select_formal(self, event=None):
        if not self.formal_tree.selection(): return
        s = self.formal_selected(); self.formal_enabled.set(s.get('enabled', True)); self.pattern_var.set(str(s['pattern'])); self.scope_var.set(s['scope'])

    def apply_formal(self):
        self.idle(); s = self.formal_selected(); pattern = int(self.pattern_var.get()); scope = self.scope_var.get().strip()
        formal_model.core.number(pattern, 'pattern'); formal_model.core.mapping(scope)
        if any(x is not s and x['pattern'] == pattern for x in self.formal_items): raise ValueError('pattern 编号不能重复')
        if scope != s['scope']: s['mapping'] = {}
        s.update(pattern=pattern, scope=scope, enabled=self.formal_enabled.get()); self.draw_formal()

    def remove_formal(self):
        self.idle(); s = self.formal_selected(); self.formal_items.remove(s); self.draw_formal()

    def import_fsdb(self):
        self.idle(); paths = filedialog.askopenfilenames(title='导入 FSDB 波形', filetypes=[('FSDB', '*.fsdb')], parent=self.root)
        if paths: self.launch('import_fsdb', dict(paths=list(paths)))

    def inspect_fsdb(self):
        self.apply_formal()
        self.launch('fsdb_inspect', dict(sources=[self.formal_selected()], max_edges=int(self.max_edges.get())))

    def generate_formal(self):
        if self.formal_tree.selection(): self.apply_formal()
        self.launch('fsdb_formal', dict(sources=[s for s in self.formal_items if s.get('enabled', True)],
                                        max_edges=int(self.max_edges.get()), format=self.selected_formal_format()))

    def selected_formal_format(self):
        return next((key for key, label in formal_model.FORMAT_LABELS.items()
                     if label == self.formal_format_var.get()), 'cycle')

    def formal_source_path(self):
        return formal_model.source_path(self.formal_selected())

    def formal_verdi(self):
        self.apply_formal()
        self.launch('wave_file', dict(path=str(self.formal_source_path()), scope=self.formal_selected()['scope']))

    def formal_wave(self):
        self.launch('wave_index', dict(path=str(self.formal_source_path())))

    def mapping_dialog(self):
        s = self.formal_selected()
        dialog = tk.Toplevel(self.root); dialog.title('端口映射 · '+s['name']); dialog.geometry('960x720'); dialog.transient(self.root)
        box = ttk.Frame(dialog, padding=18); box.pack(fill='both', expand=True)
        ttk.Label(box, text='左侧为环境端口，右侧为 FSDB 完整路径。选中行，在下方修改。', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        f, ports = tree(box, [('name', '环境端口', 180), ('use', '用途', 100), ('width', '位宽', 60), ('path', 'FSDB 路径', 420)], height=12)
        f.pack(fill='both', expand=True)
        mapping = copy.deepcopy(s.get('mapping', {})); selected_path = tk.StringVar()
        for signal in formal_model.core.SIGNALS:
            name = signal['name']; usage = '采样时钟' if name == 'clk' else ('assume' if signal['direction'] == 'input' else 'assert')
            ports.insert('', 'end', iid=name, values=(name, usage, signal['width'], mapping.get(name, s['scope']+'.'+name)))
        def choose(event=None):
            if ports.selection(): selected_path.set(ports.item(ports.selection()[0], 'values')[3])
        ports.bind('<<TreeviewSelect>>', choose)
        row = ttk.Frame(box); row.pack(fill='x', pady=12); ttk.Entry(row, textvariable=selected_path).pack(side='left', fill='x', expand=True, padx=(0, 10))
        def apply():
            if not ports.selection(): raise ValueError('请先选择端口')
            name = ports.selection()[0]; new = dict(mapping, **{name:selected_path.get().strip()})
            formal_model.core.mapping(s['scope'], new); mapping.update(new)
            values = list(ports.item(name, 'values')); values[3] = mapping[name]; ports.item(name, values=values)
        self.button(row, '应用路径', apply)
        def save():
            self.idle(); s['mapping'] = mapping; self.status.set('端口映射已保存到当前 FSDB 选择'); dialog.destroy()
        self.button(box, '保存映射', save, True, side='right'); dialog.grab_set()

    def refresh_exports(self, selected=None):
        exports = formal_model.state()['exports']; ids = [s['id'] for s in exports]
        self.exports_combo.configure(values=ids)
        selected = selected or self.formal_export or (ids[0] if ids else None)
        if selected in ids:
            self.export_var.set(selected); self.choose_export()

    def choose_export(self):
        self.formal_export = self.export_var.get()
        summary = eda.read_json(formal_model.artifact(self.formal_export, 'summary.json')); c = summary['counts']
        origin = '选集 ' + summary['coverage_set'] + '  ·  ' if summary.get('coverage_set') else ''
        style = formal_model.FORMAT_LABELS.get(summary.get('format', 'cycle'), summary.get('format', ''))
        counts = '%d patterns  ·  %d cycles  ·  %d assume  ·  %d assert  ·  %d cover' % (
            c['patterns'], c['cycles'], c['input_assumptions'], c['output_assertions'], c['terminal_covers'])
        self.formal_note.set(origin + style + '  ·  ' + counts + '  |  验证包已生成 · Formal 证明待运行')
        self.formal_preview()

    def formal_preview(self):
        if not self.formal_export: return
        path = formal_model.artifact(self.formal_export, self.preview_var.get())
        with path.open(encoding='utf-8', errors='replace') as f: text = f.read(120000)
        if path.stat().st_size > 120000: text += '\n\n[仅预览前 120 KB；导出包保留完整内容]'
        self.formal_code.set(text)

    def save_export(self):
        if not self.formal_export: raise ValueError('请先生成或选择已有 Formal 验证包')
        path = filedialog.asksaveasfilename(title='保存 Formal 验证包', defaultextension='.zip', initialfile='formal_'+self.formal_export+'.zip', filetypes=[('ZIP', '*.zip')], parent=self.root)
        if path:
            source = formal_model.artifact(self.formal_export, 'package.zip')
            if Path(path).resolve() != source.resolve(): shutil.copyfile(str(source), path)
            self.status.set('验证包已保存：'+path)

    def build_settings(self, page):
        self.notice(page, '自动检测，无需填写路径', 'Studio 从自身文件定位工程，继承启动终端的 PATH、许可证和图形会话，自动查找 VCS、Verdi、URG 与 PLI / NPI。普通使用直接启动即可。')
        bar = self.bar(page)
        self.button(bar, '重新检测', lambda: self.launch('detect'), True)
        self.button(bar, '检查工具链', self.doctor)
        self.button(bar, '高级选项…', self.advanced_settings)
        self.tool_profile = tk.StringVar(value='正在自动识别当前设备…')
        ttk.Label(bar, textvariable=self.tool_profile, style='Muted.TLabel').pack(side='right')
        f, self.tools_tree = tree(page, [('name', '项目', 170), ('status', '检测结果', 115), ('detail', '本机位置 / 说明', 780)], height=14)
        f.pack(fill='both', expand=True)
        self.tool_note = tk.StringVar(value='自动识别过程不会修改系统环境，也不会显示许可证内容。')
        ttk.Label(page, textvariable=self.tool_note, style='Muted.TLabel', wraplength=1000).pack(anchor='w', pady=14)
        ttk.Label(page, text='make env-info 显示自动检测结果；make doctor 检查组件；make smoke 进行真实编译、仿真和覆盖率合并。\n'
                  '特殊站点可在高级选项中临时或显式覆盖路径。默认自动模式不使用旧 VM 配置。',
                  style='Muted.TLabel', wraplength=1000).pack(anchor='w', pady=8)

    def auto_detect(self):
        if self.jobs.snapshot() and self.jobs.snapshot()['state'] == 'RUNNING':
            self.detect_timer = self.root.after(300, self.auto_detect)
        else:
            self.guard(lambda: self.launch('detect'))

    def show_detected(self, data):
        self.tools_tree.delete(*self.tools_tree.get_children())
        for row in data['rows']:
            self.tools_tree.insert('', 'end', values=(row['name'], row['status'], row['detail']),
                                   tags=('FAIL',) if row['status'] in ('未发现', '未就绪') else ())
        self.tool_profile.set('自动检测' if data['profile'] == 'auto' else '高级覆盖已启用')
        if data.get('legacy_config_ignored'):
            self.tool_note.set('已忽略旧版本或其他设备的配置，当前使用本机自动检测。')
        else:
            self.tool_note.set('已从当前设备识别以上信息；许可证环境由本机继承，不显示其内容。')

    def advanced_settings(self):
        self.idle(); current = eda.platform_config.load()
        dialog = tk.Toplevel(self.root); dialog.title('高级选项 · 特殊站点覆盖'); dialog.geometry('790x620'); dialog.transient(self.root)
        box = ttk.Frame(dialog, padding=22); box.pack(fill='both', expand=True)
        ttk.Label(box, text='通常无需修改。只有当前终端与登录环境找不到 EDA 工具时，才需要这些覆盖。',
                  style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=(0, 15))
        enabled = tk.BooleanVar(value=current['profile'] == 'manual')
        ttk.Checkbutton(box, text='在当前设备启用手动覆盖', variable=enabled).pack(anchor='w', pady=(0, 12))
        grid = ttk.Frame(box); grid.pack(fill='x'); grid.columnconfigure(1, weight=1)
        variables = {}; controls = []
        for i, (key, title) in enumerate([('tool_setup', '可选环境脚本'), ('vcs_home', 'VCS 安装目录'),
                                         ('verdi_home', 'Verdi 安装目录'), ('display', '图形会话 DISPLAY'), ('xauthority', '图形授权文件')]):
            var = tk.StringVar(value=current.get(key, '')); variables[key] = var
            ttk.Label(grid, text=title).grid(row=i, column=0, sticky='w', padx=(0, 16), pady=10)
            entry = ttk.Entry(grid, textvariable=var); entry.grid(row=i, column=1, sticky='ew', pady=10); controls.append(entry)
            if key != 'display':
                def browse(k=key, v=var):
                    value = filedialog.askdirectory(parent=dialog) if k in ('vcs_home', 'verdi_home') else filedialog.askopenfilename(parent=dialog)
                    if value: v.set(value)
                button = ttk.Button(grid, text='选择…', command=browse); button.grid(row=i, column=2, padx=(8, 0)); controls.append(button)
        def update(*args):
            for control in controls: control.configure(state='normal' if enabled.get() else 'disabled')
        enabled.trace_add('write', update); update()
        ttk.Label(box, text='覆盖仅绑定当前设备，写入已忽略的 eda.local.json。\n取消勾选并保存，将恢复自动检测；环境变量中的显式设置继续有效。',
                  style='Muted.TLabel', wraplength=700).pack(anchor='w', pady=18)
        def save():
            self.idle()
            value = {k:v.get() for k,v in variables.items()} if enabled.get() else {}
            value['profile'] = 'manual' if enabled.get() else 'auto'
            eda.platform_config.save(value); self.settings = model.state_read(); dialog.destroy(); self.launch('detect')
        ttk.Button(box, text='保存并重新检测', style='Primary.TButton', command=lambda: self.guard(save)).pack(anchor='e')
        dialog.grab_set()

    def doctor(self):
        self.launch('doctor')

    def launch(self, kind, payload=None):
        if kind in ('compile', 'regress'):
            self.require_rtl_import_idle()
        if kind in ('compile', 'regress') and self.sva_dirty():
            raise ValueError('请先保存端口 SVA 编辑器中的修改')
        job = self.jobs.start(kind, payload); self.status.set('任务启动：'+kind); self.progress.start(12)
        return job

    def poll(self):
        job = self.jobs.snapshot()
        analyzing = bool(job and job['state'] == 'RUNNING' and job['kind'] in backend.COVERAGE_JOBS)
        regressing = bool(job and job['state'] == 'RUNNING' and job['kind'] == 'regress'
                          and not job.get('finalizing'))
        if regressing:
            if not self.stop_regression_button.winfo_manager():
                self.stop_regression_button.pack(side='right', padx=6)
            self.stop_regression_button.configure(state='disabled' if job.get('stop_requested') else 'normal')
        elif self.stop_regression_button.winfo_manager():
            self.stop_regression_button.pack_forget()
        if self.coverage_busy.get() != analyzing:
            self.coverage_busy.set(analyzing)
        if analyzing and not self.cancel_coverage_button.winfo_manager():
            self.cancel_coverage_button.pack(side='right', padx=6)
        elif not analyzing and self.cancel_coverage_button.winfo_manager():
            self.cancel_coverage_button.pack_forget()
        if job:
            total, completed = job.get('total', 0), job.get('completed', 0)
            if job['state'] == 'RUNNING':
                if job.get('batch'):
                    self.guard(lambda: self.refresh_live_results(job))
                self.status.set(job['stage']+('  ·  %d / %d'%(completed, total) if total else '')+'  ·  %ds'%int(time.time()-job['started']))
                if total:
                    self.progress.stop(); self.progress.configure(mode='determinate', maximum=total, value=completed)
                else:
                    if str(self.progress['mode']) != 'indeterminate': self.progress.configure(mode='indeterminate'); self.progress.start(12)
            elif job['id'] != self.last_finished:
                self.last_finished = job['id']; self.progress.stop()
                if job['kind'] == 'regress' and job['state'] == 'CANCELLED' and total:
                    self.progress.configure(mode='determinate', maximum=total, value=completed)
                else:
                    self.progress.configure(mode='determinate', maximum=1, value=1 if job['state'] == 'DONE' else 0)
                finished = {'coverage_details': '覆盖率明细已就绪', 'coverage_minimize': '用例集覆盖率验证完成',
                            'coverage_formal': '选集 Formal 验证包已生成'}
                self.status.set(job.get('error') or (job['stage'] if job['state'] == 'CANCELLED'
                                                   else finished.get(job['kind'], '已完成：'+job['kind'])))
                self.on_finished(job)
            if self.job_window and self.job_window.winfo_exists():
                if self.job_log.get() != job['log']:
                    self.job_log.set(job['log']); self.job_log.text.see('end')
        if self.rtl_import is not None:
            imported = self.rtl_import.snapshot()
            if imported and imported['state'] != 'RUNNING':
                self.rtl_import = None
                if imported['state'] == 'DONE':
                    data = imported['imported']
                    self.text_set(self.dut_files, '\n'.join(data['files']))
                    self.text_set(self.dut_includes, '\n'.join(data['include_dirs']))
                    self.status.set('RTL 已导入，请检查编译顺序、例化代码并保存 DUT')
                else:
                    self.error(imported['error'])
        self.poll_id = self.root.after(250, self.poll)

    def on_finished(self, job):
        if job.get('batch'):
            self.guard(lambda: self.finish_regression_view(job['batch']))
        if job['state'] == 'CANCELLED':
            return
        if job['state'] != 'DONE':
            if job['kind'] != 'regress' or self.page == 'results':
                self.show_log()
            return
        kind = job['kind']
        if kind == 'detect':
            self.show_detected(job['detected'])
        elif kind == 'wave_index':
            WaveWindow(self.root, job['database'], job['wave_name'])
        elif kind in ('coverage_details', 'coverage_minimize'):
            self.guard(lambda: self.show_coverage_record(job['coverage_record']))
        elif kind == 'import_fsdb':
            for meta in job['imported']: self.add_formal_source(dict(kind='import', id=meta['id'], name=meta['name']))
            self.draw_formal(); self.show('formal')
        elif kind == 'fsdb_inspect':
            info = eda.read_json(formal_model.artifact(job['formal_id'], 'inspection.json'))
            self.preview('NPI 层级与信号', json.dumps(info, ensure_ascii=False, indent=2))
        elif kind in ('fsdb_formal', 'coverage_formal'):
            self.refresh_exports(job['formal_id']); self.show('formal')
            self.root.lift()

    def finish_regression_view(self, batch):
        if batch not in self.batch_rows:
            self.refresh_results(batch)
        else:
            path = backend.batch_path(batch)/'summary.json'
            if path.is_file():
                summary = eda.read_json(path)
                self.draw_batch_row(backend.summary_row(summary))
                if self.current_batch == batch:
                    self.show_final_results(summary)
                    self.select_batch()
            else:
                self.draw_batch_row(backend.progress_row(batch))
                if self.current_batch == batch:
                    self.select_batch()
            self.home_metrics[1].set(str(len(self.batch_rows)))
            self.home_metrics[2].set(self.batch_rows[batch]['status'])

    def show_log(self):
        if self.job_window and self.job_window.winfo_exists(): self.job_window.lift(); return
        self.job_window = tk.Toplevel(self.root); self.job_window.title('Make 任务日志'); self.job_window.geometry('1050x660')
        self.job_log = CodeEditor(self.job_window, self.mono, readonly=True); self.job_log.pack(fill='both', expand=True, padx=12, pady=12)
        job = self.jobs.snapshot(); self.job_log.set(job['log'] if job else '尚未运行任务。')

    def preview(self, title, text):
        window = tk.Toplevel(self.root); window.title(title); window.geometry('1000x720')
        editor = CodeEditor(window, self.mono, readonly=True); editor.pack(fill='both', expand=True, padx=14, pady=14); editor.set(text)
        return window

    def save_active(self):
        if self.page == 'cases': self.save_case()
        elif self.page == 'dut': self.save_dut()
        elif self.page == 'regression': self.save_plan()
        elif self.page == 'settings': self.launch('detect')

    def close(self):
        try:
            self.idle()
            if not self.allow_case_change() or not self.close_sva(): return
            if self.dut_value() != self.dut_baseline and not messagebox.askyesno('未保存的 DUT', 'DUT 修改尚未保存。仍然关闭窗口？', parent=self.root): return
            # Preserve a draft even if no cases are enabled. Validation occurs on run.
            try: eda.write_json(STATE/'settings.json', dict(plan=self.plan_value()))
            except ValueError: pass
            eda.write_json(STATE/'formal_selection.json', dict(sources=self.formal_items, max_edges=int(self.max_edges.get()),
                                                              format=self.selected_formal_format()))
            self.root.after_cancel(self.poll_id); self.root.after_cancel(self.detect_timer); self.root.destroy()
        except (ValueError, RuntimeError) as exc:
            self.error(str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page', choices=['home', 'dut', 'cases', 'regression', 'results', 'formal', 'settings'], default='home')
    parser.add_argument('--batch')
    parser.add_argument('--coverage-record')
    args = parser.parse_args(argv)
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print('无法创建 Tk 窗口：'+str(exc)+'\n请在 Linux 图形桌面终端运行 make gui，并检查 DISPLAY；不需要浏览器。', file=sys.stderr)
        return 2
    app = Studio(root); app.show(args.page)
    if args.batch:
        batch = args.batch
        if batch == 'latest' and backend.history(): batch = backend.history()[0]['id']
        app.refresh_results(batch)
    if args.coverage_record:
        app.show('results')
        app.guard(lambda: app.show_coverage_record(args.coverage_record))
    root.mainloop(); return 0


if __name__ == '__main__':
    sys.exit(main())
