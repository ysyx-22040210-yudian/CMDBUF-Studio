"""Vim-style editing commands for a Tk Text widget."""
import re
import tkinter as tk


WORD = re.compile(r'\w+|[^\w\s]+')
BIG_WORD = re.compile(r'\S+')
MOTIONS = 'hljk0^$wbeWBEG%'
SUBSTITUTION = re.compile(r'(%?)s/((?:\\/|[^/])*?)/((?:\\/|[^/])*?)(?:/([gi]*))?\Z')


class VimRegister:
    def __init__(self):
        self.text = ''
        self.linewise = False


class VimEdit:
    def __init__(self, editor, register):
        self.editor = editor
        self.text = editor.text
        self.register = register
        self.enabled = False
        self.mode = 'NORMAL'
        self.anchor = None
        self.column = None
        self.pattern = ''
        self.direction = 1
        self.reset_command()

    def reset_command(self):
        self.digits = ''
        self.operator = ''
        self.prefix = ''
        self.operator_count = 1

    def enable(self, enabled):
        self.enabled = enabled
        self.normal()

    def caption(self):
        pending = '{}{}{}'.format(self.operator, self.digits, self.prefix)
        return self.mode + ('  ' + pending if pending else '')

    def normal(self, leaving_insert=False):
        if self.mode == 'INSERT':
            self.text.configure(autoseparators=True)
            self.text.edit_separator()
        self.mode = 'NORMAL'
        self.anchor = self.column = None
        self.reset_command()
        self.text.tag_remove('sel', '1.0', 'end')
        self.text.tag_remove('editor_visual', '1.0', 'end')
        if self.enabled:
            index = self.text.index('insert')
            if leaving_insert and not index.endswith('.0'):
                index += ' -1c'
            self.move(index)
        self.editor.refresh_cursor()

    def insert(self, index):
        self.mode = 'INSERT'
        self.text.edit_separator()
        self.text.configure(autoseparators=False)
        self.text.mark_set('insert', index)
        self.text.see('insert')

    def move(self, index):
        index = self.text.index(index)
        line = int(index.split('.')[0])
        if line > self.last_line():
            index = self.text.index('{}.0 lineend'.format(self.last_line()))
        end = self.text.index(index + ' lineend')
        if self.text.compare(index, '>=', end) and not end.endswith('.0'):
            index = self.text.index(end + ' -1c')
        self.text.mark_set('insert', index)
        self.text.see('insert')
        if self.anchor:
            self.text.tag_remove('editor_visual', '1.0', 'end')
            start, end = self.visual_range()
            self.text.tag_add('editor_visual', start, end)

    def ordered(self, first, last):
        if self.text.compare(first, '>', last):
            return last, first
        return first, last

    def visual_range(self):
        start, end = self.ordered(self.anchor, self.text.index('insert'))
        if self.mode == 'VISUAL LINE':
            return self.line_range(start, end)
        return start, self.text.index(end + ' +1c')

    def line_range(self, first, last):
        return self.text.index(first + ' linestart'), self.text.index(last + ' lineend +1c')

    def last_line(self):
        return int(self.text.index('end-2c').split('.')[0])

    def first_nonblank(self, index):
        start = self.text.index(index + ' linestart')
        return start + ' +{}c'.format(len(self.leading_space(start)))

    def leading_space(self, index):
        line = self.text.get(index + ' linestart', index + ' lineend')
        return line[:len(line) - len(line.lstrip(' \t'))]

    def motion(self, key, count, counted, start):
        """Return destination, linewise flag, and inclusive flag."""
        line, column = (int(value) for value in start.split('.'))
        last_line = self.last_line()
        if key in ('j', 'k'):
            if self.column is None:
                self.column = column
            line = max(1, min(last_line, line + (count if key == 'j' else -count)))
            return '{}.{}'.format(line, self.column), True, False
        self.column = None
        if key in ('h', 'l'):
            index = start + ' {:+d}c'.format(count if key == 'l' else -count)
            border = start + (' lineend' if key == 'l' else ' linestart')
            if self.text.compare(index, '>' if key == 'l' else '<', border):
                index = border
            return self.text.index(index), False, False
        if key in ('gg', 'G'):
            line = count if counted else (1 if key == 'gg' else last_line)
            return self.first_nonblank('{}.0'.format(min(line, last_line))), True, False
        if key == '0':
            return '{}.0'.format(line), False, False
        if key == '^':
            return self.first_nonblank(start), False, False
        if key == '$':
            row = '{}.0'.format(min(last_line, line + count - 1))
            end = self.text.index(row + ' lineend')
            return (end, False, False) if end == row else (end + ' -1c', False, True)
        if key == '%':
            bracket = self.editor.match_bracket(start, scan_line=True)
            return bracket or start, False, bracket is not None

        cursor = len(self.text.get('1.0', start))
        tokens = (BIG_WORD if key.isupper() else WORD).finditer(self.editor.get())
        if key in 'bB':
            starts = [m.start() for m in tokens if m.start() < cursor]
            return '1.0 +{}c'.format(starts[max(0, len(starts) - count)] if starts else 0), False, False
        inclusive = key in 'eE'
        points = (m.end() - 1 if inclusive else m.start() for m in tokens)
        last = None
        for point in points:
            if point > cursor:
                last = point
                count -= 1
                if not count:
                    break
        if last is not None:
            return '1.0 +{}c'.format(last), False, inclusive
        return 'end-1c', False, False

    def word_range(self, around, count, big):
        buffer = self.editor.get()
        cursor = len(self.text.get('1.0', 'insert'))
        token = BIG_WORD if big else WORD
        words = [m for m in token.finditer(buffer) if m.end() > cursor]
        if not words:
            return
        start, end = words[0].start(), words[min(count, len(words)) - 1].end()
        if around:
            tail = re.match(r'[ \t]+', buffer[end:])
            if tail:
                end += tail.end()
            else:
                start -= len(buffer[:start]) - len(buffer[:start].rstrip(' \t'))
        return '1.0 +{}c'.format(start), '1.0 +{}c'.format(end)

    def apply(self, operator, start, end, linewise=False):
        if not self.text.compare(start, '<', end):
            if operator == 'c' and not self.anchor:
                self.insert(start)
            return
        value = self.text.get(start, end)
        self.register.text = value
        self.register.linewise = linewise
        if operator == 'y':
            self.normal()
            self.move(start)
            return

        self.text.edit_separator()
        self.text.configure(autoseparators=False)
        if linewise and operator == 'c':
            indent = self.leading_space(start)
            self.text.delete(start, end + ' -1c')
            self.text.insert(start, indent)
            start += ' +{}c'.format(len(indent))
        else:
            if linewise and self.text.compare(end, '==', 'end') and self.text.compare(start, '>', '1.0'):
                self.text.delete(start + ' -1c', end)
            else:
                self.text.delete(start, end)
        # A change and its following insert are a single undoable edit.
        self.text.tag_remove('editor_visual', '1.0', 'end')
        self.anchor = None
        if operator == 'c':
            self.mode = 'INSERT'
            self.text.mark_set('insert', start)
            self.text.see('insert')
        else:
            self.text.edit_separator()
            self.text.configure(autoseparators=True)
            self.normal()
            self.move(start)

    def paste(self, after, count):
        if not self.register.text:
            self.editor.notice.set('寄存器为空')
            return
        value = self.register.text * count
        if self.register.linewise:
            end = self.text.index('insert lineend')
            if after and self.text.compare(end, '==', 'end-1c') and self.editor.get():
                index, value, cursor = end, '\n' + value[:-1], end + ' +1c'
            elif not self.editor.get():
                index, value, cursor = '1.0', value[:-1], '1.0'
            else:
                index = self.text.index('insert lineend +1c' if after else 'insert linestart')
                cursor = index
        else:
            index = self.text.index('insert +1c' if after and not self.text.compare(
                'insert', '==', 'insert lineend') else 'insert')
            cursor = index + ' +{}c'.format(len(value) - 1)
        with self.editor.edit_group():
            self.text.insert(index, value)
        self.move(self.first_nonblank(cursor) if self.register.linewise else cursor)

    def paste_visual(self, after, count):
        value = self.register.text * count
        start, end = self.visual_range()
        linewise = self.mode == 'VISUAL LINE'
        if after:
            self.register.text = self.text.get(start, end)
            self.register.linewise = linewise
        if linewise and not value.endswith('\n'):
            value += '\n'
        if self.text.compare(end, '==', 'end') and value.endswith('\n'):
            value = value[:-1]
        offset = 0 if value.endswith('\n') or linewise else max(0, len(value) - 1)
        with self.editor.edit_group():
            self.text.replace(start, end, value)
        self.normal()
        self.move(start + ' +{}c'.format(offset))

    def undo(self, redo=False):
        try:
            if redo:
                self.text.edit_redo()
            else:
                self.text.edit_undo()
        except tk.TclError:
            self.editor.notice.set('没有可重做的修改' if redo else '没有可撤销的修改')
            return False
        self.move('insert')
        return True

    def open_line(self, below):
        indent = self.leading_space('insert')
        index = self.text.index('insert lineend' if below else 'insert linestart')
        self.insert(index)
        self.text.insert(index, '\n' + indent if below else indent + '\n')
        if not below:
            self.text.mark_set('insert', index + ' +{}c'.format(len(indent)))
        self.text.see('insert')

    def search(self, pattern, direction, count=1):
        if pattern:
            self.pattern, self.direction = pattern, direction
        if self.pattern:
            result = self.editor.vim_find(re.compile(re.escape(self.pattern)), direction, count)
            if result:
                self.move(result)
            else:
                self.editor.notice.set('未找到 ' + self.pattern)

    def substitute(self, command):
        """Apply a literal :s or :%s command, preserving the editor's undo grouping."""
        match = SUBSTITUTION.fullmatch(command)
        if not match:
            return False
        whole, old, replacement, flags = match.groups(default='')
        old, replacement = old.replace(r'\/', '/'), replacement.replace(r'\/', '/')
        old = old or self.pattern
        if not old:
            self.editor.notice.set('请输入要替换的文字')
            return True
        self.pattern, self.direction = old, 1
        current_line = int(self.text.index('insert').split('.')[0])
        lines = range(1, self.last_line() + 1) if whole else (current_line,)
        pattern = re.compile(re.escape(old), re.IGNORECASE if 'i' in flags else 0)
        changes = []
        for row in lines:
            start = '{}.0'.format(row)
            text = self.text.get(start, start + ' lineend')
            for hit in pattern.finditer(text):
                first = self.text.index(start + ' +{}c'.format(hit.start()))
                last = self.text.index(start + ' +{}c'.format(hit.end()))
                changes.append((first, last, replacement))
                if 'g' not in flags:
                    break
        self.editor.replace_ranges(changes)
        if changes:
            self.move(changes[0][0])
        return True

    def key(self, event):
        if not self.enabled:
            return
        control = event.state & 0x4
        key = event.keysym
        if key == 'Escape' or (control and key == 'bracketleft'):
            self.normal(leaving_insert=self.mode == 'INSERT')
            if self.editor.find_mode:
                self.editor.close_find()
            return 'break'
        if self.mode == 'INSERT':
            return
        if control:
            if key == 'r':
                self.normal()
                self.undo(redo=True)
                return 'break'
            if key in ('s', 'f', 'g', 'h', 'slash', 'c', 'Insert', 'equal', 'minus', '0'):
                return
            return 'break' if event.char or key in ('Left', 'Right', 'Up', 'Down', 'Home', 'End') else None
        aliases = {'Left': 'h', 'Down': 'j', 'Up': 'k', 'Right': 'l',
                   'Home': '0', 'End': '$', 'BackSpace': 'h', 'space': 'l', 'Delete': 'x'}
        command = aliases.get(key, event.char if event.char.isprintable() else '')
        if command:
            self.editor.notice.set('')
            self.command(command)
        self.editor.refresh_cursor()
        return 'break' if command or key in ('Return', 'KP_Enter', 'Tab', 'ISO_Left_Tab') else None

    def command(self, key):
        start = self.text.index('insert')
        visual = self.anchor is not None
        if self.prefix == 'r':
            count = int(self.digits or '1')
            end = self.text.index('insert +{}c'.format(count))
            if self.text.compare(end, '<=', 'insert lineend'):
                self.text.edit_separator()
                self.text.replace(start, end, key * count)
                self.text.edit_separator()
                self.move(start + ' +{}c'.format(count - 1))
            self.reset_command()
            return
        if key in '0123456789' and (key != '0' or self.digits) and not self.prefix:
            self.digits = (self.digits + key)[:6]
            return
        if key in 'dcy' and not visual and not self.operator and not self.prefix:
            self.operator, self.operator_count = key, int(self.digits or '1')
            self.digits = ''
            return
        count = min(self.operator_count * int(self.digits or '1'), 999999)
        counted = bool(self.digits) or self.operator_count > 1
        if not self.prefix and (key == 'g' or (self.operator and key in 'ia')):
            self.prefix = key
            return
        operator, prefix = self.operator, self.prefix
        if not prefix and key in 'fFtT':
            self.prefix = key
            return
        if key == 'r' and not operator and not prefix and not visual:
            self.prefix = key
            return
        self.reset_command()
        if prefix in ('f', 'F', 't', 'T'):
            end = self.find_char(prefix, key, count, start)
            if end:
                if operator:
                    lo, hi = self.ordered(start, end)
                    self.apply(operator, lo, hi + ' +1c' if prefix.islower() else hi)
                else:
                    self.move(end)
        elif operator and prefix in ('i', 'a') and key in 'wW':
            region = self.word_range(prefix == 'a', count, key == 'W')
            if region:
                self.apply(operator, *region)
        elif operator and key == operator and not prefix:
            row = min(self.last_line(), int(start.split('.')[0]) + count - 1)
            end = '{}.0'.format(row)
            self.apply(operator, *self.line_range(start, end), linewise=True)
        elif (prefix == 'g' and key == 'g') or (not prefix and key in MOTIONS):
            motion = 'gg' if prefix else key
            if operator == 'c' and key in 'wW' and self.text.get('insert', 'insert +1c').strip():
                region = self.word_range(False, count, key == 'W')
                if region:
                    self.apply(operator, start, region[1])
                return
            end, linewise, inclusive = self.motion(motion, count, counted, start)
            if operator:
                if motion in 'wW' and int(self.text.index(end).split('.')[0]) > int(start.split('.')[0]):
                    previous = self.text.index(end + ' -1c')
                    if count == 1 and self.text.get(previous, end).isspace():
                        end = start + ' lineend'
                if (motion == '%' and not inclusive) or (
                        motion in 'jk' and self.text.index(end).split('.')[0] == start.split('.')[0]):
                    return
                lo, hi = self.ordered(start, self.text.index(end))
                if linewise:
                    lo, hi = self.line_range(lo, hi)
                elif inclusive:
                    hi += ' +1c'
                self.apply(operator, lo, hi, linewise)
            else:
                self.move(end)
        elif prefix or operator:
            return
        elif key in 'vV':
            mode = 'VISUAL' if key == 'v' else 'VISUAL LINE'
            if self.mode == mode:
                self.normal()
            else:
                self.text.tag_remove('sel', '1.0', 'end')
                self.anchor = self.anchor or start
                self.mode = mode
                self.move(start)
        elif key == 'J':
            self.join_lines(start, count)
        elif visual:
            if key in 'dcyx':
                self.apply('d' if key == 'x' else key, *self.visual_range(), linewise=self.mode == 'VISUAL LINE')
            elif key in 'pP' and self.register.text:
                self.paste_visual(key == 'p', count)
        else:
            self.column = None
            self.normal_command(key, start, count)

    def normal_command(self, key, start, count):
        if key in 'iIaA':
            positions = {'i': start, 'I': self.first_nonblank(start), 'A': 'insert lineend'}
            self.insert(positions.get(key, 'insert +1c' if not self.text.compare(
                'insert', '==', 'insert lineend') else 'insert'))
        elif key in 'oO':
            self.open_line(key == 'o')
        elif key in 'xXsDC':
            if key == 'X':
                lo, _, _ = self.motion('h', count, False, start)
                hi = start
            elif key in 'DC':
                lo, hi = start, self.text.index('insert lineend')
            else:
                lo = start
                hi = self.text.index('insert +{}c'.format(count))
                if self.text.compare(hi, '>', 'insert lineend'):
                    hi = self.text.index('insert lineend')
            self.apply('c' if key in 'sC' else 'd', lo, hi)
        elif key in 'pP':
            self.paste(key == 'p', count)
        elif key == 'u':
            for _ in range(count):
                if not self.undo():
                    break
        elif key in '/?:':
            self.editor.prompt(key)
        elif key in 'nN':
            self.search('', self.direction if key == 'n' else -self.direction, count)

    def find_char(self, motion, char, count, start):
        forward = motion.islower()
        edge = self.text.index(start + (' +1c' if forward else ' linestart'))
        if forward and self.text.compare(edge, '>', start + ' lineend'):
            return
        source = self.text.get(edge, start + ' lineend' if forward else start)
        locations = [match.start() for match in re.finditer(re.escape(char), source)]
        if len(locations) < count:
            return
        offset = locations[count-1] if forward else locations[-count]
        if motion in 'tT':
            offset += -1 if forward else 1
        return self.text.index(edge + ' +{}c'.format(offset))

    def join_lines(self, index, count):
        line = int(index.split('.')[0])
        if self.anchor:
            anchor = int(self.anchor.split('.')[0])
            line, count = min(line, anchor), abs(line - anchor) + 1
        last = min(self.last_line(), line + max(2, count) - 1)
        if last <= line:
            return
        self.normal()
        with self.editor.edit_group():
            for row in range(last-1, line-1, -1):
                end = self.text.index('{}.0 lineend'.format(row))
                following = '{}.0'.format(row + 1)
                next_word = self.first_nonblank(following)
                separator = '' if end.endswith('.0') or self.text.get(end + ' -1c', end).isspace() else ' '
                self.text.replace(end, next_word, separator)
        self.move(end)
