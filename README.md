# Easy & Flexible Python Indentation Tool (efpit)

Python code re-indenter and formatter with a Tk GUI. Load a script with broken
indentation, click a button, get properly indented Python back.

Run it with `python efpit.py` (Python 3.8+, Tkinter required; no other dependencies).

## How does the efpit works

Indentation is recalculated **line by line from the structure of the lines before
it**, not by adding to whatever indentation each line already had (that was the
cause of the old "indent keeps growing" bug):

* a statement ending in `:` opens a level, so the next line is its body even if it
  was not indented in the source;
* `return`, `raise`, `break`, `continue` and `pass` end a flat body;
* `elif` / `else` / `except` / `finally` are re-attached to a compatible opener;
* a `def`, `class` or decorator at the column of an earlier one becomes its sibling,
  or a member of the enclosing class;
* a stray over-indented line stays at the enclosing level and never pushes later
  lines deeper; a block's body can never be shallower than its opener;
* continuation lines keep their position relative to their statement, comment-only
  lines are kept, and the inside of multi-line strings is never touched.

None of this needs the code to be valid Python.

### Note:  I used Claude to help fix 4 bugs that the original code had.

### Two modes (Edit menu)

| Command | Use it when |
|---|---|
| **Apply Indent** | The existing indentation is partly right. It is used only as a *hint* for where blocks end. |
| **Reset && Recalculate Indent** | The existing indentation cannot be trusted. All leading whitespace is discarded and rebuilt from the preceding lines only. |

**Limits.** If *all* indentation is gone, where a block *ends* cannot always be
known (for example, `print(x)` followed by a flat `return`). Reset mode uses the
rules above and is a best effort; Apply Indent is more accurate whenever some of
the original indentation is correct.

Measured on real standard-library files: untouched, correctly indented files
come back unchanged. With 10% of lines deliberately over-indented, 92% of lines
get the right indent (79% at 30% of lines); fully flattened files parse
afterwards about 9 times out of 10.

## Formatter and tools

* **Format (PEP 8)**: spacing, comment style, blank lines, long-line wrapping.
  It is token-based, so string and comment contents are never changed, and the
  result is checked against the original AST: if the program's meaning would
  change, nothing is applied.
* **Organize Imports**: groups (stdlib / third-party / local) and sorts the leading
  import block; multi-line imports stay intact.
* **Refactor**: remove unused imports (module-level only, honors `__all__`,
  `# noqa`, `__init__.py`), simplify `if x: return True else: return False`, convert
  simple `.format()` / `%s` strings to f-strings (only when it is exactly equivalent).
* **External formatters**: ruff, black or autopep8 when installed.

## Tests

```
python -m unittest test_preindent -v
```

The tests run headless (Tkinter is stubbed).

## License

GPL-3.0
