"""
Ef Reindenter / Python Formatter — Unified Strong Edition
Version 3.2-B — by Dr. Eric O. Flores (Ef-brand)

Features:
• AST-assisted validation and safe transformations
• Stable indentation reconstruction engine
• Heuristic block repair for badly-indented code
• PEP 8 formatting pipeline (tokenized)
• Organize imports (stdlib / third-party / local grouping)
• Remove unused imports
• Simplify boolean-return patterns
• Convert % and .format to f-strings (safe subset)
• Token-safe operator and keyword spacing
• Long-line wrapping (tokens + brackets scanning)
• Multi-line string awareness
• Parenthesis-aware hanging indentation
• Tkinter GUI with structure tree navigation
• External formatter integration (ruff / black / autopep8)
• Project settings support via pyproject.toml

GPL-3 License
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import io
import keyword
import os
import re
import string
import subprocess
import sys
import tempfile
import textwrap
import tokenize
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from typing import Iterable, List, Optional, Tuple, Dict, Set

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore


# -----------------------------
# Utility: Standard library set
# -----------------------------
try:  # Python ≥3.10
    STDLIB_NAMES: Set[str] = set(sys.stdlib_module_names)  # type: ignore[attr-defined]
except Exception:
    STDLIB_NAMES = {
        "sys", "os", "re", "math", "json", "pathlib", "itertools", "functools",
        "collections", "subprocess", "typing", "asyncio", "dataclasses", "datetime",
        "time", "logging", "argparse", "shutil", "hashlib", "inspect", "tokenize",
        "token", "io", "ast", "site", "importlib",
    }

BUILTIN_NAMES: Set[str] = set(dir(builtins))


def _classify_top_name(name: str) -> str:
    if name.startswith('.'):
        return 'local'
    root = name.split('.')[0]
    if root in STDLIB_NAMES or root in BUILTIN_NAMES:
        return 'stdlib'
    try:
        spec = importlib.util.find_spec(root)
    except Exception:
        spec = None
    if spec and spec.origin:
        origin = (spec.origin or "").lower()
        if "site-packages" in origin or "dist-packages" in origin:
            return 'thirdparty'
        if origin == 'built-in' or ("python" in origin and "lib" in origin and "site-packages" not in origin):
            return 'stdlib'
    return 'local'


@dataclass(order=True)
class ImportLine:
    group: str
    text: str
    key: str


@dataclass
class Settings:
    line_length: int = 79
    comment_width: int = 72
    quote_style: str = "auto"  # auto | single | double


# -----------------------------
# Main Application
# -----------------------------
class PythonReindenterApp:
    """GUI application for PEP 8 formatting + light refactoring."""

    def __init__(self, root):
        self.root = root
        self.root.title("Ef Reindenter / Python Formatter v3.2-B")

        self.filename: Optional[str] = None
        self.indentation_applied = False
        self.version = "3.2-B"
        self.indent_spaces_var = tk.IntVar(value=4)
        self.wrap_lines_var = tk.BooleanVar(value=True)
        self.settings = Settings()

        self._load_project_settings()

        # UI: Paned window with structure tree + editor
        self.pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.sidebar = ttk.Frame(self.pane, width=260)
        self.pane.add(self.sidebar, weight=0)
        self.editor_frame = ttk.Frame(self.pane)
        self.pane.add(self.editor_frame, weight=1)
        self.pane.pack(expand=True, fill="both")

        self.create_menu()

        # Structure tree
        self.tree = ttk.Treeview(self.sidebar, columns=("line",), show="tree")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Editor
        self.text_display = tk.Text(
            self.editor_frame,
            wrap="none",
            state="disabled",
            font=("Courier", 12),
        )
        self.text_display.pack(expand=True, fill="both")

        # Status
        self.status_label = tk.Label(self.root, text="Ready", anchor="w")
        self.status_label.pack(fill="x")

    # --------------- UI ---------------
    def create_menu(self):
        menubar = tk.Menu(self.root)

        # File
        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Load", command=self.load_file)
        self.file_menu.add_command(label="Save", command=self.save_file, state="disabled")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=self.file_menu)

        # Edit
        edit_menu = tk.Menu(menubar, tearoff=0)
        indent_menu = tk.Menu(edit_menu, tearoff=0)
        indent_menu.add_radiobutton(
            label="2 Spaces",
            variable=self.indent_spaces_var,
            value=2,
            command=self._status_indent_setting,
        )
        indent_menu.add_radiobutton(
            label="4 Spaces",
            variable=self.indent_spaces_var,
            value=4,
            command=self._status_indent_setting,
        )
        edit_menu.add_cascade(label="Set Indent Spaces", menu=indent_menu)
        edit_menu.add_checkbutton(
            label="Wrap long lines to 79 chars",
            variable=self.wrap_lines_var,
            onvalue=True,
            offvalue=False,
        )
        edit_menu.add_command(label="Apply Indent", command=self.apply_indent_from_menu)
        edit_menu.add_command(
            label="Reset && Recalculate Indent", command=self.reset_and_recalculate_indent
        )
        edit_menu.add_separator()
        edit_menu.add_command(label="Format (PEP 8)", command=self.apply_pep8_format)
        edit_menu.add_command(label="Organize Imports (PEP 8)", command=self.organize_imports)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # Refactor
        ref_menu = tk.Menu(menubar, tearoff=0)
        ref_menu.add_command(label="Remove Unused Imports", command=self.remove_unused_imports)
        ref_menu.add_command(label="Simplify Boolean Returns", command=self.simplify_boolean_returns)
        ref_menu.add_command(label="Convert to f-strings (safe)", command=self.convert_to_fstrings)
        menubar.add_cascade(label="Refactor", menu=ref_menu)

        # Tools (external formatters)
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="External Formatters…", command=self.run_external_formatter)
        tools_menu.add_command(label="Settings…", command=self.open_settings_dialog)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _status_indent_setting(self):
        self.set_status(f"Indentation = {self.indent_spaces_var.get()} spaces")

    # --------------- Settings ---------------
    def _load_project_settings(self):
        # Look for pyproject.toml next to opened file (later), or current cwd now
        cfg = None
        search_dirs = [os.getcwd()]
        if self.filename:
            search_dirs.insert(0, os.path.dirname(self.filename))

        for d in search_dirs:
            path = os.path.join(d, "pyproject.toml")
            if os.path.isfile(path) and tomllib is not None:
                try:
                    with open(path, "rb") as f:
                        data = tomllib.load(f)
                    tool = data.get("tool", {}).get("pep8fmt", {})
                    if isinstance(tool, dict):
                        cfg = tool
                        break
                except Exception:
                    pass
        if cfg:
            self.settings.line_length = int(cfg.get("line-length", self.settings.line_length))
            self.settings.comment_width = int(cfg.get("comment-width", self.settings.comment_width))
            self.settings.quote_style = str(cfg.get("quote-style", self.settings.quote_style))

    def open_settings_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("Settings")
        tk.Label(top, text="Max line length:").grid(row=0, column=0, sticky="w")
        e_len = tk.Entry(top)
        e_len.insert(0, str(self.settings.line_length))
        e_len.grid(row=0, column=1, padx=6, pady=4)
        tk.Label(top, text="Comment width:").grid(row=1, column=0, sticky="w")
        e_cw = tk.Entry(top)
        e_cw.insert(0, str(self.settings.comment_width))
        e_cw.grid(row=1, column=1, padx=6, pady=4)
        tk.Label(top, text="Quote style (auto/single/double):").grid(row=2, column=0, sticky="w")
        e_q = tk.Entry(top)
        e_q.insert(0, self.settings.quote_style)
        e_q.grid(row=2, column=1, padx=6, pady=4)

        def ok():
            try:
                self.settings.line_length = int(e_len.get() or self.settings.line_length)
                self.settings.comment_width = int(e_cw.get() or self.settings.comment_width)
                self.settings.quote_style = (e_q.get() or self.settings.quote_style).strip().lower()
                top.destroy()
                self.set_status("Settings updated")
            except Exception as e:
                messagebox.showerror("Invalid settings", str(e))

        tk.Button(top, text="OK", command=ok).grid(row=3, column=0, columnspan=2, pady=6)
        top.transient(self.root)
        top.grab_set()
        self.root.wait_window(top)

    # --------------- File ops ---------------
    def load_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Python Files", "*.py"), ("All Files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
            self.filename = path
            self._load_project_settings()  # Re-load settings based on new file location
            self.display_code(code)
            self.indentation_applied = False
            self.update_save_state()
            self.set_status(f"Loaded: {path}")
            self._refresh_structure_tree(code)
        except Exception as e:
            messagebox.showerror("Error Loading File", f"Could not read file:\n{e}")
            self.set_status("Error loading file")

    def save_file(self):
        if not self.filename:
            path = filedialog.asksaveasfilename(
                defaultextension=".py",
                filetypes=[("Python Files", "*.py")],
            )
            if not path:
                return
            self.filename = path
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                f.write(self._get_buffer().rstrip() + "\n")
            messagebox.showinfo("Save", f"File saved: {self.filename}")
            self.set_status(f"Saved: {self.filename}")
            self.indentation_applied = False
            self.update_save_state()
        except Exception as e:
            messagebox.showerror("Error Saving File", f"Could not save file:\n{e}")
            self.set_status("Error saving file")

    # --------------- Buffer helpers ---------------
    def display_code(self, code: str):
        self.text_display.config(state="normal")
        self.text_display.delete("1.0", tk.END)
        self.text_display.insert("1.0", code)
        self.text_display.config(state="disabled")
        self._refresh_structure_tree(code)

    def _get_buffer(self) -> str:
        self.text_display.config(state="normal")
        code = self.text_display.get("1.0", tk.END)
        self.text_display.config(state="disabled")
        return code

    # --------------- Structure pane ---------------
    def _refresh_structure_tree(self, code: str):
        for i in self.tree.get_children():
            self.tree.delete(i)
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return

        root = self.tree.insert(
            "",
            "end",
            text=os.path.basename(self.filename or "<buffer>"),
            values=(1,),
        )

        def walk(parent, node):
            for n in getattr(node, "body", []):
                if isinstance(n, ast.ClassDef):
                    ci = self.tree.insert(
                        parent,
                        "end",
                        text=f"class {n.name}",
                        values=(getattr(n, "lineno", 1),),
                    )
                    walk(ci, n)
                elif isinstance(n, ast.FunctionDef):
                    self.tree.insert(
                        parent,
                        "end",
                        text=f"def {n.name}()",
                        values=(getattr(n, "lineno", 1),),
                    )
                elif isinstance(n, ast.AsyncFunctionDef):
                    self.tree.insert(
                        parent,
                        "end",
                        text=f"async def {n.name}()",
                        values=(getattr(n, "lineno", 1),),
                    )

        walk(root, tree)

    def _on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        line = self.tree.set(sel[0], "line")
        try:
            lno = int(line)
        except Exception:
            return
        self._highlight_line(lno)

    def _highlight_line(self, lno: int):
        self.text_display.config(state="normal")
        index = f"{lno}.0"
        self.text_display.see(index)
        self.text_display.tag_remove("hi", "1.0", tk.END)
        self.text_display.tag_add("hi", index, f"{lno}.0 lineend")
        self.text_display.tag_config("hi", background="#ffeeaa")
        self.text_display.config(state="disabled")

    # --------------- Indentation (logic + heuristic repair) ---------------
    def apply_indent_from_menu(self):
        """
        Applies heuristic block repair + reindent.

        NOTE:
        This does NOT require the code to be syntactically valid.
        It will best-effort normalize indentation even if the AST
        parser would fail.
        """
        code = self._get_buffer()
        spaces = self.indent_spaces_var.get()
        code = self._normalize_newlines(code)
        code = self._detab(code, spaces)
        code = self._reindent_only(code, spaces)
        code = self._strip_trailing_whitespace(code).rstrip() + "\n"

        self.display_code(code)
        self.indentation_applied = True
        self.update_save_state()
        self.set_status("Indentation applied (structural repair + reindent)")

    def reset_and_recalculate_indent(self):
        """
        Discards ALL existing indentation, then rebuilds it line by line from
        the structure of the preceding lines. Use this when the original
        indentation of a pasted/broken script cannot be trusted.
        """
        code = self._get_buffer()
        spaces = self.indent_spaces_var.get()
        code = self._normalize_newlines(code)
        code = self._detab(code, spaces)
        code = self._reindent_only(code, spaces, reset=True)
        code = self._strip_trailing_whitespace(code).rstrip() + "\n"
        self.display_code(code)
        self.indentation_applied = True
        self.update_save_state()
        self.set_status("Indentation reset and recalculated from structure")

    # Keywords that continue a compound statement, and which openers they
    # may legally attach to.
    _CONTINUATION_KW = {
        "elif": {"if", "elif"},
        "else": {"if", "elif", "for", "while", "try", "except"},
        "except": {"try", "except"},
        "finally": {"try", "except", "else"},
    }

    _BODY_ENDERS = {"return", "raise", "break", "continue", "pass"}

    _COMPOUND_KW = {
        "if", "elif", "else", "for", "while", "try", "except", "finally",
        "with", "def", "class",
    }

    @staticmethod
    def _scan_code_line(line: str, triple: Optional[str], depth: int):
        """
        Scan one physical line, tracking string/bracket state.

        Returns (code, triple, depth) where `code` is the line with string
        contents blanked out and comments removed (so keyword and trailing
        colon checks cannot be fooled by text inside strings or comments),
        `triple` is the open triple-quote delimiter (or None) and `depth`
        is the open-bracket depth after the line.
        """
        i, n = 0, len(line)
        out: List[str] = []
        while i < n:
            if triple:
                j = i
                while j < n:
                    if line[j] == "\\":
                        j += 2
                        continue
                    if line.startswith(triple, j):
                        break
                    j += 1
                if j >= n:
                    return "".join(out), triple, depth
                i = j + 3
                triple = None
                continue
            c = line[i]
            if c == "#":
                break
            if c in "\"'":
                if line.startswith(c * 3, i):
                    triple = c * 3
                    out.append('""')
                    i += 3
                    continue
                j = i + 1
                while j < n and line[j] != c:
                    j += 2 if line[j] == "\\" else 1
                out.append('""')
                i = j + 1
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth = max(0, depth - 1)
            out.append(c)
            i += 1
        return "".join(out), triple, depth

    def _isolated_dedent_fixes(self, lines: List[str]):
        """
        Find single statements whose indentation drops below BOTH neighbours
        without any block opener explaining it, e.g.

                a = 1
            b = 2        <- stray dedent
                c = 3

        and return {line_index: indent_to_use} treating them as part of the
        surrounding block instead of closing it.
        """
        stmts: List[Tuple[int, int, bool, bool]] = []   # idx, indent, opens, is_cont_kw
        triple: Optional[str] = None
        depth = 0
        mid = False
        for i, line in enumerate(lines):
            st = line.strip()
            if triple is not None or mid:
                code, triple, depth = self._scan_code_line(line, triple, depth)
                if triple is None and depth == 0 and not code.rstrip().endswith("\\"):
                    mid = False
                    if stmts:
                        idx, ind, _o, kwc = stmts[-1]
                        stmts[-1] = (idx, ind, code.rstrip().endswith(":"), kwc)
                continue
            if not st or st.startswith("#"):
                continue
            code, triple, depth = self._scan_code_line(line, None, 0)
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", code)
            kwc = bool(m and m.group(1) in self._CONTINUATION_KW)
            stmts.append((i, len(line) - len(line.lstrip(" ")), code.rstrip().endswith(":"), kwc))
            mid = triple is not None or depth > 0 or code.rstrip().endswith("\\")
        fixes: Dict[int, int] = {}
        # opener line -> indent of the statement after it (its first body line)
        body_hint: Dict[int, int] = {}
        for k in range(len(stmts) - 1):
            if stmts[k][2]:
                body_hint[stmts[k][0]] = stmts[k + 1][1]
        self._body_hint = body_hint
        for k in range(1, len(stmts) - 1):
            (_, ip, op, _), (ik, ik_ind, ok_, kk), (_, iq, _, kq) = stmts[k - 1], stmts[k], stmts[k + 1]
            if op or kk or kq:
                continue
            if ok_:
                # dedented block opener whose body stays deeper than the
                # previous line -> the opener was dedented by mistake
                if ik_ind < ip and iq > ip:
                    fixes[ik] = ip
                continue
            if ik_ind < ip and ik_ind < iq:
                fixes[ik] = min(ip, iq)
        return fixes

    def _reindent_only(self, s: str, spaces: int, reset: bool = False) -> str:
        """
        Re-indent code to `spaces` per level, without needing valid syntax.

        Works statement by statement:
          * the original indentation decides each statement's nesting level
            (stray over-indents stay at the enclosing level);
          * a line after a block opener (a statement ending in ':') is that
            block's body, even if it was not indented in the source;
          * elif/else/except/finally are re-attached to a compatible opener
            when their indentation is wrong, and a def/class/decorator at
            the same column as an earlier def/class becomes its sibling;
          * continuation lines keep their position relative to the first
            line of their statement; comment-only lines are kept and placed
            at the level their indentation implies;
          * lines inside multi-line strings are never touched.

        With reset=True the original leading whitespace of every line is
        discarded first, so each line's indent is recalculated purely from
        the lines before it (':' opens a level, return/raise/break/continue/
        pass close one, else/elif/except/finally re-attach, methods go under
        their class).
        """
        lines = s.strip("\n").split("\n")
        adj: Dict[int, int] = {}
        hints: Dict[int, int] = {}
        if not reset:
            adj = self._isolated_dedent_fixes(lines)
            hints = getattr(self, "_body_hint", {})
        if reset:
            st_triple: Optional[str] = None
            st_depth = 0
            flat: List[str] = []
            for ln in lines:
                interior = st_triple is not None
                _c, st_triple, st_depth = self._scan_code_line(ln, st_triple, st_depth)
                flat.append(ln if interior else ln.strip())
            lines = flat
        out: List[str] = []

        # (orig_indent, level, forced): `forced` marks a body that was not
        # indented in the source and was inferred from the preceding ':'.
        # entry = [orig_indent, level, forced, statements_seen]
        stack: List[list] = [[0, 0, False, 1]]
        openers: List[Tuple[str, int, int]] = []      # (keyword, level, orig_indent)

        triple: Optional[str] = None
        depth = 0
        mid = False                  # inside a multi-line statement
        prev_opens_block = False
        prev_level = 0
        prev_indent = 0
        prev_kw = ""

        stmt_kw = ""
        stmt_level = 0
        stmt_indent = 0
        stmt_new = 0
        last_code = ""
        prev_line_opens_bracket = False

        def finish_statement():
            nonlocal prev_opens_block, prev_level, prev_indent, prev_kw
            prev_opens_block = last_code.rstrip().endswith(":")
            prev_level, prev_indent, prev_kw = stmt_level, stmt_indent, stmt_kw
            # One-line compounds ("if x: y", "else: z") never open a block
            # but still count as openers for elif/else/except matching.
            if prev_opens_block or stmt_kw in self._COMPOUND_KW:
                openers.append((stmt_kw, stmt_level, stmt_indent))

        for line_no, line in enumerate(lines):
            stripped = line.strip()

            # ---- interior of a multi-line string: leave untouched ----
            if triple is not None:
                out.append(line)
                last_code, triple, depth = self._scan_code_line(line, triple, depth)
                if triple is None and depth == 0 and not last_code.rstrip().endswith("\\"):
                    mid = False
                    finish_statement()
                continue

            if not stripped:
                out.append("")
                continue

            indent = len(line) - len(line.lstrip(" "))
            indent = adj.get(line_no, indent) if not mid else indent
            if not mid:
                # A block's body can never be shallower than its opener: if
                # it is, the opener was over-indented -> snap it back.
                hint = hints.get(line_no)
                if hint is not None and indent > hint and not prev_opens_block:
                    cands = [e[0] for e in stack if e[0] < hint]
                    indent = max(cands) if cands else 0

            # ---- continuation of a multi-line statement ----
            if mid:
                rel = indent - stmt_indent
                if rel <= 0:
                    rel = 0 if stripped[0] in ")]}" else spaces
                out.append(" " * (stmt_new + rel) + stripped)
                if stripped.startswith("#"):
                    continue
                last_code, triple, depth = self._scan_code_line(line, None, depth)
                if triple is None and depth == 0 and not last_code.rstrip().endswith("\\"):
                    mid = False
                    finish_statement()
                continue

            # ---- comment-only line outside a statement ----
            if stripped.startswith("#"):
                if prev_opens_block:
                    lvl = prev_level + 1
                else:
                    k = len(stack) - 1
                    while k > 0 and stack[k][0] > indent:
                        k -= 1
                    lvl = stack[k][1]
                out.append(" " * (lvl * spaces) + stripped)
                continue

            # ---- start of a new statement ----
            code, triple, depth = self._scan_code_line(line, None, 0)
            m = re.match(r"\s*(@|[A-Za-z_][A-Za-z0-9_]*)", code)
            token = m.group(1) if m else ""
            kw = token
            if token == "async":
                m2 = re.match(r"\s*async\s+([A-Za-z_][A-Za-z0-9_]*)", code)
                kw = m2.group(1) if m2 else token

            if prev_opens_block:
                level = prev_level + 1
                stack.append([indent, level,
                              2 if indent < prev_indent else int(indent == prev_indent), 0])
            else:
                if kw in ("def", "class", "@"):
                    while len(stack) > 1 and stack[-1][2] == 2:
                        stack.pop()
                popped = None
                popped_all: List[list] = []
                while len(stack) > 1 and stack[-1][0] > indent:
                    popped = stack.pop()
                    popped_all.append(popped)
                # Misaligned dedent (between two known indents).
                if popped is not None and stack[-1][0] < indent:
                    body_ind = hints.get(line_no)
                    if popped[3] <= 1:
                        # the block's lone first line was the odd one out:
                        # this line shows the block's real indent
                        stack.append([indent, popped[1], popped[2], 1])
                    elif body_ind is not None:
                        # A block opener: its body must be deeper than it, so
                        # it belongs to the deepest known block shallower than
                        # its own first body line.
                        known = stack + popped_all[::-1]
                        fits = [e for e in known if e[0] < body_ind]
                        if fits:
                            keep_ind = max(e[0] for e in fits)
                            stack[:] = [e for e in known if e[0] <= keep_ind]
                    elif popped[0] - indent < indent - stack[-1][0]:
                        stack.append(popped)     # clearly closer to the deeper block

                # Accepted stray over-indent: remember its column as an alias of
                # the enclosing level so later lines at that column stay there.
                if stack[-1][0] < indent:
                    stack.append([indent, stack[-1][1], False, 0])

                # A flat body that already ended in return/raise/break/
                # continue/pass is closed by the next statement.
                if (
                    len(stack) > 1
                    and stack[-1][2]
                    and stack[-1][0] == indent
                    and prev_kw in self._BODY_ENDERS
                    and prev_level == stack[-1][1]
                    and token not in self._CONTINUATION_KW
                ):
                    stack.pop()
                level = stack[-1][1]

                # def/class/decorator at the same column as an earlier
                # def/class -> sibling of that def, or a member of that class
                # (repairs flattened methods)
                if kw in ("def", "class", "@"):
                    for okw, olv, oind in reversed(openers):
                        if okw in ("def", "class") and oind == indent:
                            level = olv + 1 if (okw == "class" and level > olv) else olv
                            break

                # elif/else/except/finally -> attach to a compatible opener
                if token in self._CONTINUATION_KW:
                    compat = self._CONTINUATION_KW[token]
                    at_level = [o for o in openers if o[1] == level]
                    if not (at_level and at_level[-1][0] in compat):
                        for okw, olv, _ in reversed(openers):
                            if okw in compat:
                                level = olv
                                break

                while len(stack) > 1 and stack[-1][1] > level:
                    stack.pop()

            stack[-1][3] += 1
            while openers and openers[-1][1] >= level:
                openers.pop()

            new_indent = level * spaces
            out.append(" " * new_indent + stripped)

            stmt_kw, stmt_level, stmt_indent, stmt_new = kw, level, indent, new_indent
            last_code = code
            if triple is not None or depth > 0 or code.rstrip().endswith("\\"):
                mid = True
            else:
                finish_statement()

        return "\n".join(out)

    # --------------- PEP 8 formatting pipeline ---------------
    def apply_pep8_format(self):
        code = self._get_buffer()
        ok, err = self._ast_valid(code)
        if not ok:
            messagebox.showwarning("Syntax error", f"Parsing failed; not formatting.\n\n{err}")
            return
        spaces = self.indent_spaces_var.get()
        code = self._normalize_newlines(code)
        code = self._detab(code, spaces)
        code = self._strip_trailing_whitespace(code)
        code = self._fix_spacing_tokenized(code)
        code = self._enforce_blank_lines(code)
        code = self._reindent_only(code, spaces)
        if self.wrap_lines_var.get():
            code = self._wrap_long_lines_tokenized(
                code,
                width=self.settings.line_length,
                comment_width=self.settings.comment_width,
            )
        code = self._strip_trailing_whitespace(code).rstrip() + "\n"

        # Safety net: formatting must never change what the program means.
        original = self._get_buffer()
        try:
            same = ast.dump(ast.parse(original)) == ast.dump(ast.parse(code))
        except SyntaxError:
            same = False
        if not same:
            messagebox.showwarning(
                "Formatting skipped",
                "The formatted result did not match the original program "
                "structure, so nothing was changed.",
            )
            return

        self.display_code(code)
        self.indentation_applied = True
        self.update_save_state()
        self.set_status("PEP 8 formatting applied")

    # --------------- Import organization ---------------
    def organize_imports(self):
        code = self._get_buffer()
        ok, err = self._ast_valid(code)
        if not ok:
            messagebox.showwarning("Syntax error", f"Parsing failed; cannot organize imports.\n\n{err}")
            return
        new_code = self._reorder_top_level_imports(code)
        if new_code != code:
            self.display_code(new_code)
            self.indentation_applied = True
            self.update_save_state()
            self.set_status("Imports organized (top-level)")
        else:
            self.set_status("No top-level import changes detected")

    def _reorder_top_level_imports(self, code: str) -> str:
        """
        Group and sort the leading block of top-level imports.

        Works on whole statements (so multi-line ``from x import (...)``
        stays intact), keeps comments that sit directly above an import with
        it, and only touches the contiguous run of imports after the module
        docstring and any ``from __future__`` imports.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code
        lines = code.split("\n")
        body = tree.body
        i = 0
        if body and self._module_docstring_span(tree):
            i = 1
        while (
            i < len(body)
            and isinstance(body[i], ast.ImportFrom)
            and body[i].module == "__future__"
        ):
            i += 1
        region = []
        while i < len(body) and isinstance(body[i], (ast.Import, ast.ImportFrom)):
            region.append(body[i])
            i += 1
        if not region:
            return code
        starts = [n.lineno for n in region]
        if len(set(starts)) != len(starts) or any(
            n.end_lineno != n.lineno and False for n in region
        ):
            return code  # "import a; import b" on one line: leave alone

        floor = (body[i - len(region) - 1].end_lineno if i - len(region) > 0 else 0)
        units = []
        for node in region:
            first = node.lineno
            while first - 2 >= floor and lines[first - 2].lstrip().startswith("#") \
                    and first - 1 > floor:
                first -= 1
            floor = node.end_lineno
            text = lines[first - 1: node.end_lineno]
            if isinstance(node, ast.Import):
                root = node.names[0].name
                kind, mod = 0, node.names[0].name.lower()
                group = _classify_top_name(root)
            else:
                mod = ("." * node.level) + (node.module or "")
                kind = 1
                group = "local" if node.level else _classify_top_name(node.module or "")
                mod = mod.lower()
            units.append((group, kind, mod, "\n".join(text), first, node.end_lineno))

        start_line = units[0][4]
        end_line = units[-1][5]
        seen = set()
        blocks: Dict[str, List[Tuple[int, str, str]]] = {"stdlib": [], "thirdparty": [], "local": []}
        for group, kind, mod, text, _a, _b in units:
            sig = re.sub(r"\s+", " ", text)
            if sig in seen:
                continue
            seen.add(sig)
            blocks[group].append((kind, mod, text))
        new_block: List[str] = []
        for g in ("stdlib", "thirdparty", "local"):
            if not blocks[g]:
                continue
            if new_block:
                new_block.append("")
            for _k, _m, text in sorted(blocks[g], key=lambda b: (b[0], b[1], b[2])):
                new_block.extend(text.split("\n"))

        head = lines[: start_line - 1]
        tail = lines[end_line:]
        if head and head[-1].strip():
            head.append("")
        if tail and tail[0].strip():
            tail.insert(0, "")
        return "\n".join(head + new_block + tail)

    def _module_docstring_span(self, tree: ast.Module) -> Optional[Tuple[int, int]]:
        if not tree.body:
            return None
        node0 = tree.body[0]
        if (
            isinstance(node0, ast.Expr)
            and isinstance(getattr(node0, "value", None), ast.Constant)
            and isinstance(node0.value.value, str)
        ):
            return (node0.lineno, getattr(node0, "end_lineno", node0.lineno))
        return None

    # --------------- Tokenize-aware whitespace fixes ---------------
    @staticmethod
    def _normalize_newlines(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _detab(s: str, spaces: int) -> str:
        """Expand tabs in leading indentation only; string contents are kept."""
        out: List[str] = []
        triple: Optional[str] = None
        depth = 0
        for line in s.split("\n"):
            interior = triple is not None
            _c, triple, depth = PythonReindenterApp._scan_code_line(line, triple, depth)
            if interior or "\t" not in line:
                out.append(line)
                continue
            body = line.lstrip(" \t")
            lead = line[: len(line) - len(body)]
            out.append(lead.expandtabs(spaces) + body)
        return "\n".join(out)

    @staticmethod
    def _strip_trailing_whitespace(s: str) -> str:
        """rstrip each line unless it ends inside a multi-line string."""
        out: List[str] = []
        triple: Optional[str] = None
        depth = 0
        for line in s.split("\n"):
            _c, triple, depth = PythonReindenterApp._scan_code_line(line, triple, depth)
            out.append(line if triple is not None else line.rstrip())
        return "\n".join(out)

    def _fix_spacing_tokenized(self, s: str) -> str:
        """
        PEP 8 whitespace normalisation (E2xx) driven by the tokenizer.

        Only the gaps *between* tokens on one line are edited, and inline
        comments get two spaces plus a "# " prefix. String literals,
        f-strings and comment bodies are never modified.
        """
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(s).readline))
        except (tokenize.TokenError, SyntaxError):
            return s

        T = tokenize
        fs_start = getattr(T, "FSTRING_START", None)
        fs_end = getattr(T, "FSTRING_END", None)
        skip_types = {T.NEWLINE, T.NL, T.INDENT, T.DEDENT, T.ENDMARKER}

        raw = s.split("\n")
        offsets = [0]
        for ln in raw:
            offsets.append(offsets[-1] + len(ln) + 1)

        def idx(pos):
            return offsets[pos[0] - 1] + pos[1]

        COMPARE = {"==", "!=", "<", ">", "<=", ">=", "->", ":=", "<>",
                   "+=", "-=", "*=", "/=", "//=", "%=", "**=", "@=", "&=",
                   "|=", "^=", ">>=", "<<="}
        ARITH = {"+", "-", "*", "/", "//", "%", "**", "@", "&", "|", "^", "<<", ">>"}
        OPEN, CLOSE = "([{", ")]}"

        def is_kw(t):
            return t.type == T.NAME and keyword.iskeyword(t.string) and \
                t.string not in ("True", "False", "None")

        def operand_end(t):
            if t is None:
                return False
            if t.type == T.NAME:
                return not is_kw(t)
            if t.type in (T.NUMBER, T.STRING) or t.type == fs_end:
                return True
            return t.type == T.OP and t.string in CLOSE + "..."

        frames = [{"ch": None, "annot": False, "lam": False}]
        edits: List[Tuple[int, int, str]] = []
        prev = None
        prev_cls = None
        prev_colon_slice = False
        fdepth = 0

        for tok in toks:
            tt, ts = tok.type, tok.string

            if tt in skip_types:
                if tt in (T.NEWLINE, T.NL):
                    prev = None
                    prev_cls = None
                if tt == T.NEWLINE:
                    frames[0]["annot"] = False
                    frames[0]["lam"] = False
                continue

            if tt == T.COMMENT:
                if prev is not None and prev.end[0] == tok.start[0] and fdepth == 0:
                    a, b = idx(prev.end), idx(tok.start)
                    if s[a:b].strip(" \t") == "" and s[a:b] != "  ":
                        edits.append((a, b, "  "))
                body = ts[1:]
                if (body and body[0] not in " !:#\t"
                        and not (tok.start[0] == 1 and body[0] == "!")):
                    edits.append((idx(tok.start) + 1, idx(tok.start) + 1, " "))
                continue

            # ---- f-string interiors are opaque ----
            if fs_start is not None and tt == fs_start and fdepth == 0:
                pass  # gap before the f-string is ordinary code
            elif fdepth > 0 or tt == fs_end:
                if tt == fs_start:
                    fdepth += 1
                elif tt == fs_end:
                    fdepth -= 1
                    if fdepth == 0:
                        prev, prev_cls = tok, "operand"
                continue
            if fs_start is not None and tt == fs_start:
                fdepth += 1
                gap_prev = prev
            else:
                gap_prev = prev

            top = frames[-1]
            cls = "other"
            if tt == T.OP:
                if ts in OPEN:
                    cls = "open"
                elif ts in CLOSE:
                    cls = "close"
                elif ts == ",":
                    cls = "comma"
                elif ts == ";":
                    cls = "semi"
                elif ts == ":":
                    cls = "colon"
                elif ts == "=":
                    cls = "tight" if ((top["ch"] == "(" and not top["annot"]) or top["lam"]) else "sp"
                elif ts in COMPARE:
                    cls = "sp"
                elif ts in ARITH:
                    cls = "sp" if operand_end(prev) else "unary"
                elif ts == "~":
                    cls = "unary"
                elif ts == ".":
                    cls = "dot"
            if tt == T.NAME and ts == "lambda":
                top["lam"] = True

            # ---- decide gap between prev and tok ----
            if prev is not None and prev.end[0] == tok.start[0]:
                a, b = idx(prev.end), idx(tok.start)
                orig = s[a:b]
                new: Optional[str] = None
                if orig.strip(" \t") == "":
                    if cls == "tight" or prev_cls == "tight":
                        new = ""
                    elif cls == "sp" or prev_cls == "sp":
                        new = " "
                    elif prev_cls == "unary":
                        new = ""
                    elif cls == "close" or prev_cls == "open":
                        new = ""
                    elif cls in ("comma", "semi"):
                        new = ""
                    elif prev_cls in ("comma", "semi"):
                        new = " "
                    elif cls == "colon":
                        new = None if top["ch"] == "[" else ""
                    elif prev_cls == "colon":
                        new = None if prev_colon_slice else " "
                    elif is_kw(prev):
                        new = None if (cls == "dot") else " "
                    elif cls == "open" and ts in "([" and (
                        (prev.type == T.NAME and prev.string not in ("match", "case"))
                        or (prev.type == T.OP and prev.string in ")]")
                    ):
                        new = ""
                    elif cls == "dot":
                        if operand_end(prev) and prev.type != T.NUMBER:
                            new = ""
                        elif prev_cls == "dot":
                            new = ""
                    elif prev_cls == "dot":
                        new = " " if is_kw(tok) else ""
                    elif orig != "":
                        new = " "
                    if new is not None and new != orig:
                        edits.append((a, b, new))

            # ---- update context after this token ----
            if cls == "open":
                frames.append({"ch": ts, "annot": False, "lam": False})
            elif cls == "close":
                if len(frames) > 1:
                    frames.pop()
            elif cls == "comma":
                top["annot"] = False
            elif cls == "colon":
                prev_colon_slice = top["ch"] == "["
                if top["lam"]:
                    top["lam"] = False
                else:
                    top["annot"] = True

            prev, prev_cls = tok, cls

        for a, b, new in sorted(edits, key=lambda e: e[0], reverse=True):
            s = s[:a] + new + s[b:]
        return s

    @staticmethod
    def _enforce_blank_lines(s: str) -> str:
        """
        PEP 8 blank-line rules (E301/E302/E303/E305), computed from the
        lexical structure so string contents are never touched:
          * two blank lines around top-level def/class (and their decorators
            or comments sitting directly above them);
          * one blank line before nested def/class unless first in its block;
          * no more than 2 blank lines at top level, 1 inside blocks.
        """
        scan = PythonReindenterApp._scan_code_line
        lines = s.split("\n")
        # ---- classify physical lines ----
        # kind: 'raw' (string interior / continuation / blank inside brackets),
        #       'blank', 'comment', 'stmt'
        info: List[Tuple[str, int, str]] = []   # (kind, indent, first token)
        triple: Optional[str] = None
        depth = 0
        mid = False
        stmt_end_opens: Dict[int, bool] = {}
        cur_stmt = -1
        for i, line in enumerate(lines):
            st = line.strip()
            if triple is not None:
                info.append(("raw", 0, ""))
                code, triple, depth = scan(line, triple, depth)
            elif mid:
                info.append(("raw", 0, ""))
                code, triple, depth = scan(line, None, depth)
            elif not st:
                info.append(("blank", 0, ""))
                continue
            elif st.startswith("#"):
                info.append(("comment", len(line) - len(line.lstrip(" ")), ""))
                continue
            else:
                code, triple, depth = scan(line, None, 0)
                m = re.match(r"\s*(@|[A-Za-z_][A-Za-z0-9_]*)", code)
                tok = m.group(1) if m else ""
                if tok == "async":
                    tok = "def"
                info.append(("stmt", len(line) - len(line.lstrip(" ")), tok))
                cur_stmt = i
            mid = triple is not None or depth > 0 or code.rstrip().endswith("\\")
            if not mid:
                stmt_end_opens[cur_stmt] = code.rstrip().endswith(":")

        out: List[str] = []
        pending = 0                     # blank lines waiting to be placed
        prev_item: Optional[Tuple[str, int, str, bool]] = None   # kind, indent, token, opens
        last_top_def = False
        group_open = False              # inside a comment/decorator run above a def
        i = 0
        n = len(lines)

        def is_def(tok):
            return tok in ("def", "class", "@")

        while i < n:
            kind, indent, tok = info[i]
            if kind == "raw":
                out.append(lines[i])
                i += 1
                continue
            if kind == "blank":
                pending += 1
                i += 1
                continue

            # ---- an item: comment line or statement start ----
            head_def = False
            if kind == "stmt" and is_def(tok):
                head_def = True
            elif kind == "comment":
                j = i + 1
                while j < n and info[j][0] == "comment":
                    j += 1
                if j < n and info[j][0] == "stmt" and is_def(info[j][2]) \
                        and info[j][1] == indent:
                    head_def = True

            if prev_item is None:
                want = 0
            elif group_open and (kind == "stmt" or kind == "comment"):
                # continuation of a comment/decorator run directly above a def
                want = min(pending, 1) if kind == "comment" and pending else 0
            else:
                limit = 2 if indent == 0 else 1
                want = min(pending, limit)
                p_kind, p_indent, p_tok, p_opens = prev_item
                if head_def:
                    if p_opens:
                        want = 0
                    else:
                        want = 2 if indent == 0 else 1
                elif (indent == 0 and last_top_def
                      and not (p_kind == "comment" and pending == 0)
                      and kind in ("stmt", "comment")):
                    want = 2
                if p_opens and not head_def:
                    want = 0 if pending == 0 else min(pending, 1)
                    if kind == "comment" or indent > 0:
                        want = min(pending, 1) if pending else 0

            out.extend([""] * want)
            pending = 0

            if kind == "comment":
                out.append(lines[i])
                group_open = head_def or (group_open and True)
                prev_item = ("comment", indent, "", False) if prev_item is None or True else prev_item
                i += 1
                continue

            # statement: emit first line (continuations follow as 'raw')
            out.append(lines[i])
            opens = stmt_end_opens.get(i, False)
            if indent == 0:
                last_top_def = is_def(tok)
            group_open = tok == "@"
            prev_item = ("stmt", indent, tok, opens)
            i += 1

        while out and out[-1].strip() == "":
            out.pop()
        return "\n".join(out)

    def _wrap_long_lines_tokenized(self, s: str, width: int = 79, comment_width: int = 72) -> str:
        out: List[str] = []
        triple: Optional[str] = None
        depth = 0
        for line in s.split("\n"):
            started_in_string = triple is not None
            _c, triple, depth = self._scan_code_line(line, triple, depth)
            if started_in_string or triple is not None:
                out.append(line)          # never re-wrap text inside a string
                continue
            if len(line) <= width:
                out.append(line)
                continue
            if line.lstrip().startswith("#"):
                indent = re.match(r"\s*", line).group(0)
                text = line.strip()[1:].lstrip()
                wrapped = textwrap.fill(
                    text,
                    width=comment_width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                out.extend((indent + "# " + w).rstrip() for w in wrapped.split("\n"))
                continue
            if not any(ch in line for ch in "([{"):
                out.append(line)
                continue
            try:
                tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
            except tokenize.TokenError:
                out.append(line)
                continue
            breaks: List[int] = []
            level = 0
            fs_start = getattr(tokenize, "FSTRING_START", None)
            fs_end = getattr(tokenize, "FSTRING_END", None)
            in_fstring = 0
            for tok in tokens:
                ttype, tstr, start, end, _ = tok
                if fs_start is not None and ttype == fs_start:
                    in_fstring += 1
                    continue
                if fs_end is not None and ttype == fs_end:
                    in_fstring -= 1
                    continue
                if in_fstring:
                    continue
                if ttype == tokenize.OP:
                    if tstr in ("(", "[", "{"):
                        level += 1
                    elif tstr in (")", "]", "}"):
                        level = max(0, level - 1)
                    elif tstr == "," and level > 0:
                        breaks.append(self._pos_to_idx(line, end))
            if not breaks:
                out.append(line)
                continue
            indent = re.match(r"\s*", line).group(0)
            hang = indent + " " * 4
            prefix = indent
            begin = len(indent)           # absolute index into `line`
            pieces: List[str] = []
            while len(prefix) + (len(line) - begin) > width:
                limit = width - len(prefix)
                cands = [b for b in breaks if b > begin and b - begin <= limit]
                if not cands:
                    break
                cut = max(cands)
                pieces.append((prefix + line[begin:cut]).rstrip())
                begin = cut
                while begin < len(line) and line[begin] == " ":
                    begin += 1
                prefix = hang
            if not pieces:
                out.append(line)
                continue
            pieces.append(prefix + line[begin:])
            out.extend(pieces)
        return "\n".join(out)

    # --------------- Refactor helpers (AST-driven) ---------------
    @staticmethod
    def _char_col(line: str, byte_col: int) -> int:
        """AST columns are UTF-8 byte offsets; convert to str index."""
        return len(line.encode("utf-8")[:byte_col].decode("utf-8", "ignore"))

    def _unused_import_edits(self, code: str, is_init: bool = False):
        """Return (new_code, removed_names) or (None, [])."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None, []
        if is_init:
            return None, []

        used: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
        # names re-exported through __all__
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    for c in ast.walk(node):
                        if isinstance(c, ast.Constant) and isinstance(c.value, str):
                            used.add(c.value)
        # string annotations such as "List[Foo]"
        ann_nodes: List[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.annotation:
                ann_nodes.append(node.annotation)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                ann_nodes.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                ann_nodes.append(node.annotation)
        for a in ann_nodes:
            for c in ast.walk(a):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    used.update(re.findall(r"[A-Za-z_]\w*", c.value))

        lines = code.split("\n")
        start_count: Dict[int, int] = {}
        for n in tree.body:
            start_count[n.lineno] = start_count.get(n.lineno, 0) + 1

        edits: List[Tuple[int, int, Optional[str]]] = []
        removed: List[str] = []
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            if start_count.get(node.lineno, 0) > 1:
                continue
            span = lines[node.lineno - 1: node.end_lineno]
            if any("noqa" in ln for ln in span):
                continue
            if any(a.name == "*" for a in node.names):
                continue
            keep, drop = [], []
            for a in node.names:
                if isinstance(node, ast.Import):
                    bound = a.asname or a.name.split(".")[0]
                else:
                    bound = a.asname or a.name
                (keep if bound in used else drop).append(a)
            if not drop:
                continue
            removed.extend(a.asname or a.name for a in drop)
            if not keep:
                edits.append((node.lineno, node.end_lineno, None))
                continue
            parts = [a.name + (f" as {a.asname}" if a.asname else "") for a in keep]
            if isinstance(node, ast.Import):
                text = "import " + ", ".join(parts)
            else:
                text = f"from {'.' * node.level}{node.module or ''} import " + ", ".join(parts)
            if node.lineno == node.end_lineno and "#" in span[0]:
                text += "  #" + span[0].split("#", 1)[1]
            edits.append((node.lineno, node.end_lineno, text))

        if not edits:
            return None, []
        for a, b, text in sorted(edits, key=lambda e: e[0], reverse=True):
            lines[a - 1: b] = [] if text is None else [text]
        new = "\n".join(lines)
        try:
            ast.parse(new)
        except SyntaxError:
            return None, []
        return new, removed

    def remove_unused_imports(self):
        code = self._get_buffer()
        ok, err = self._ast_valid(code)
        if not ok:
            messagebox.showwarning("Syntax error", f"Parsing failed; cannot refactor.\n\n{err}")
            return
        is_init = bool(self.filename) and os.path.basename(self.filename) == "__init__.py"
        new, removed = self._unused_import_edits(code, is_init)
        if new is None:
            self.set_status("No unused imports found (conservative)")
            return
        self.display_code(new)
        self.set_status(f"Removed {len(removed)} unused import name(s): {', '.join(removed[:5])}")
        self.indentation_applied = True
        self.update_save_state()

    # --------------- Refactor: Simplify Boolean Returns ---------------
    def _simplify_boolean_returns_text(self, code: str):
        """Return (new_code, count) or (None, 0)."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None, 0
        lines = code.split("\n")
        repl: List[Tuple[int, int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            if len(node.body) != 1 or len(node.orelse) != 1:
                continue
            rt, rf = node.body[0], node.orelse[0]
            if not (isinstance(rt, ast.Return) and isinstance(rf, ast.Return)):
                continue
            vt, vf = rt.value, rf.value
            if not (isinstance(vt, ast.Constant) and isinstance(vf, ast.Constant)):
                continue
            if not (isinstance(vt.value, bool) and isinstance(vf.value, bool)):
                continue
            if vt.value == vf.value:
                continue
            first = lines[node.lineno - 1]
            if not first.lstrip().startswith("if "):      # skips `elif` branches
                continue
            span = lines[node.lineno - 1: node.end_lineno]
            if any("#" in ln for ln in span):             # would drop comments
                continue
            test = ast.get_source_segment(code, node.test)
            if not test:
                continue
            indent = first[: len(first) - len(first.lstrip())]
            if vt.value:
                text = f"{indent}return bool({test})"
            else:
                simple = isinstance(
                    node.test,
                    (ast.Name, ast.Call, ast.Attribute, ast.Subscript, ast.Constant),
                )
                text = f"{indent}return not {test}" if simple else f"{indent}return not ({test})"
            repl.append((node.lineno, node.end_lineno, text))
        if not repl:
            return None, 0
        for a, b, text in sorted(repl, key=lambda r: r[0], reverse=True):
            lines[a - 1: b] = [text]
        new = "\n".join(lines)
        try:
            ast.parse(new)
        except SyntaxError:
            return None, 0
        return new, len(repl)

    def simplify_boolean_returns(self):
        code = self._get_buffer()
        ok, err = self._ast_valid(code)
        if not ok:
            messagebox.showwarning("Syntax error", f"Parsing failed; cannot refactor.\n\n{err}")
            return
        new, n = self._simplify_boolean_returns_text(code)
        if new is None:
            self.set_status("No boolean return patterns found")
            return
        self.display_code(new)
        self.set_status(f"Simplified {n} boolean return(s) (conservative)")
        self.indentation_applied = True
        self.update_save_state()

    # --------------- Refactor: Convert to f-strings (safe) ---------------
    def _convert_fstrings_text(self, code: str):
        """Return (new_code, count) or (None, 0). Only converts when every
        argument is a plain name and the template uses nothing exotic."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None, 0
        lines = code.split("\n")
        formatter = string.Formatter()
        edits: List[Tuple[int, int, int, str]] = []   # lineno, col, end_col, text

        def literal_body(const: ast.Constant) -> Optional[Tuple[str, str]]:
            if const.lineno != const.end_lineno:
                return None
            ln = lines[const.lineno - 1]
            a = self._char_col(ln, const.col_offset)
            b = self._char_col(ln, const.end_col_offset)
            seg = ln[a:b]
            if len(seg) < 2 or seg[0] not in "\"'" or seg[-1] != seg[0]:
                return None
            q, body = seg[0], seg[1:-1]
            if q in body or "\\N{" in body or body.startswith(q * 2):
                return None
            return q, body

        def is_name(n: ast.AST) -> bool:
            return isinstance(n, ast.Name)

        for node in ast.walk(tree):
            if node.lineno != getattr(node, "end_lineno", -1) if hasattr(node, "lineno") else True:
                continue
            new_text = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
                and all(is_name(a) for a in node.args)
                and all(k.arg is not None and is_name(k.value) for k in node.keywords)
            ):
                lit = literal_body(node.func.value)
                if not lit:
                    continue
                q, body = lit
                pos = [a.id for a in node.args]
                kws = {k.arg: k.value.id for k in node.keywords}
                out, auto, manual, okay = [], 0, False, True
                try:
                    parsed = list(formatter.parse(body))
                except ValueError:
                    continue
                for literal, fname, spec, conv in parsed:
                    out.append(literal.replace("{", "{{").replace("}", "}}"))
                    if fname is None:
                        continue
                    if "{" in (spec or "") or "}" in (spec or "") or re.search(r"[.\[]", fname):
                        okay = False
                        break
                    if fname == "":
                        if manual or auto >= len(pos):
                            okay = False
                            break
                        expr, auto = pos[auto], auto + 1
                    elif fname.isdigit():
                        if auto or int(fname) >= len(pos):
                            okay = False
                            break
                        manual, expr = True, pos[int(fname)]
                    elif fname in kws:
                        expr = kws[fname]
                    else:
                        okay = False
                        break
                    out.append("{" + expr + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "") + "}")
                if okay:
                    new_text = "f" + q + "".join(out) + q
            elif (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and (is_name(node.right) or (
                    isinstance(node.right, ast.Tuple) and node.right.elts
                    and all(is_name(e) for e in node.right.elts)))
            ):
                lit = literal_body(node.left)
                if not lit:
                    continue
                q, body = lit
                names = [node.right.id] if is_name(node.right) else [e.id for e in node.right.elts]
                out, idx, okay = [], 0, True
                pos_ = 0
                for m in re.finditer(r"%(.?)", body):
                    out.append(body[pos_:m.start()].replace("{", "{{").replace("}", "}}"))
                    pos_ = m.end()
                    kind = m.group(1)
                    if kind == "%":
                        out.append("%")
                    elif kind in ("s", "r") and idx < len(names):
                        out.append("{" + names[idx] + ("!r" if kind == "r" else "") + "}")
                        idx += 1
                    else:
                        okay = False
                        break
                if okay and idx == len(names):
                    out.append(body[pos_:].replace("{", "{{").replace("}", "}}"))
                    new_text = "f" + q + "".join(out) + q
            if new_text:
                ln = lines[node.lineno - 1]
                edits.append((
                    node.lineno,
                    self._char_col(ln, node.col_offset),
                    self._char_col(ln, node.end_col_offset),
                    new_text,
                ))
        if not edits:
            return None, 0
        for lineno, a, b, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
            ln = lines[lineno - 1]
            lines[lineno - 1] = ln[:a] + text + ln[b:]
        new = "\n".join(lines)
        try:
            ast.parse(new)
        except SyntaxError:
            return None, 0
        return new, len(edits)

    def convert_to_fstrings(self):
        code = self._get_buffer()
        ok, err = self._ast_valid(code)
        if not ok:
            messagebox.showwarning("Syntax error", f"Parsing failed; cannot refactor.\n\n{err}")
            return
        new, n = self._convert_fstrings_text(code)
        if new is None:
            self.set_status("No safe f-string conversions found")
            return
        self.display_code(new)
        self.set_status(f"Converted {n} string(s) to f-strings (safe subset)")
        self.indentation_applied = True
        self.update_save_state()

    # --------------- External formatters ---------------
    def run_external_formatter(self):
        fmt = self._choose_formatter()
        if not fmt:
            return
        tool = fmt
        code = self._get_buffer()
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "buf.py")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(code)
            try:
                if tool == "ruff":
                    cmd = ["ruff", "format", tmp]
                elif tool == "black":
                    cmd = ["black", "--quiet", tmp]
                elif tool == "autopep8":
                    cmd = ["autopep8", "-a", "-a", "--in-place", tmp]
                else:
                    messagebox.showinfo("External Formatter", f"Unsupported: {tool}")
                    return
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    messagebox.showerror(
                        "Formatter Error",
                        proc.stderr or proc.stdout or f"{tool} failed",
                    )
                    return
                with open(tmp, "r", encoding="utf-8") as f:
                    new_code = f.read()
                self.display_code(new_code)
                self.indentation_applied = True
                self.update_save_state()
                self.set_status(f"Applied external formatter: {tool}")
            except FileNotFoundError:
                messagebox.showwarning("Not Found", f"{tool} not found on PATH.")

    def _choose_formatter(self) -> Optional[str]:
        tools = [t for t in ("ruff", "black", "autopep8") if self._is_on_path(t)]
        if not tools:
            messagebox.showinfo(
                "External Formatters",
                "No supported formatter found on PATH (ruff, black, autopep8).",
            )
            return None
        top = tk.Toplevel(self.root)
        top.title("Choose Formatter")
        var = tk.StringVar(value=tools[0])
        for t in tools:
            tk.Radiobutton(top, text=t, variable=var, value=t).pack(anchor="w")
        tk.Button(top, text="OK", command=top.destroy).pack(pady=6)
        top.transient(self.root)
        top.grab_set()
        self.root.wait_window(top)
        return var.get()

    @staticmethod
    def _is_on_path(exe: str) -> bool:
        for p in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(p, exe)
            exts = (".exe", ".bat", ".cmd", "") if os.name == "nt" else ("",)
            for e in exts:
                if os.path.isfile(candidate + e) and os.access(candidate + e, os.X_OK):
                    return True
        return False

    # --------------- AST helpers ---------------
    def _ast_valid(self, code: str) -> Tuple[bool, str]:
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"Line {e.lineno}, col {e.offset}: {e.msg}"

    # --------------- Misc helpers ---------------
    @staticmethod
    def _pos_to_idx(text: str, pos: Tuple[int, int]) -> int:
        line_no, col = pos
        lines = text.splitlines(True)
        if line_no - 1 < 0 or line_no - 1 >= len(lines):
            return len(text)
        return sum(len(lines[i]) for i in range(line_no - 1)) + col

    def update_save_state(self):
        state = "normal" if self.indentation_applied else "disabled"
        self.file_menu.entryconfig("Save", state=state)

    def show_about(self):
        about_text = (
            f"Ef Reindenter / Python Formatter — v{self.version}\n\n"
            "• PEP 8 formatting, wrapping, import organization\n"
            "• Heuristic block repair + reindent\n"
            "• Refactors: remove unused imports, simplify boolean returns,\n"
            "  convert simple %/.format to f-strings\n"
            "• Structure tree navigator\n"
            "• Project settings (pyproject.toml) + Settings dialog\n"
            "• External formatters (ruff/black/autopep8)\n\n"
            "AST-assisted, token-aware, Ef-brand tool.\n"
        )
        messagebox.showinfo("About", about_text)

    def set_status(self, msg: str):
        self.status_label.config(text=msg)


if __name__ == "__main__":
    root = tk.Tk()
    app = PythonReindenterApp(root)
    root.mainloop()
