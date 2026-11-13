"""Native Tk editors and a four-state waveform canvas (no web renderer)."""
import re
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
import vcd

BG = '#f3f5f9'
INK = '#17243a'
MUTED = '#687890'
ACCENT = '#3868ed'
DARK = '#111d31'


def fonts(root):
    names = set(tkfont.families(root))
    ui = next((n for n in ('Microsoft YaHei UI', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans') if n in names), 'TkDefaultFont')
    mono = next((n for n in ('Cascadia Code', 'Consolas', 'DejaVu Sans Mono') if n in names), 'TkFixedFont')
    return ui, mono


def style(root):
    ui, mono = fonts(root)
    s = ttk.Style(root); s.theme_use('clam')
    s.configure('.', font=(ui, 10), foreground=INK, background=BG)
    s.configure('TFrame', background=BG)
    s.configure('Card.TFrame', background='white')
    s.configure('TLabel', background=BG)
    s.configure('Card.TLabel', background='white')
    s.configure('Muted.TLabel', foreground=MUTED)
    s.configure('Title.TLabel', font=(ui, 22, 'bold'))
    s.configure('Heading.TLabel', font=(ui, 13, 'bold'))
    s.configure('TButton', padding=(14, 8), borderwidth=1, background='white', bordercolor='#d6deea')
    s.map('TButton', background=[('active', '#e9efff'), ('disabled', '#eef1f6')], foreground=[('disabled', '#95a2b7')])
    s.configure('Primary.TButton', background=ACCENT, foreground='white', bordercolor=ACCENT)
    s.map('Primary.TButton', background=[('active', '#2753ce'), ('disabled', '#a8b9e9')], foreground=[('disabled', '#f3f5fb')])
    s.configure('TEntry', padding=6, fieldbackground='white', bordercolor='#d6deea')
    s.configure('TCombobox', padding=5, fieldbackground='white')
    s.configure('Treeview', background='white', fieldbackground='white', rowheight=33, borderwidth=0)
    s.configure('Treeview.Heading', background='#eaf0f8', font=(ui, 10, 'bold'), padding=(8, 8), relief='flat')
    s.map('Treeview', background=[('selected', '#e4ecff')], foreground=[('selected', '#254fbc')])
    s.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#e1e8f4', borderwidth=0)
    s.configure('TLabelframe', bordercolor='#d8e1ed', padding=10)
    s.configure('TLabelframe.Label', font=(ui, 10, 'bold'))
    s.configure('TNotebook', borderwidth=0)
    s.configure('TNotebook.Tab', padding=(18, 9))
    return ui, mono


def tree(parent, columns, height=7, selectmode='browse'):
    frame = ttk.Frame(parent)
    widget = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', height=height, selectmode=selectmode)
    for key, label, width in columns:
        widget.heading(key, text=label); widget.column(key, width=width, minwidth=55, stretch=True)
    y = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
    x = ttk.Scrollbar(frame, orient='horizontal', command=widget.xview)
    widget.configure(yscrollcommand=y.set, xscrollcommand=x.set)
    widget.grid(row=0, column=0, sticky='nsew'); y.grid(row=0, column=1, sticky='ns'); x.grid(row=1, column=0, sticky='ew')
    frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
    widget.tag_configure('PASS', foreground='#087d69'); widget.tag_configure('FAIL', foreground='#bc3d4f')
    widget.tag_configure('INCOMPLETE', foreground='#9b6b10'); widget.tag_configure('TIMEOUT', foreground='#9b6b10')
    return frame, widget


def formal_format_field(parent, value, labels):
    ttk.Label(parent, text='Formal 写法').pack(side='left', padx=(0, 8))
    picker = ttk.Combobox(parent, textvariable=value, values=list(labels.values()), state='readonly',
                          width=21, height=len(labels))
    picker.pack(side='left', padx=(0, 16))
    return picker


def multiline(parent, mono, height):
    frame = ttk.Frame(parent)
    text = tk.Text(frame, width=34, height=height, wrap='none', font=(mono, 10), relief='flat', padx=10, pady=8, undo=True)
    x = ttk.Scrollbar(frame, orient='horizontal', command=text.xview)
    y = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
    text.configure(xscrollcommand=x.set, yscrollcommand=y.set)
    text.grid(row=0, column=0, sticky='nsew'); y.grid(row=0, column=1, sticky='ns'); x.grid(row=1, column=0, sticky='ew')
    frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
    return frame, text


class ScrollPage(ttk.Frame):
    """Keep controls reachable on shorter laptop displays, while editors expand."""
    def __init__(self, parent):
        ttk.Frame.__init__(self, parent)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        bar.pack(side='right', fill='y'); self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=bar.set, yscrollincrement=24)
        self.body = ttk.Frame(self.canvas, padding=(26, 22))
        self.window = self.canvas.create_window(0, 0, anchor='nw', window=self.body)
        self.canvas.bind('<Configure>', self.resize); self.body.bind('<Configure>', self.resize)
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(int(-e.delta/120), 'units'))
        self.canvas.bind('<Button-4>', lambda e: self.canvas.yview_scroll(-1, 'units'))
        self.canvas.bind('<Button-5>', lambda e: self.canvas.yview_scroll(1, 'units'))

    def resize(self, event=None):
        w = self.canvas.winfo_width(); h = max(self.canvas.winfo_height(), self.body.winfo_reqheight())
        self.canvas.itemconfigure(self.window, width=w, height=h)
        self.canvas.configure(scrollregion=(0, 0, w, h))


class CodeEditor(ttk.Frame):
    def __init__(self, parent, mono='TkFixedFont', readonly=False, height=15, on_change=None):
        ttk.Frame.__init__(self, parent)
        self.readonly = readonly; self.on_change = on_change; self.pending = None
        self.gutter = tk.Canvas(self, width=50, background='#17243a', highlightthickness=0)
        self.text = tk.Text(self, wrap='none', undo=True, maxundo=200, font=(mono, 11), height=height,
                            background='#101c30', foreground='#dbe5f5', insertbackground='white',
                            selectbackground='#36507a', relief='flat', padx=12, pady=10,
                            spacing1=2, spacing3=2, tabs=('4c',))
        y = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        x = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview)
        def yscroll(a, b):
            y.set(a, b); self.redraw_gutter()
        self.text.configure(yscrollcommand=yscroll, xscrollcommand=x.set)
        self.gutter.grid(row=0, column=0, sticky='ns'); self.text.grid(row=0, column=1, sticky='nsew')
        y.grid(row=0, column=2, sticky='ns'); x.grid(row=1, column=1, sticky='ew')
        self.rowconfigure(0, weight=1); self.columnconfigure(1, weight=1)
        for tag, color in [('keyword', '#b4a1ff'), ('number', '#f1c57a'), ('comment', '#7695a5'), ('string', '#80d7b7')]:
            self.text.tag_configure(tag, foreground=color)
        self.text.bind('<<Modified>>', self.changed)
        self.text.bind('<Configure>', lambda event: self.redraw_gutter())
        self.text.bind('<Tab>', self.indent)
        if readonly:
            self.text.configure(state='disabled')

    def indent(self, event):
        if not self.readonly:
            self.text.insert('insert', '    ')
        return 'break'

    def get(self):
        return self.text.get('1.0', 'end-1c')

    def set(self, value):
        self.text.configure(state='normal'); self.text.delete('1.0', 'end'); self.text.insert('1.0', value)
        self.text.edit_reset(); self.text.edit_modified(False)
        if self.readonly:
            self.text.configure(state='disabled')
        self.highlight(); self.redraw_gutter()

    def changed(self, event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(250, self.highlight)
        if self.on_change:
            self.on_change()

    def highlight(self):
        self.pending = None
        code = self.get()
        for tag in ('keyword', 'number', 'comment', 'string'):
            self.text.tag_remove(tag, '1.0', 'end')
        patterns = [('keyword', r'\b(?:virtual|task|endtask|for|int|unsigned|begin|end|if|else|logic|case|endcase|posedge|negedge|assert|assume|property|disable|iff|module|endmodule|input|output|wire|always_ff)\b'),
                    ('number', r"\b\d+(?:'[sS]?[bBdDhHoO][0-9a-fA-F_xXzZ]+)?\b"),
                    ('string', r'"(?:\\.|[^"\\])*"'), ('comment', r'//[^\n]*|/\*[\s\S]*?\*/')]
        for tag, pattern in patterns:
            for m in re.finditer(pattern, code):
                self.text.tag_add(tag, '1.0+%dc'%m.start(), '1.0+%dc'%m.end())
        self.redraw_gutter()

    def redraw_gutter(self):
        self.gutter.delete('all')
        index = self.text.index('@0,0')
        while True:
            box = self.text.dlineinfo(index)
            if box is None:
                break
            self.gutter.create_text(39, box[1]+2, text=index.split('.')[0], anchor='ne', fill='#778ca8', font=('TkFixedFont', 10))
            index = self.text.index(index+'+1line')


class WaveWindow(tk.Toplevel):
    def __init__(self, parent, database, name):
        tk.Toplevel.__init__(self, parent)
        self.title(name+' · CMDBUF 波形'); self.geometry('1340x820'); self.minsize(950, 600)
        self.database = database; self.meta = vcd.describe(database); self.start = 0; self.end = max(1, self.meta['end'])
        self.ids = []; self.cursor = 0; self.cursor_b = None
        self.configure(background=BG)
        bar = ttk.Frame(self, padding=16); bar.pack(fill='x')
        ttk.Label(bar, text='波形查看器', style='Heading.TLabel').pack(side='left')
        ttk.Label(bar, text='  '+name+'  ·  '+self.meta['timescale']+' / tick', style='Muted.TLabel').pack(side='left')
        self.format = tk.StringVar(value='hex')
        combo = ttk.Combobox(bar, textvariable=self.format, values=['hex', 'bin', 'unsigned'], width=10, state='readonly')
        combo.pack(side='right'); combo.bind('<<ComboboxSelected>>', lambda e: self.draw())
        pane = ttk.Panedwindow(self, orient='horizontal'); pane.pack(fill='both', expand=True, padx=16)
        left = ttk.Frame(pane); right = ttk.Frame(pane); pane.add(left, weight=1); pane.add(right, weight=4)
        self.filter = tk.StringVar()
        ttk.Label(left, text='信号库 · 输入名称过滤').pack(anchor='w', pady=(0, 6))
        ttk.Entry(left, textvariable=self.filter).pack(fill='x', pady=(0, 8))
        self.filter.trace_add('write', lambda *args: self.populate())
        frame, self.signals = tree(left, [('name', '信号路径', 260), ('width', '位宽', 50)], selectmode='extended')
        frame.pack(fill='both', expand=True)
        ttk.Button(left, text='添加选中信号', command=self.add_selected).pack(fill='x', pady=8)
        ttk.Button(left, text='清空显示', command=self.clear).pack(fill='x')
        self.signals.bind('<Double-1>', lambda event: self.add_selected())
        controls = ttk.Frame(right); controls.pack(fill='x', pady=(0, 8))
        for title, action in [('全览', self.fit), ('放大 +', lambda: self.zoom(.5)), ('缩小 −', lambda: self.zoom(2)), ('←', lambda: self.pan(-.5)), ('→', lambda: self.pan(.5))]:
            ttk.Button(controls, text=title, command=action).pack(side='left', padx=(0, 4))
        self.from_var = tk.StringVar(value='0'); self.to_var = tk.StringVar(value=str(self.end))
        ttk.Entry(controls, textvariable=self.from_var, width=10).pack(side='left', padx=3)
        ttk.Label(controls, text='→').pack(side='left')
        ttk.Entry(controls, textvariable=self.to_var, width=10).pack(side='left', padx=3)
        ttk.Button(controls, text='定位', command=self.locate).pack(side='left')
        self.canvas = tk.Canvas(right, background='#101c30', highlightthickness=0)
        scroll = ttk.Scrollbar(right, command=self.canvas.yview)
        scroll.pack(side='right', fill='y'); self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<Button-1>', self.point); self.canvas.bind('<Shift-Button-1>', lambda e: self.point(e, True))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(int(-e.delta/120), 'units'))
        self.canvas.bind('<Button-4>', lambda e: self.canvas.yview_scroll(-1, 'units'))
        self.canvas.bind('<Button-5>', lambda e: self.canvas.yview_scroll(1, 'units'))
        self.note = tk.StringVar(value='单击设置游标 A；Shift + 单击设置 B；数值保留 X / Z。')
        ttk.Label(self, textvariable=self.note, style='Muted.TLabel', padding=16).pack(fill='x')
        self.populate()
        wanted = {'clk', 'rst_n', 'arvalid', 'arready', 'araddr', 'src', 'rvalid', 'rready', 'rdata', 'req', 'arb_addr', 'pending_cnt', 'mem_rdata'}
        preferred = [s for s in self.meta['signals'] if '.dut.' in s['name'] and '.' not in s['name'].split('.dut.', 1)[1]]
        self.ids = [s['id'] for s in (preferred or self.meta['signals']) if s['name'].split('.')[-1].split('[')[0] in wanted][:20]
        if not self.ids:
            self.ids = [s['id'] for s in self.meta['signals'][:10]]
        self.after_idle(self.draw)

    def populate(self):
        self.signals.delete(*self.signals.get_children())
        needle = self.filter.get().lower()
        for s in self.meta['signals']:
            if needle in s['name'].lower():
                self.signals.insert('', 'end', iid=str(s['id']), values=(s['name'], s['width']))

    def add_selected(self):
        self.ids = list(dict.fromkeys(self.ids+[int(x) for x in self.signals.selection()]))[:64]
        self.draw()

    def clear(self):
        self.ids = []; self.draw()

    def fit(self):
        self.start, self.end = 0, max(1, self.meta['end']); self.draw()

    def zoom(self, factor):
        center = self.cursor if self.start <= self.cursor <= self.end else (self.start+self.end)//2
        width = max(1, int((self.end-self.start)*factor))
        self.start = max(0, center-width//2); self.end = self.start+width; self.draw()

    def pan(self, factor):
        width = self.end-self.start; self.start = max(0, self.start+int(width*factor)); self.end = self.start+width; self.draw()

    def locate(self):
        try:
            start, end = int(self.from_var.get()), int(self.to_var.get())
            if not 0 <= start < end:
                raise ValueError()
            self.start, self.end = start, end; self.draw()
        except ValueError:
            self.note.set('时间范围必须是非负整数，且起点小于终点；单位为 '+self.meta['timescale'])

    def point(self, event, second=False):
        x0 = self.name_width; width = max(1, self.canvas.winfo_width()-x0-12)
        if event.x < x0:
            return
        point = min(self.end, max(self.start, self.start+round((event.x-x0)/width*(self.end-self.start))))
        if second:
            self.cursor_b = point
        else:
            self.cursor = point
        self.draw()

    def value_text(self, value, bits):
        if not value or any(c in value for c in 'xz'):
            return value.upper()
        try:
            number = int(value, 2)
            if self.format.get() == 'unsigned':
                return str(number)
            if self.format.get() == 'bin':
                return value.zfill(bits)
            return ('%0*x'%((bits+3)//4, number)).upper()
        except ValueError:
            return value

    def draw(self):
        c = self.canvas; c.delete('all'); w = c.winfo_width()
        self.name_width = min(345, max(240, int(w*.36))); x0 = self.name_width
        self.from_var.set(str(self.start)); self.to_var.set(str(self.end))
        h = max(c.winfo_height(), 48+len(self.ids)*46); c.configure(scrollregion=(0, 0, w, h))
        if w < 10:
            return
        width = max(1, w-x0-12)
        def x(t): return x0+(t-self.start)*width/(self.end-self.start)
        for i in range(9):
            tick = self.start+int((self.end-self.start)*i/8); px = x(tick)
            c.create_line(px, 30, px, h, fill='#26364f')
            c.create_text(px+2 if i == 0 else px, 18, text=str(tick), anchor='w' if i == 0 else ('e' if i == 8 else 'center'), fill='#9eb0cb', font=('TkFixedFont', 9))
        c.create_rectangle(0, 0, x0-2, h, fill='#18263d', outline='')
        c.create_text(12, 18, text='信号 / 游标 A 数值', anchor='w', fill='#a8bbd5')
        if not self.ids:
            c.create_text(x0+30, 80, anchor='w', text='从左侧添加信号', fill='#9eb0cb'); return
        data = vcd.window(self.database, self.ids, self.start, self.end)
        dense = 0
        for row, signal in enumerate(data['signals']):
            y = 48+row*46; values = signal['values']; current = 'x' if self.start <= self.cursor <= self.end else '—'
            for t, value in values:
                if self.start <= self.cursor <= self.end and t <= self.cursor: current = value
            name = signal['name']; short = name if len(name) <= 32 else '…'+name[-31:]
            c.create_text(10, y, text=short, anchor='w', fill='#dbe6f6', font=('TkFixedFont', 9))
            c.create_text(10, y+16, text=self.value_text(current, signal['width'])[:42], anchor='w', fill='#80dbc5', font=('TkFixedFont', 9))
            c.create_line(0, y+31, w, y+31, fill='#22334d')
            if signal['dense'] or len(values) > 4000:
                dense += 1
                c.create_text(x0+16, y+7, text='跳变密集，请放大时间范围查看准确边沿', anchor='w', fill='#edc77b'); continue
            for i, (t, value) in enumerate(values):
                a, b = x(t), x(values[i+1][0] if i+1 < len(values) else self.end)
                color = '#f0aa76' if any(k in value for k in 'xz') else '#6bddbd'
                if signal['width'] == 1 and value in ('0', '1'):
                    yy = y-2 if value == '1' else y+20
                    c.create_line(a, yy, b, yy, fill=color, width=2)
                    if i and values[i-1][1] in ('0', '1') and values[i-1][1] != value:
                        c.create_line(a, y-2, a, y+20, fill=color, width=2)
                else:
                    c.create_polygon(a, y+9, min(a+4, b), y-2, max(a, b-4), y-2, b, y+9,
                                     max(a, b-4), y+20, min(a+4, b), y+20, outline=color, fill='')
                    text = self.value_text(value, signal['width'])
                    if b-a > len(text)*7+12:
                        c.create_text((a+b)/2, y+9, text=text, fill=color, font=('TkFixedFont', 9))
        for point, color in [(self.cursor, '#f7d17c'), (self.cursor_b, '#d1a5fa')]:
            if point is not None and self.start <= point <= self.end:
                c.create_line(x(point), 30, x(point), h, fill=color, dash=(4, 3))
        note = 'A = %d'%self.cursor
        if self.cursor_b is not None:
            note += '   B = %d   Δ = %d'%(self.cursor_b, self.cursor_b-self.cursor)
        self.note.set(note+'  × '+self.meta['timescale']+'   |   %d 个信号'%len(self.ids)+
                      ('   |   %d 个密集信号需放大'%dense if dense else '   |   Shift + 单击设置 B；支持 X / Z'))
