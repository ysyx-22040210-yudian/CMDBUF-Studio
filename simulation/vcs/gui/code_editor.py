"""Native SystemVerilog editor and read-only source viewer."""
from bisect import bisect_left, bisect_right
from contextlib import contextmanager
import re
import tkinter as tk
from tkinter import font as tkfont, ttk

from vim_edit import VimEdit, VimRegister


BACKGROUND = '#1e1e1e'
PANEL = '#252526'
FOREGROUND = '#d4d4d4'
MUTED = '#a3a3a3'
SELECTION = '#264f78'
ACTIVE_LINE = '#2a2d2e'
BORDER = '#363636'
ACCENT = '#007acc'
TOKENS = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))'
    r'|(?P<string>"(?:\\[^\n]|[^"\\\n])*"?)'
    r"|(?P<number>(?<![\w$])(?:\d[\d_]*)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+"
    r"|(?<![\w$])'[01xXzZ]\b|(?<![\w$])\d[\d_]*(?:\.\d[\d_]*)?"
    r'(?:[eE][+-]?\d[\d_]*)?(?:ms|us|ns|ps|fs|s)?\b)'
    r'|(?P<macro>`[A-Za-z_]\w*|\$[A-Za-z_]\w*)'
    r'|(?P<identifier>\b[A-Za-z_][\w$]*)'
    r'|(?P<bracket>[()\[\]{}])'
)
KEYWORDS = set((
    'always always_comb always_ff always_latch assert assign assume automatic begin '
    'bind break case casex casez checker class clocking const constraint continue cover '
    'default disable do else end endcase endchecker endclass endclocking endfunction '
    'endgenerate endinterface endmodule endpackage endprogram endproperty endsequence '
    'endtask enum event export extends final for foreach forever fork function generate '
    'genvar if iff import initial inout input inside interface local localparam module '
    'negedge new output package packed parameter posedge priority program property '
    'protected rand randc randomize repeat return sequence signed static struct super '
    'task this typedef union unique unique0 unsigned virtual wait while with'
).split())
TYPES = set('bit byte int integer logic longint real realtime reg shortint string time void wire'.split())
PALETTE = {'comment': '#6a9955', 'string': '#ce9178', 'number': '#b5cea8', 'keyword': '#c586c0',
           'type': '#4ec9b0', 'macro': '#dcdcaa', 'bracket': '#d4d4d4'}
OPEN = '([{'
CLOSE = ')]}'


def match_indexes(source, matches):
    """Map regex matches to Text indexes, using offsets within each line."""
    row, line_start = 1, 0
    line_end = source.find('\n')
    for match in matches:
        while 0 <= line_end < match.start():
            row, line_start = row + 1, line_end + 1
            line_end = source.find('\n', line_start)
        start = '{}.0 +{}c'.format(row, match.start() - line_start)
        end = start + ' +{}c'.format(match.end() - match.start())
        yield match, start, end


def code_styles(parent):
    style = ttk.Style(parent)
    style.configure('Code.TEntry', fieldbackground='#333333', foreground='#f0f0f0',
                    insertcolor='white', bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=(6, 3))
    style.configure('Code.Horizontal.TScrollbar', background='#494949', troughcolor=BACKGROUND,
                    bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND, arrowsize=9, width=9)
    style.configure('Code.Vertical.TScrollbar', background='#494949', troughcolor=BACKGROUND,
                    bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND, arrowsize=9, width=9)
    for orient in ('Horizontal', 'Vertical'):
        style.map('Code.' + orient + '.TScrollbar', background=[('active', '#626262')])


