#!/usr/bin/env python3

# fzf.py — Keyboard-driven file selector
# Copies selected file path to clipboard and exits.
#
# Usage:
#   python3 fzf.py [--test N]
#
# Detects CWD of the active terminal via AT-SPI window title (GNOME Wayland).
#
# NOTE: This tool is launched by a global shortcut key, NOT from a terminal.
# The whole point is that it works inside programs that own the terminal
# (Claude Code, vim, REPLs, etc.) where shell-based fzf cannot run.
# Therefore /proc/ppid tricks won't work — the parent process is gnome-shell,
# not the user's shell. CWD must come from the terminal's window title.

import sys
import os
import re
import shlex
import subprocess
import logging
import logging.handlers
import syslog
import time
import tkinter as tk
import tkinter.messagebox as messagebox
import tkinter.ttk as ttk
import tkinter.font as tkfont
from datetime import datetime

# --- Logging ---
debug_mode = os.environ.get('PATHPOP_DEBUG', '') != ''
log = logging.getLogger('pathpop')
if debug_mode:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.handlers.SysLogHandler(address='/dev/log',
                                                  facility=logging.handlers.SysLogHandler.LOG_USER)]
    )
else:
    log.addHandler(logging.NullHandler())

# --- Globals ---
test_mode = False
test_index = 0
cwd = ""
initial_cwd = ""
all_items = []
item_mtimes = {}
sort_column = "name"
sort_reverse = False
tilde_mode = False
selection_mode = False
checked_items = set()  # set of raw item names (e.g. "foo.txt", "bar/")
display_to_item = {}   # maps displayed text -> raw item name (used in selection mode)
HOME = os.path.expanduser('~')

# Widget references (set during UI creation)
root = None
path_var = None
filter_var = None
tree = None
filter_entry = None


# --- CWD Detection ---

def detect_cwd():
    """Read the focused window title via AT-SPI and extract a directory path.
    Returns (path, None) on success or (None, error_message) on failure.
    NOTE: This intentionally does NOT fall back to other windows when the
    active window has no path.  Falling back would silently mask the real
    problem (wrong window focused) and confuse the user."""
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    Atspi.init()
    desktop = Atspi.get_desktop(0)
    debug_lines = []
    try:
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if not app:
                continue
            app_name = app.get_name() or "(unnamed)"
            app_pid = app.get_process_id()
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if not win:
                    continue
                states = win.get_state_set()
                is_active = states.contains(Atspi.StateType.ACTIVE)
                is_focused = states.contains(Atspi.StateType.FOCUSED)
                win_title = win.get_name() or "(no title)"
                win_role = win.get_role_name() or "(no role)"
                marker = " ** ACTIVE **" if is_active else ""
                if is_focused:
                    marker += " FOCUSED"
                debug_lines.append(f"  app={app_name!r} pid={app_pid} win={win_title!r} role={win_role!r}{marker}")
                if is_active:
                    title = win.get_name()
                    if not title:
                        return None, f"active window ({app_name!r} pid={app_pid}) has no title"
                    m = re.search(r'(~[^\s:]*|/[^\s:]*)', title)
                    if not m:
                        return None, f"no path found in active window ({app_name!r}) title: {title!r}"
                    path = os.path.expanduser(m.group(1))
                    if not os.path.isdir(path):
                        return None, f"path from title is not a directory: {path!r} (title: {title!r})"
                    return path, None
        return None, "no active window found via AT-SPI"
    finally:
        for dl in debug_lines:
            log.debug("AT-SPI: %s", dl)


# --- Logic ---

def tilde_display(path):
    """Return a ~-prefixed display string for an absolute path."""
    rel = os.path.relpath(path, HOME)
    return '~' if rel == '.' else '~/' + rel


