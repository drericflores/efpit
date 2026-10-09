"""
Regression tests for the Ef-Reindenter engines.

Runs headless (tkinter is stubbed), so it works in CI:
    python -m unittest test_preindent -v
"""
import ast
import sys
import types
import unittest

for _m in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules["tkinter"].filedialog = sys.modules["tkinter.filedialog"]
sys.modules["tkinter"].messagebox = sys.modules["tkinter.messagebox"]
sys.modules["tkinter"].ttk = sys.modules["tkinter.ttk"]

import preindent as P  # noqa: E402

App = P.PythonReindenterApp
app = object.__new__(App)


def indent(code, spaces=4, reset=False):
    c = App._detab(App._normalize_newlines(code), spaces)
    c = app._reindent_only(c, spaces, reset=reset)
    return App._strip_trailing_whitespace(c).rstrip() + "\n"


def pep8(code, spaces=4):
    c = App._detab(App._normalize_newlines(code), spaces)
    c = App._strip_trailing_whitespace(c)
    c = app._fix_spacing_tokenized(c)
    c = App._enforce_blank_lines(c)
    c = app._reindent_only(c, spaces)
    c = app._wrap_long_lines_tokenized(c, 79, 72)
    return App._strip_trailing_whitespace(c).rstrip() + "\n"


def same_ast(a, b):
    return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))


VALID = '''\
import os


class A:
    """Doc

    kept   exactly
    """

    def f(self, x):
        # comment
        if x:
            return [
                1,
                2,
            ]
        elif x is None:
            return None
        else:
            try:
                pass
            except ValueError:
                pass
            finally:
                os.getcwd()
        total = foo(1,
                    2)  # trailing
        return total


raise SystemExit
'''


class IndentEngine(unittest.TestCase):
    def test_valid_code_is_unchanged(self):
        self.assertEqual(indent(VALID), VALID)

    def test_other_indent_widths_and_tabs(self):
        # Convert only the *leading* 4-space units (docstring text, whose
        # content is part of the program, is skipped).
        def restyle(unit):
            out, in_doc = [], False
            for line in VALID.split("\n"):
                if in_doc or '"""' in line:
                    out.append(line)
                    if line.count('"""') == 1:
                        in_doc = not in_doc
                    continue
                n = len(line) - len(line.lstrip(" "))
                out.append(unit * (n // 4) + " " * (n % 4) + line.lstrip(" "))
            return "\n".join(out)

        for unit in ("  ", "\t", " " * 8):
            out = indent(restyle(unit))
            self.assertTrue(same_ast(out, VALID), out)

    def test_comments_are_kept(self):
        out = indent("def f():\n# note\nx = 1  # t\nreturn x\n")
        self.assertIn("# note", out)
        self.assertIn("# t", out)

    def test_over_indented_line_does_not_drift(self):
        out = indent("def f():\n    a = 1\n            b = 2\n    c = 3\n    return c\n")
        self.assertEqual(out, "def f():\n    a = 1\n    b = 2\n    c = 3\n    return c\n")

    def test_flat_code_is_healed(self):
        src = ("class A:\ndef f(self, x):\nif x:\nreturn 1\nelse:\nreturn 2\n"
               "def g(self):\ntry:\npass\nexcept E:\npass\n")
        out = indent(src)
        ast.parse(out)
        self.assertIn("    def g(self):", out)
        self.assertIn("        else:", out)

    def test_reset_mode_ignores_existing_indent(self):
        src = "def f(x):\n            if x:\n  return 1\n        else:\n   return 2\n"
        out = indent(src, reset=True)
        self.assertEqual(out, "def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n")

    def test_string_interiors_untouched(self):
        src = 'def f():\n    s = """a\n  b\n\tc"""\n    return s\n'
        out = indent(src)
        self.assertIn('"""a\n  b\n\tc"""', out)


class Formatter(unittest.TestCase):
    def test_pep8_preserves_meaning_and_strings(self):
        src = (
            "import os,sys\n"
            "def f(a,b = 1,*args,**kw):\n"
            "    x=a+b*2-  -1\n"
            "    s = \"a,b  c\" + 'x=1'   #c\n"
            "    t = lambda q=1:q+1\n"
            "    print(a, end = '', file = sys.stderr)\n"
            "    return f\"{x = }\", x[1 : 2]\n"
            "class A :\n"
            "    def m(self): pass\n"
            "    def n(self):\n"
            '        """D\n'
            "def not_code\n"
            "        # not a comment\n"
            '        """\n'
        )
        out = pep8(src)
        self.assertTrue(same_ast(src, out), out)
        self.assertIn("def f(a, b=1, *args, **kw):", out)
        self.assertIn('"a,b  c"', out)
        self.assertIn("# c", out)

    def test_idempotent(self):
        once = pep8(VALID)
        self.assertEqual(pep8(once), once)


class RefactorTools(unittest.TestCase):
    def test_boolean_return_keeps_indent_and_skips_elif(self):
        src = ("def f(x):\n    if x > 1:\n        return True\n    else:\n        return False\n\n"
               "def g(a):\n    if a == 1:\n        return 1\n    elif a == 2:\n"
               "        return True\n    else:\n        return False\n")
        new, n = app._simplify_boolean_returns_text(src)
        self.assertEqual(n, 1)
        self.assertIn("    return bool(x > 1)", new)
        ast.parse(new)

    def test_fstring_conversion_is_exact(self):
        src = ('name = "n"\nn = 3\na = "{0} and {1}".format(name, n)\n'
               'c = "%.2f items" % n\nd = "%s-%s" % (name, n)\ne = "{{x}} {}".format(name)\n')
        new, _ = app._convert_fstrings_text(src)
        ns_old, ns_new = {}, {}
        exec(src, ns_old)
        exec(new, ns_new)
        for k in "acde":
            self.assertEqual(ns_old[k], ns_new[k], k)
        self.assertIn('"%.2f items" % n', new)    # lossy conversion is skipped

    def test_unused_imports_only_top_level_and_multiline_safe(self):
        src = ("import os, sys\ntry:\n    import json\nexcept ImportError:\n    json = None\n"
               "from typing import (\n    List,\n    Dict,\n)\n__all__ = ['Dict']\nprint(sys.argv)\n")
        new, removed = app._unused_import_edits(src)
        self.assertEqual(set(removed), {"os", "List"})
        self.assertIn("import json", new)
        ast.parse(new)

    def test_organize_imports_keeps_multiline_and_later_imports(self):
        src = ("import sys\nfrom collections import (\n    OrderedDict,\n    defaultdict,\n)\n"
               "import os\nx = 1\nimport re\n")
        out = app._reorder_top_level_imports(src)
        ast.parse(out)
        self.assertEqual(out.count("import re"), 1)
        self.assertLess(out.index("import os"), out.index("import sys"))
        self.assertIn("    OrderedDict,\n    defaultdict,\n)", out)


if __name__ == "__main__":
    unittest.main()