class CodeEditor(ttk.Frame):
    def __init__(self, parent, mono='TkFixedFont', readonly=False, height=15, on_change=None,
                 filename=None, on_save=None, vim_enabled=None, vim_register=None, language='sv'):
        super().__init__(parent)
        # Embedded editors live inside a scrollable page on smaller displays.
        # Keep a keyboard jump visible in that viewport as well as in Text.
        container = parent
        while container is not None and not isinstance(container, tk.Canvas):
            container = container.master
        self.viewport = container
        self.readonly = readonly
        self.language = language
        self.on_change = on_change
        self.on_save = on_save
        self.vim_enabled = vim_enabled if vim_enabled is not None else tk.BooleanVar(master=self)
        self.pending = self.cursor_idle = self.gutter_idle = None
        self.loading = False
        self.brackets = {}
        self.find_mode = ''
        self.matches = []
        self.current_match = None
        self.last_cursor_line = None
        self.file = tk.StringVar(master=self, value=filename or ('SystemVerilog' if language == 'sv' else '文本'))
        self.file_type = tk.StringVar(master=self, value='SV' if language == 'sv' else 'TXT')
        self.position = tk.StringVar(master=self)
        self.notice = tk.StringVar(master=self)
        self.mode = tk.StringVar(master=self)
        code_styles(self)
        self.font = tkfont.Font(root=self, font=(mono, 11))
        self.gutter = tk.Canvas(self, width=50, background=BACKGROUND, highlightthickness=0, borderwidth=0,
                                takefocus=False)
        self.text = tk.Text(self, wrap='none', undo=not readonly, maxundo=200, font=self.font, height=height,
                            width=48, background=BACKGROUND, foreground=FOREGROUND, insertbackground='#f0f0f0',
                            selectbackground=SELECTION, selectforeground='#ffffff', relief='flat', borderwidth=0,
                            highlightthickness=0, padx=10, pady=8, spacing1=2, spacing3=2,
                            tabs=(self.font.measure('    '),))
        self.block_cursor = 'blockcursor' in self.text.keys()
        if 'inactiveselectbackground' in self.text.keys():
            self.text.configure(inactiveselectbackground=SELECTION)
        self.vim = VimEdit(self, vim_register if vim_register is not None else VimRegister())
        self.build_header()
        self.build_find()
        self.build_status()

        y = ttk.Scrollbar(self, orient='vertical', command=self.text.yview, style='Code.Vertical.TScrollbar')
        x = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview, style='Code.Horizontal.TScrollbar')

        def yscroll(first, last):
            y.set(first, last)
            self.queue_gutter()

        self.text.configure(yscrollcommand=yscroll, xscrollcommand=x.set)
        self.gutter.grid(row=3, column=0, sticky='ns')
        self.text.grid(row=3, column=1, sticky='nsew')
        y.grid(row=3, column=2, sticky='ns')
        x.grid(row=4, column=1, sticky='ew')
        self.rowconfigure(3, weight=1)
        self.columnconfigure(1, weight=1)
        self.text.tag_configure('editor_line', background=ACTIVE_LINE)
        self.text.tag_configure('editor_find', background='#423923')
        self.text.tag_configure('editor_match', background='#815526', foreground='#ffffff')
        self.text.tag_configure('editor_bracket', background='#34515e', foreground='#ffffff')
        self.text.tag_configure('editor_visual', background=SELECTION, foreground='#ffffff')
        for tag, color in PALETTE.items():
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_raise('editor_bracket')
        self.text.tag_raise('editor_match')
        self.text.tag_raise('editor_visual')
        self.text.tag_raise('sel')
        self.text.bind('<<Modified>>', self.changed)
        self.text.bind('<Configure>', lambda event: self.queue_gutter())
        self.text.bind('<KeyRelease>', self.queue_cursor)
        self.text.bind('<ButtonRelease-1>', self.queue_cursor)
        self.text.bind('<FocusIn>', self.queue_cursor)
        self.text.bind('<Control-equal>', lambda event: self.zoom(1))
        self.text.bind('<Control-minus>', lambda event: self.zoom(-1))
        self.text.bind('<Control-0>', lambda event: self.zoom(0))
        if not readonly:
            self.text.bind('<ButtonPress-1>', self.clear_vim_selection)
            self.text.bind('<Tab>', self.indent)
            self.text.bind('<Shift-Tab>', lambda event: self.indent(event, reverse=True))
            if self.tk.call('tk', 'windowingsystem') == 'x11':
                self.text.bind('<ISO_Left_Tab>', lambda event: self.indent(event, reverse=True))
            self.text.bind('<Return>', self.newline)
            self.text.bind('<Control-slash>', self.toggle_comment)
            self.text.bind('<Control-h>', lambda event: self.open_find(True))
            if on_save:
                self.text.bind('<Control-s>', self.save_key)
            # Tk widget bindings for individual shortcuts run after the Vim guard.
            # Bind by tag so Normal mode receives Return/Tab before Text can insert.
            self.modal_tag = 'CodeEditorVim' + str(self.text)
            self.text.bindtags((self.modal_tag,) + self.text.bindtags())
            self.modal_binding = self.text.bind_class(self.modal_tag, '<KeyPress>', self.vim.key)
            self.text.bind('<<Copy>>', self.copy_selection)
            for sequence in ('<<Paste>>', '<<PasteSelection>>', '<ButtonRelease-2>', '<<Cut>>', '<<Clear>>'):
                self.text.bind(sequence, self.guard_modal_edit)
            self.vim_trace = self.vim_enabled.trace_add('write', self.sync_mode)
            self.sync_mode()
        else:
            self.text.configure(state='disabled')
        self.text.bind('<Control-f>', lambda event: self.open_find())
        self.text.bind('<F3>', lambda event: self.find_next())
        self.text.bind('<Shift-F3>', lambda event: self.find_next(-1))
        self.text.bind('<Control-g>', lambda event: self.prompt(':', '行号'))
        self.text.bind('<Escape>', lambda event: self.close_find() if self.find_mode else None)
        self.bind('<Destroy>', self.cleanup, add='+')
        self.refresh_cursor()

    def small_button(self, parent, label, command):
        return tk.Button(parent, text=label, command=command, background=PANEL, foreground='#d4d4d4',
                         activebackground='#383838', activeforeground='white', relief='flat', borderwidth=0,
                         highlightthickness=0, padx=8, pady=3, cursor='hand2')

    def build_header(self):
        header = tk.Frame(self, bg=PANEL)
        header.grid(row=0, column=0, columnspan=3, sticky='ew')
        badge = tk.Label(header, textvariable=self.file_type, bg=BACKGROUND, fg='#73c991',
                         padx=8, pady=6, font=('TkDefaultFont', 9, 'bold'))
        badge.pack(side='left', padx=(1, 0))
        self.small_button(header, '查找', self.open_find).pack(side='right', padx=(0, 4))
        if not self.readonly:
            self.vim_button = self.small_button(header, 'Vim', self.toggle_vim)
            self.vim_button.pack(side='right', padx=(0, 2))
        tab = tk.Label(header, textvariable=self.file, bg=BACKGROUND, fg=FOREGROUND, padx=8, pady=6, anchor='w')
        tab.pack(side='left', fill='x')

    def build_status(self):
        bar = tk.Frame(self, bg=PANEL)
        bar.grid(row=1, column=0, columnspan=3, sticky='ew')
        tk.Label(bar, textvariable=self.position, bg=PANEL, fg=FOREGROUND, padx=10, pady=4).pack(side='right')
        tk.Label(bar, textvariable=self.mode, bg=ACCENT, fg='white', padx=9, pady=4).pack(side='left')
        tk.Label(bar, textvariable=self.notice, bg=PANEL, fg=FOREGROUND, padx=8, pady=4).pack(side='left')
        self.mode.set('只读' if self.readonly else '编辑')

    def build_find(self):
        frame = tk.Frame(self, bg=PANEL, padx=5, pady=4)
        self.find_bar = frame
        frame.grid(row=2, column=0, columnspan=3, sticky='ew')
        frame.columnconfigure(1, weight=1)
        self.search_value = tk.StringVar(master=self)
        self.replace_value = tk.StringVar(master=self)
        self.match_count = tk.StringVar(master=self)
        self.find_label = tk.Label(frame, text='查找', bg=PANEL, fg=FOREGROUND, width=4, anchor='w')
        self.find_label.grid(row=0, column=0, padx=3)
        self.search_entry = ttk.Entry(frame, textvariable=self.search_value, style='Code.TEntry', width=18)
        self.search_entry.grid(row=0, column=1, sticky='ew')
        tk.Label(frame, textvariable=self.match_count, bg=PANEL, fg=MUTED, width=9).grid(row=0, column=2)
        self.find_controls = tk.Frame(frame, bg=PANEL)
        self.find_controls.grid(row=0, column=3)
        self.small_button(self.find_controls, '↑', lambda: self.find_next(-1)).pack(side='left')
        self.small_button(self.find_controls, '↓', self.find_next).pack(side='left')
        self.small_button(frame, '×', self.close_find).grid(row=0, column=4)
        self.replace_row = tk.Frame(frame, bg=PANEL)
        self.replace_row.grid(row=1, column=0, columnspan=5, sticky='ew', pady=(4, 0))
        tk.Label(self.replace_row, text='替换', bg=PANEL, fg=FOREGROUND, width=4, anchor='w').pack(side='left', padx=3)
        self.small_button(self.replace_row, '全部替换', lambda: self.replace_matches(True)).pack(side='right')
        self.small_button(self.replace_row, '替换', self.replace_matches).pack(side='right')
        replace = ttk.Entry(self.replace_row, textvariable=self.replace_value, style='Code.TEntry')
        replace.pack(side='left', fill='x', expand=True)
        replace.bind('<Return>', lambda event: self.replace_matches())
        self.search_entry.bind('<Return>', self.submit_find)
        self.search_entry.bind('<Shift-Return>', lambda event: self.find_next(-1))
        for item in (self.search_entry, replace):
            item.bind('<Escape>', lambda event: self.close_find())
            item.bind('<F3>', lambda event: self.find_next())
            item.bind('<Shift-F3>', lambda event: self.find_next(-1))
            item.bind('<Control-f>', lambda event: self.open_find())
            if not self.readonly:
                item.bind('<Control-h>', lambda event: self.open_find(True))
        self.search_trace = self.search_value.trace_add('write', lambda *args: self.update_matches())
        frame.grid_remove()

    def cleanup(self, event):
        if event.widget is not self:
            return
        for callback in (self.pending, self.cursor_idle, self.gutter_idle):
            if callback:
                self.after_cancel(callback)
        self.search_value.trace_remove('write', self.search_trace)
        if not self.readonly:
            self.vim_enabled.trace_remove('write', self.vim_trace)
            self.text.unbind_class(self.modal_tag, '<KeyPress>')
            self._root().deletecommand(self.modal_binding)

    def sync_mode(self, *args):
        self.vim.enable(self.vim_enabled.get())
        self.vim_button.configure(background=ACCENT if self.vim.enabled else PANEL,
                                  foreground='white' if self.vim.enabled else FOREGROUND)
        self.notice.set('i 插入 · Esc 返回 · :w 保存' if self.vim.enabled else '')

    def toggle_vim(self):
        self.vim_enabled.set(not self.vim_enabled.get())
        self.text.focus_set()

    def clear_vim_selection(self, event):
        if self.vim.enabled and self.vim.mode.startswith('VISUAL'):
            self.vim.normal()

    def guard_modal_edit(self, event):
        if self.vim.enabled and self.vim.mode != 'INSERT':
            return 'break'

    def copy_selection(self, event):
        if self.vim.anchor:
            self.text.clipboard_clear()
            self.text.clipboard_append(self.text.get(*self.vim.visual_range()))
            return 'break'

    def save_key(self, event):
        self.on_save()
        return 'break'

    def get(self):
        return self.text.get('1.0', 'end-1c')

    def set(self, value, filename=None):
        if filename is not None:
            self.file.set(filename)
            if self.readonly:
                self.language = 'sv' if filename.lower().endswith(('.sv', '.svh', '.v', '.vh')) else 'text'
                self.file_type.set('SV' if self.language == 'sv' else 'TXT')
        if self.pending:
            self.after_cancel(self.pending)
            self.pending = None
        self.loading = True
        try:
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', value)
            self.text.mark_set('insert', '1.0')
            self.text.xview_moveto(0)
            self.text.yview_moveto(0)
            self.text.edit_reset()
            self.text.edit_modified(False)
            self.last_cursor_line = None
            self.vim.normal()
            if self.readonly:
                self.text.configure(state='disabled')
        finally:
            self.loading = False
        self.highlight()
        self.update_matches()

    def changed(self, event=None):
        if self.loading or not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(130, self.update_document)
        self.queue_cursor()
        if self.on_change:
            self.on_change()

    def update_document(self):
        self.pending = None
        self.highlight()
        self.update_matches()

    def flush_document(self):
        if self.pending:
            self.after_cancel(self.pending)
            self.update_document()

    def highlight(self):
        self.brackets = {}
        for tag in PALETTE:
            self.text.tag_remove(tag, '1.0', 'end')
        if self.language == 'sv':
            source = self.get()
            ranges = {tag: [] for tag in PALETTE}
            stack = []
            for token, start, end in match_indexes(source, TOKENS.finditer(source)):
                tag, value = token.lastgroup, token.group()
                if tag == 'identifier':
                    if value in KEYWORDS:
                        tag = 'keyword'
                    elif value in TYPES:
                        tag = 'type'
                    else:
                        continue
                ranges[tag].extend((start, end))
                if tag == 'bracket':
                    index = self.text.index(start)
                    if value in OPEN:
                        stack.append((value, index))
                    elif stack and stack[-1][0] == OPEN[CLOSE.index(value)]:
                        _, other = stack.pop()
                        self.brackets[other] = index
                        self.brackets[index] = other
            for tag, indexes in ranges.items():
                if indexes:
                    self.text.tag_add(tag, *indexes)
        self.refresh_cursor()

    def match_bracket(self, index, scan_line=False):
        self.flush_document()
        index = self.text.index(index)
        if scan_line:
            tail = self.text.get(index, index + ' lineend')
            for offset, value in enumerate(tail):
                if value in OPEN + CLOSE:
                    match = self.brackets.get(self.text.index(index + ' +{}c'.format(offset)))
                    if match:
                        return match
        return self.brackets.get(index)

    def queue_cursor(self, event=None):
        if not self.cursor_idle:
            self.cursor_idle = self.after_idle(self.refresh_cursor)

    def queue_gutter(self):
        if not self.gutter_idle:
            self.gutter_idle = self.after_idle(self.draw_queued_gutter)

    def draw_queued_gutter(self):
        self.gutter_idle = None
        self.redraw_gutter()

    def refresh_cursor(self):
        if self.cursor_idle:
            self.after_cancel(self.cursor_idle)
            self.cursor_idle = None
        index = self.text.index('insert')
        line, column = index.split('.')
        self.position.set('行 {}    列 {}    ·    {}    ·    UTF-8'.format(
            line, int(column) + 1, 'SystemVerilog' if self.language == 'sv' else '文本'))
        if not self.readonly:
            self.mode.set(self.vim.caption() if self.vim.enabled else '编辑')
            if self.block_cursor:
                self.text.configure(blockcursor=self.vim.enabled and self.vim.mode != 'INSERT')
            if self.last_cursor_line != line:
                self.text.tag_remove('editor_line', '1.0', 'end')
                self.text.tag_add('editor_line', line + '.0', line + '.0 lineend +1c')
                self.last_cursor_line = line
        self.text.tag_remove('editor_bracket', '1.0', 'end')
        at = index if index in self.brackets else self.text.index('insert -1c')
        if at in self.brackets:
            for mark in (at, self.brackets[at]):
                self.text.tag_add('editor_bracket', mark, mark + ' +1c')
        if self.viewport is not None and self.focus_get() == self.text:
            self.reveal_in_page(index)
        self.queue_gutter()

    def reveal_in_page(self, index):
        canvas = self.viewport
        if canvas is None or not canvas.winfo_ismapped():
            return
        box = self.text.dlineinfo(index)
        if box is None:
            return
        first, last = canvas.yview()
        if last - first >= 1:
            return
        top = self.text.winfo_rooty() + box[1] - canvas.winfo_rooty()
        bottom = top + box[3]
        size = canvas.winfo_height()
        if top < 0 or bottom > size:
            area = canvas.tk.splitlist(canvas.cget('scrollregion'))
            if area:
                height = int(area[3]) - int(area[1])
                if height:
                    shift = top - 24 if top < 0 else bottom - size + 24
                    canvas.yview_moveto(first + shift / height)

    def redraw_gutter(self):
        self.gutter.delete('all')
        cursor = self.text.index('insert').split('.')[0] if not self.readonly else None
        width = max(40, self.font.measure(self.text.index('end-1c').split('.')[0]) + 22)
        ascent = self.font.metrics('ascent')
        self.gutter.configure(width=width)
        index = self.text.index('@0,0')
        wrapped = self.text.cget('wrap') != 'none'
        while True:
            box = self.text.dlineinfo(index)
            if box is None:
                break
            line = index.split('.')[0]
            if line == cursor:
                self.gutter.create_rectangle(0, box[1], width, box[1]+box[3], fill=ACTIVE_LINE, width=0)
            if not wrapped or self.text.compare(index, '==', index + ' linestart'):
                self.gutter.create_text(width-10, box[1]+box[4]-ascent, text=line, anchor='ne',
                                        fill=FOREGROUND if line == cursor else MUTED, font=self.font)
            following = self.text.index(index + (' +1 display lines' if wrapped else ' +1line'))
            if following == index or self.text.compare(following, '>=', 'end'):
                break
            index = following

    @contextmanager
    def edit_group(self):
        separators = self.text.cget('autoseparators')
        self.text.edit_separator()
        self.text.configure(autoseparators=False)
        try:
            yield
        finally:
            self.text.edit_separator()
            self.text.configure(autoseparators=separators)
            self.queue_cursor()

    def selection(self):
        return self.text.tag_ranges('editor_visual' if self.vim.anchor else 'sel')

    def selected_lines(self):
        selection = self.selection()
        if not selection:
            return [self.text.index('insert linestart')]
        first = int(str(selection[0]).split('.')[0])
        last = int(self.text.index(str(selection[-1]) + ' -1c').split('.')[0])
        return ['{}.0'.format(row) for row in range(first, last + 1)]

    def indent(self, event, reverse=False):
        selection = self.text.tag_ranges('sel')
        if not selection and not reverse:
            self.text.insert('insert', ' ' * (4 - int(self.text.index('insert').split('.')[1]) % 4))
            return 'break'
        with self.edit_group():
            for start in self.selected_lines():
                if reverse:
                    leading = re.match(r'(?: {1,4}|\t)', self.text.get(start, start + ' lineend'))
                    if leading:
                        self.text.delete(start, start + ' +{}c'.format(leading.end()))
                else:
                    self.text.insert(start, '    ')
        return 'break'

    def newline(self, event):
        selection = self.text.tag_ranges('sel')
        start = self.text.index(selection[0] if selection else 'insert')
        before = self.text.get(start + ' linestart', start)
        spaces = re.match(r'[ \t]*', before).group()
        if re.search(r'(?:\bbegin|\bfork|[({\[])\s*$', before):
            spaces += '    '
        if selection:
            with self.edit_group():
                self.text.replace(*selection, '\n' + spaces)
                self.text.mark_set('insert', start + ' +{}c'.format(1 + len(spaces)))
        else:
            self.text.insert('insert', '\n' + spaces)
        self.text.see('insert')
        return 'break'

    def toggle_comment(self, event=None):
        rows = [(index, self.text.get(index, index + ' lineend')) for index in self.selected_lines()]
        rows = [(index, line) for index, line in rows if line.strip()]
        uncomment = all(line.lstrip().startswith('//') for _, line in rows)
        with self.edit_group():
            for index, line in rows:
                column = len(line) - len(line.lstrip())
                start = index + ' +{}c'.format(column)
                if uncomment:
                    self.text.delete(start, start + (' +3c' if line[column:].startswith('// ') else ' +2c'))
                else:
                    self.text.insert(start, '// ')
        if self.vim.enabled and self.vim.mode.startswith('VISUAL'):
            self.vim.normal()
        return 'break'

    def show_find(self, mode):
        if self.vim.anchor:
            self.vim.normal()
        self.find_mode = mode
        self.find_label.config(text={'find': '查找', 'replace': '查找', '/': '/', '?': '?', ':': ':'}[mode])
        if mode in ('find', 'replace'):
            self.find_controls.grid()
        else:
            self.find_controls.grid_remove()
        if mode == 'replace':
            self.replace_row.grid()
        else:
            self.replace_row.grid_remove()
        self.find_bar.grid()
        self.update_matches()
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, 'end')

    def open_find(self, replace=False):
        selection = self.selection()
        if selection:
            value = self.text.get(*selection)
            if '\n' not in value:
                self.search_value.set(value)
        self.show_find('replace' if replace else 'find')
        return 'break'

    def prompt(self, mode, hint=None):
        self.search_value.set('')
        self.show_find(mode)
        if hint:
            self.find_label.configure(text=hint)
        return 'break'

    def close_find(self):
        self.text.focus_set()
        self.find_mode = ''
        self.find_bar.grid_remove()
        self.update_matches()
        return 'break'

    def zoom(self, steps):
        size = max(8, min(24, self.font.cget('size') + steps)) if steps else 11
        self.font.configure(size=size)
        self.text.configure(tabs=(self.font.measure('    '),))
        self.queue_gutter()
        return 'break'

    def find_ranges(self, pattern):
        source = self.get()
        return [(self.text.index(first), self.text.index(last))
                for _, first, last in match_indexes(source, pattern.finditer(source))]

    def update_matches(self):
        self.text.tag_remove('editor_find', '1.0', 'end')
        self.text.tag_remove('editor_match', '1.0', 'end')
        self.current_match = None
        self.matches = []
        query = self.search_value.get()
        if self.find_mode in ('find', 'replace') and query:
            self.matches = self.find_ranges(re.compile(re.escape(query)))
            if self.matches:
                self.text.tag_add('editor_find', *(index for match in self.matches for index in match))
        self.match_count.set(str(len(self.matches)) if self.find_mode in ('find', 'replace') else '')

    def select_match(self, index):
        self.current_match = index
        first, last = self.matches[index]
        self.text.tag_remove('editor_match', '1.0', 'end')
        self.text.tag_add('editor_match', first, last)
        self.text.mark_set('insert', first)
        self.update_idletasks()
        self.text.see(first)
        self.match_count.set('{}/{}'.format(index + 1, len(self.matches)))
        self.refresh_cursor()
        self.reveal_in_page(first)

    def find_next(self, direction=1):
        if not self.find_mode:
            return self.open_find()
        self.flush_document()
        if self.find_mode not in ('find', 'replace') or not self.matches:
            return 'break'
        if self.current_match is None:
            cursor = self.text.index('insert')
            if direction > 0:
                index = next((i for i, (start, _) in enumerate(self.matches)
                              if self.text.compare(start, '>=', cursor)), 0)
            else:
                index = next((i for i in range(len(self.matches) - 1, -1, -1)
                              if self.text.compare(self.matches[i][0], '<=', cursor)), len(self.matches) - 1)
        else:
            index = (self.current_match + direction) % len(self.matches)
        self.select_match(index)
        return 'break'

    def replace_matches(self, replace_all=False):
        self.flush_document()
        if self.readonly or not self.matches:
            return 'break'
        if self.current_match is None:
            self.find_next()
        ranges = self.matches if replace_all else [self.matches[self.current_match]]
        value = self.replace_value.get()
        self.replace_ranges([(first, last, value) for first, last in ranges])
        self.update_matches()
        return 'break'

    def replace_ranges(self, changes):
        if changes:
            with self.edit_group():
                for first, last, value in reversed(changes):
                    self.text.replace(first, last, value)
        self.notice.set('已替换 {} 处'.format(len(changes)))

    def vim_find(self, pattern, direction, count=1):
        matches = self.find_ranges(pattern)
        if not matches:
            return
        indexed = [(tuple(int(bit) for bit in start.split('.')), start) for start, _ in matches]
        keys, starts = zip(*indexed)
        cursor = tuple(int(bit) for bit in self.text.index('insert').split('.'))
        offset = bisect_right(keys, cursor) + count - 1 if direction > 0 else bisect_left(keys, cursor) - count
        return starts[offset % len(starts)]

    def submit_find(self, event=None):
        if self.find_mode in ('find', 'replace'):
            return self.find_next()
        mode, value = self.find_mode, self.search_value.get()
        self.close_find()
        if mode in ('/', '?'):
            self.vim.search(value, 1 if mode == '/' else -1)
        elif re.fullmatch(r'[0-9]{1,9}', value.strip()):
            last = self.vim.last_line() if self.vim.enabled else int(self.text.index('end-1c').split('.')[0])
            line = min(int(value), last)
            index = '{}.0'.format(max(1, line))
            if self.vim.enabled and self.vim.mode == 'NORMAL':
                self.vim.move(self.vim.first_nonblank(index))
            else:
                self.text.mark_set('insert', index)
                self.text.see(index)
        elif value.strip() in ('w', 'write') and self.on_save:
            self.on_save()
        elif value.strip() and not (self.vim.enabled and self.vim.substitute(value.lstrip())):
            self.notice.set('可用命令：:w 保存，:行号 跳转，:%s 替换')
        self.refresh_cursor()
        return 'break'
