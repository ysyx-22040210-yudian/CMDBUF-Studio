"""Exercise the Tk bindings for standard editing and Vim modes."""
from pathlib import Path
import sys
import unittest

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))
    from code_editor import CodeEditor
    from vim_edit import VimRegister


@unittest.skipIf(tk is None, 'Tk is not installed')
class CodeEditorTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda cls, exc, tb: self.errors.append(exc)
        self.register = VimRegister()
        self.enabled = tk.BooleanVar(self.root)
        self.editor = self.make_editor()
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

    def make_editor(self, parent=None, **options):
        editor = CodeEditor(parent or self.root, vim_enabled=self.enabled, vim_register=self.register, **options)
        editor.pack(fill='both', expand=True)
        self.focus(editor.text)
        return editor

    def focus(self, widget):
        self.root.update()
        widget.focus_force()
        self.root.update()

    def press(self, *keys, **options):
        for key in keys:
            widget = self.root.focus_get()
            self.assertIsNotNone(widget)
            widget.event_generate('<KeyPress>', keysym=key, **options)
            widget.event_generate('<KeyRelease>', keysym=key, **options)
            self.root.update()

    def test_normal_insert_change_and_undo(self):
        editor = self.editor
        editor.set('alpha beta\ngamma')
        editor.toggle_vim()
        self.press('Return', 'Tab', 'z')
        editor.text.clipboard_clear()
        editor.text.clipboard_append('clipboard')
        editor.text.event_generate('<<Paste>>')
        self.root.update()
        self.assertEqual(editor.get(), 'alpha beta\ngamma')
        self.press('c', 'i', 'w', 'X', 'Y', 'Escape')
        self.assertEqual(editor.get(), 'XY beta\ngamma')
        self.assertEqual(editor.mode.get(), 'NORMAL')
        self.assertEqual(editor.text.index('insert'), '1.1')
        self.press('u')
        self.assertEqual(editor.get(), 'alpha beta\ngamma')
        self.press('r', state=0x4)
        self.assertEqual(editor.get(), 'XY beta\ngamma')
        editor.toggle_vim()
        editor.text.mark_set('insert', '1.1')
        self.press('z')
        self.assertEqual(editor.get(), 'XzY beta\ngamma')

    def test_visual_line_register_and_last_line_delete(self):
        self.editor.set('alpha\n  beta\ngamma')
        self.editor.text.clipboard_clear()
        self.editor.text.clipboard_append('clipboard')
        self.enabled.set(True)
        self.press('j', 'V', 'j')
        self.assertEqual(self.editor.mode.get(), 'VISUAL LINE')
        self.assertEqual(self.editor.text.get(*self.editor.selection()), '  beta\ngamma\n')
        self.assertEqual(self.editor.text.clipboard_get(), 'clipboard')
        self.press('y')
        other = self.make_editor()
        other.set('module m;\nendmodule')
        self.assertEqual(other.mode.get(), 'NORMAL')
        self.press('p')
        self.assertEqual(other.get(), 'module m;\n  beta\ngamma\nendmodule')
        self.press('G', 'd', 'd')
        self.assertEqual(other.get(), 'module m;\n  beta\ngamma')
        self.press('u')
        self.assertEqual(other.get(), 'module m;\n  beta\ngamma\nendmodule')

    def test_counted_character_motions_join_and_visual_paste(self):
        editor = self.editor
        editor.set('a,bc,def\n    tail\nsource end')
        self.enabled.set(True)
        self.press('2', 'f', 'comma')
        self.assertEqual(editor.text.index('insert'), '1.4')
        self.press('0', 'd', 't', 'b')
        self.assertEqual(editor.get(), 'bc,def\n    tail\nsource end')
        self.press('J')
        self.assertEqual(editor.get(), 'bc,def tail\nsource end')
        self.press('G', 'y', 'i', 'w', 'g', 'g', 'v', 'l', 'p')
        self.assertEqual(editor.get(), 'source,def tail\nsource end')
        self.press('u')
        self.assertEqual(editor.get(), 'bc,def tail\nsource end')

    def test_word_and_pair_motions_search_and_save(self):
        saved = []
        editor = self.make_editor(on_save=lambda: saved.append(editor.get()))
        editor.set('foo(a[0]); // )\nbar foo(a);\n')
        editor.toggle_vim()
        self.press('percent')
        self.assertEqual(editor.text.index('insert'), '1.8')
        self.press('slash', 'f', 'o', 'o', 'Return')
        self.assertEqual(editor.text.index('insert'), '2.4')
        self.press('n')
        self.assertEqual(editor.text.index('insert'), '1.0')
        self.press('w')
        self.assertEqual(editor.text.index('insert'), '1.3')
        self.press('colon', '2', 'Return')
        self.assertEqual(editor.text.index('insert'), '2.0')
        self.press('colon', 'w', 'Return')
        self.press('s', state=0x4)
        self.assertEqual(saved, [editor.get(), editor.get()])

    def test_vim_command_line_replaces_literal_text_with_undo(self):
        editor = self.editor
        editor.set('a.b a.b A.B\na.b a-b a.b\n')
        self.enabled.set(True)
        self.press('colon')
        self.assertEqual(self.root.focus_get(), editor.search_entry)
        editor.search_value.set('%s/a.b/r\\/d/gi')
        self.press('Return')
        self.assertEqual(editor.get(), 'r/d r/d r/d\nr/d a-b r/d\n')
        self.press('u')
        self.assertEqual(editor.get(), 'a.b a.b A.B\na.b a-b a.b\n')
        self.press('g', 'g')
        self.press('colon')
        editor.search_value.set('s/a.b/id/')
        self.press('Return')
        self.assertEqual(editor.get(), 'id a.b A.B\na.b a-b a.b\n')

    def test_standard_indentation_comments_and_font_size(self):
        editor = self.editor
        editor.set('  initial begin')
        editor.text.mark_set('insert', 'end-1c')
        self.press('Return', 'a')
        self.assertEqual(editor.get(), '  initial begin\n      a')
        self.press('slash', state=0x4)
        self.assertEqual(editor.get(), '  initial begin\n      // a')
        self.press('slash', state=0x4)
        self.assertEqual(editor.get(), '  initial begin\n      a')
        size = editor.font.cget('size')
        self.press('equal', state=0x4)
        self.assertEqual(editor.font.cget('size'), size+1)
        self.press('0', state=0x4)
        self.assertEqual(editor.font.cget('size'), size)

    def test_find_replace_group_and_readonly(self):
        editor = self.editor
        editor.set('wire data;\nassign data = 0;')
        self.press('h', state=0x4)
        self.assertEqual(editor.find_mode, 'replace')
        editor.search_value.set('data')
        self.assertEqual(editor.match_count.get(), '2')
        self.press('F3', 'F3')
        self.assertEqual(editor.text.index('insert'), '2.7')
        editor.replace_value.set('addr')
        editor.replace_matches(replace_all=True)
        self.assertEqual(editor.get(), 'wire addr;\nassign addr = 0;')
        self.press('Escape')
        editor.text.edit_undo()
        self.assertEqual(editor.get(), 'wire data;\nassign data = 0;')

        readonly = self.make_editor(readonly=True)
        readonly.set('logic x;', filename='check.sv')
        self.assertIn('type', readonly.text.tag_names('1.0'))
        self.enabled.set(True)
        self.press('i', 'x')
        self.assertEqual(readonly.get(), 'logic x;')
        self.assertEqual(readonly.mode.get(), '只读')
        self.press('f', state=0x4)
        readonly.search_value.set('logic')
        self.assertEqual(readonly.match_count.get(), '1')

    def test_keyboard_jump_is_visible_in_the_containing_scroll_page(self):
        from widgets import ScrollPage

        self.editor.destroy()
        self.root.geometry('600x200')
        page = ScrollPage(self.root)
        page.pack(fill='both', expand=True)
        editor = self.make_editor(page.body)
        editor.set('\n'.join('logic x{};'.format(row) for row in range(40)))
        self.enabled.set(True)
        self.press('G')
        self.assertGreater(page.canvas.yview()[0], 0)
        box = editor.text.dlineinfo('insert')
        bottom = editor.text.winfo_rooty() + box[1] + box[3]
        self.assertLessEqual(bottom, page.canvas.winfo_rooty() + page.canvas.winfo_height())


if __name__ == '__main__':
    unittest.main()
