# Testing fzf.py

## The `--test N` flag

```
python3 fzf.py --test N
```

Auto-selects the item at index N (0-based) from the directory listing,
copies it to clipboard via `wl-copy`, pastes it into the terminal via
`ydotool` Ctrl+Shift+V, then sends Enter to submit. The app exits
after selection.

This tests the **full clipboard pipeline**: Python → wl-copy → Wayland
clipboard → ydotool paste → terminal receives the path.

## Directory listing order

The treeview is populated as:

| Index | Item |
|-------|------|
| 0     | `..` |
| 1     | `.claude/` |
| 2     | `.git/` |
| 3+    | remaining dirs (sorted case-insensitive) |
| ...   | files (sorted case-insensitive) |

Directories (including hidden) are listed first, then files.

## Notes

- The test runs the real GUI (Tk) — it needs a display.
- Index N assumes a specific directory listing order. If you add
  directories to the project root, the index may shift.