def load_dir(dir_path):
    global cwd, all_items, item_mtimes
    cwd = dir_path
    path_var.set(tilde_display(dir_path) if tilde_mode else dir_path)
    all_items = []
    item_mtimes = {}
    filter_var.set("")

    if dir_path != "/":
        all_items.append("..")

    try:
        entries = os.listdir(dir_path)
    except OSError:
        entries = []

    for name in entries:
        full = os.path.join(dir_path, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            mtime = 0
        if os.path.isdir(full):
            item_name = name + "/"
        else:
            item_name = name
        all_items.append(item_name)
        item_mtimes[item_name] = mtime

    sort_items()
    apply_filter()


def format_mtime(t):
    """Format a timestamp for display."""
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def sort_items():
    """Sort all_items based on current sort column and direction."""
    global all_items
    has_parent = ".." in all_items
    if has_parent:
        all_items.remove("..")

    if sort_column == "name":
        dirs = [x for x in all_items if x.endswith("/")]
        files = [x for x in all_items if not x.endswith("/")]
        dirs.sort(key=str.lower, reverse=sort_reverse)
        files.sort(key=str.lower, reverse=sort_reverse)
        all_items = dirs + files
    else:
        all_items.sort(key=lambda x: item_mtimes.get(x, 0), reverse=sort_reverse)

    if has_parent:
        all_items.insert(0, "..")


def sort_by_column(col):
    """Handle click on a column header to change sort order."""
    global sort_column, sort_reverse
    if sort_column == col:
        sort_reverse = not sort_reverse
    else:
        sort_column = col
        sort_reverse = (col == "date")
    current = get_selected_raw()
    sort_items()
    apply_filter()
    if current:
        _reselect(current)
    update_headings()


def update_headings():
    """Update column header text to reflect current sort."""
    arrow = " \u25bc" if sort_reverse else " \u25b2"
    tree.heading("name", text="Name" + (arrow if sort_column == "name" else ""))
    tree.heading("date", text="Date" + (arrow if sort_column == "date" else ""))


def apply_filter(*_args):
    global display_to_item
    tree.delete(*tree.get_children())
    display_to_item = {}
    pat = filter_var.get().lower()
    for item in all_items:
        if selection_mode and item == "..":
            continue
        if pat == "" or pat in item.lower():
            if selection_mode:
                prefix = "[x] " if item in checked_items else "[ ] "
                display = prefix + item
            else:
                display = item
            display_to_item[display] = item
            date_str = format_mtime(item_mtimes[item]) if item in item_mtimes else ""
            tree.insert("", tk.END, values=(display, date_str))
    children = tree.get_children()
    if children:
        tree.selection_set(children[0])
        tree.see(children[0])


def move_selection(delta):
    children = tree.get_children()
    if not children:
        return
    sel = tree.selection()
    if not sel:
        idx = 0
    else:
        idx = children.index(sel[0]) + delta
    idx = max(0, min(idx, len(children) - 1))
    tree.selection_set(children[idx])
    tree.see(children[idx])


def get_selected_text():
    """Return the text of the currently selected treeview item, or None."""
    sel = tree.selection()
    if not sel:
        return None
    return tree.set(sel[0], "name")


def get_selected_raw():
    """Return the raw item name of the currently selected treeview item."""
    display = get_selected_text()
    if display is None:
        return None
    return display_to_item.get(display, display)


def update_selection_count():
    n = len(checked_items)
    base = tilde_display(cwd) if tilde_mode else cwd
    path_var.set(f"{base}  ({n} selected)")


def enter_selection_mode():
    global selection_mode
    selection_mode = True
    current = get_selected_raw()
    if current and current != "..":
        checked_items.add(current)
    update_selection_count()
    status_var.set(HINT_SELECTION)
    apply_filter()
    # Re-select the item that was highlighted
    if current:
        _reselect(current)


def exit_selection_mode():
    global selection_mode
    selection_mode = False
    checked_items.clear()
    path_var.set(tilde_display(cwd) if tilde_mode else cwd)
    status_var.set(HINT_NORMAL)
    apply_filter()


def toggle_selection_mode():
    if selection_mode:
        exit_selection_mode()
    else:
        enter_selection_mode()


def toggle_check():
    if not selection_mode:
        return
    item = get_selected_raw()
    if item is None:
        return
    if item in checked_items:
        checked_items.discard(item)
    else:
        checked_items.add(item)
    update_selection_count()
    apply_filter()
    _reselect(item)


def _reselect(raw_item):
    """After apply_filter redraws, re-select the row matching raw_item."""
    for child in tree.get_children():
        if display_to_item.get(tree.set(child, "name")) == raw_item:
            tree.selection_set(child)
            tree.see(child)
            return


def enter_dir():
    if selection_mode:
        return
    item = get_selected_text()
    if item is None:
        return
    if item == "..":
        load_dir(os.path.dirname(cwd))
        return
    path = os.path.join(cwd, item.rstrip("/"))
    if os.path.isdir(path):
        load_dir(path)


def go_up():
    if selection_mode:
        return
    load_dir(os.path.dirname(cwd))


def goto_home():
    if selection_mode:
        return
    global tilde_mode
    tilde_mode = True
    load_dir(HOME)


def goto_root():
    if selection_mode:
        return
    global tilde_mode
    tilde_mode = False
    load_dir("/")


def select_item():
    if selection_mode:
        if not checked_items:
            return
        paths = []
        for item in checked_items:
            paths.append(os.path.join(cwd, item.rstrip("/")))
        copy_multi_and_exit(paths)
        return
    item = get_selected_text()
    if item is None:
        return
    if item == "..":
        load_dir(os.path.dirname(cwd))
        return
    path = os.path.join(cwd, item.rstrip("/"))
    if os.path.isdir(path):
        load_dir(path)
    else:
        copy_and_exit(path)


def copy_and_exit(path):
    if tilde_mode:
        rel = tilde_display(path)
    else:
        rel = os.path.relpath(path, initial_cwd)
    quoted = shlex.quote(rel)
    log.debug("copy_and_exit: path=%r rel=%r quoted=%r", path, rel, quoted)
    try:
        if is_wayland:
            proc = subprocess.Popen(['wl-copy'],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.stdin.write(quoted.encode())
            proc.stdin.close()
            log.debug("wl-copy started (stdin mode)")
        else:
            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate(quoted.encode(), timeout=5)
            if proc.returncode != 0:
                log.error("xclip failed (rc=%d): %s", proc.returncode, stderr.decode(errors='replace'))
            else:
                log.debug("xclip succeeded")
    except OSError as e:
        log.error("clipboard copy OSError: %s", e)
    except subprocess.TimeoutExpired:
        log.error("clipboard copy timed out")
        proc.kill()
    root.destroy()
    time.sleep(0.3)
    try:
        result = subprocess.run(['ydotool', 'key', '29:1', '42:1', '47:1', '47:0', '42:0', '29:0'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            log.error("ydotool paste failed (rc=%d): %s", result.returncode, result.stderr.decode(errors='replace'))
        else:
            log.debug("ydotool paste keystroke sent")
    except OSError as e:
        log.error("ydotool OSError: %s", e)
    if test_mode:
        time.sleep(0.1)
        try:
            subprocess.run(['ydotool', 'key', '28:1', '28:0'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    sys.exit(0)


def copy_multi_and_exit(paths):
    parts = []
    for path in sorted(paths):
        if tilde_mode:
            rel = tilde_display(path)
        else:
            rel = os.path.relpath(path, initial_cwd)
        parts.append(shlex.quote(rel))
    combined = " ".join(parts)
    log.debug("copy_multi_and_exit: %r", combined)
    try:
        if is_wayland:
            proc = subprocess.Popen(['wl-copy'],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.stdin.write(combined.encode())
            proc.stdin.close()
        else:
            proc = subprocess.Popen(['xclip', '-selection', 'clipboard'],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate(combined.encode(), timeout=5)
            if proc.returncode != 0:
                log.error("xclip failed (rc=%d): %s", proc.returncode, stderr.decode(errors='replace'))
    except OSError as e:
        log.error("clipboard copy OSError: %s", e)
    except subprocess.TimeoutExpired:
        log.error("clipboard copy timed out")
        proc.kill()
    root.destroy()
    time.sleep(0.3)
    try:
        result = subprocess.run(['ydotool', 'key', '29:1', '42:1', '47:1', '47:0', '42:0', '29:0'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            log.error("ydotool paste failed (rc=%d): %s", result.returncode, result.stderr.decode(errors='replace'))
    except OSError as e:
        log.error("ydotool OSError: %s", e)
    if test_mode:
        time.sleep(0.1)
        try:
            subprocess.run(['ydotool', 'key', '28:1', '28:0'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    sys.exit(0)


# --- Key binding helpers ---

def bind_break(widget, event, func):
    """Bind event to func and suppress default handling."""
    widget.bind(event, lambda e: (func(), "break")[-1])


# --- Parse args ---
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--test":
        test_mode = True
        i += 1
        if i < len(args):
            test_index = int(args[i])
    i += 1

# --- Display server detection ---
is_wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
if is_wayland:
    clip_cmd = 'wl-copy'
else:
    clip_cmd = 'xclip'
if not any(os.access(os.path.join(d, clip_cmd), os.X_OK) for d in os.environ.get('PATH', '').split(':')):
    msg = f"pathpop: required clipboard tool '{clip_cmd}' not found"
    syslog.syslog(syslog.LOG_ERR, msg)
    err_root = tk.Tk()
    err_root.withdraw()
    messagebox.showerror("PathPop", msg)
    err_root.destroy()
    sys.exit(1)

# --- CWD detection ---
time.sleep(0.4)
cwd, err = detect_cwd()
log.debug("detect_cwd: cwd=%r err=%r", cwd, err)
if not cwd:
    msg = f"fzf: {err}"
    syslog.syslog(syslog.LOG_ERR, msg)
    err_root = tk.Tk()
    err_root.withdraw()
    messagebox.showerror("fzf", msg)
    err_root.destroy()
    sys.exit(1)
initial_cwd = cwd

# --- Create root ---
root = tk.Tk()
root.withdraw()

# --- Variables ---
path_var = tk.StringVar()
filter_var = tk.StringVar()

# --- UI ---
root.title("fzf")
root.geometry("720x520")
root.attributes('-topmost', True)
root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))

# Path label
path_label = ttk.Label(root, textvariable=path_var, font=("monospace", 9),
                        foreground="grey40")
path_label.pack(fill=tk.X, padx=6, pady=(4, 0))

# Filter entry
filter_entry = ttk.Entry(root, textvariable=filter_var, font=("monospace", 11))
filter_entry.pack(fill=tk.X, padx=6, pady=(2, 0))

# Treeview (flat list, single column showing item text)
frame = ttk.Frame(root)
tree = ttk.Treeview(frame, columns=("name", "date"), show="headings", selectmode="browse")
tree.heading("name", text="Name \u25b2", anchor=tk.W, command=lambda: sort_by_column("name"))
tree.heading("date", text="Date", anchor=tk.W, command=lambda: sort_by_column("date"))
tree.column("name", stretch=True, anchor=tk.W)
tree.column("date", width=150, stretch=False, anchor=tk.W)
scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
tree.pack(fill=tk.BOTH, expand=True)
frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 0))

# Status bar
HINT_NORMAL = "Enter to choose. Shift+Enter for multi-select"
HINT_SELECTION = "Space to toggle. Enter to confirm. Esc to cancel"
status_var = tk.StringVar(value=HINT_NORMAL)
status_bar = ttk.Label(root, textvariable=status_var, font=("monospace", 8),
                        foreground="grey50")
status_bar.pack(fill=tk.X, padx=6, pady=(0, 4))

# Style the treeview font and selection colours
style = ttk.Style()
tree_font = tkfont.Font(family="monospace", size=11)
style.configure("Treeview", font=tree_font, rowheight=tree_font.metrics("linespace") + 4)
style.map("Treeview",
          background=[("selected", "#3465a4")],
          foreground=[("selected", "white")])

filter_entry.focus_set()

# --- Filter trace ---
filter_var.trace_add("write", lambda *_: apply_filter())

# --- Bindings: filter entry ---
bind_break(filter_entry, "<Return>", select_item)
bind_break(filter_entry, "<Shift-Return>", toggle_selection_mode)
bind_break(filter_entry, "<Control-Return>", toggle_selection_mode)
bind_break(filter_entry, "<Down>", lambda: move_selection(1))
bind_break(filter_entry, "<Up>", lambda: move_selection(-1))
bind_break(filter_entry, "<Next>", lambda: move_selection(10))
bind_break(filter_entry, "<Prior>", lambda: move_selection(-10))
bind_break(filter_entry, "<Home>", lambda: move_selection(-999999))
bind_break(filter_entry, "<End>", lambda: move_selection(999999))


def on_filter_right(event):
    if filter_entry.index(tk.INSERT) >= len(filter_entry.get()):
        enter_dir()
        return "break"


def on_filter_left(event):
    if filter_entry.index(tk.INSERT) == 0:
        go_up()
        return "break"


def on_filter_backspace(event):
    if filter_var.get() == "":
        go_up()
        return "break"


def on_filter_tilde(event):
    if filter_var.get() == "":
        goto_home()
        return "break"


def on_filter_slash(event):
    if filter_var.get() == "":
        goto_root()
        return "break"


def on_filter_space(event):
    if selection_mode and filter_var.get() == "":
        toggle_check()
        return "break"


filter_entry.bind("<Right>", on_filter_right)
filter_entry.bind("<Left>", on_filter_left)
filter_entry.bind("<BackSpace>", on_filter_backspace)
filter_entry.bind("<asciitilde>", on_filter_tilde)
filter_entry.bind("<slash>", on_filter_slash)
filter_entry.bind("<space>", on_filter_space)

# --- Bindings: treeview ---
bind_break(tree, "<Return>", select_item)
bind_break(tree, "<Shift-Return>", toggle_selection_mode)
bind_break(tree, "<Control-Return>", toggle_selection_mode)
bind_break(tree, "<space>", toggle_check)
bind_break(tree, "<Right>", enter_dir)
bind_break(tree, "<Left>", go_up)
bind_break(tree, "<BackSpace>", go_up)
bind_break(tree, "<asciitilde>", goto_home)
bind_break(tree, "<slash>", goto_root)
tree.bind("<Double-1>", lambda e: (select_item(), "break")[-1])

# Redirect typing from treeview back to the filter entry
NAV_KEYS = {'Up', 'Down', 'Left', 'Right', 'Return', 'space', 'Next', 'Prior', 'Home', 'End', 'BackSpace', 'asciitilde', 'slash'}


def on_tree_key(event):
    if event.keysym not in NAV_KEYS:
        filter_entry.focus_set()
        filter_entry.event_generate('<Key>', keysym=event.keysym)
        return "break"


tree.bind("<Key>", on_tree_key)

# --- Escape ---
def on_escape():
    if selection_mode:
        exit_selection_mode()
    else:
        sys.exit(0)

root.bind("<Escape>", lambda e: on_escape())

# --- Start ---
root.deiconify()
load_dir(cwd)

if test_mode:
    def test_callback():
        children = tree.get_children()
        if test_index < len(children):
            tree.selection_set(children[test_index])
            tree.see(children[test_index])
        select_item()
    root.after(500, test_callback)

root.mainloop()
