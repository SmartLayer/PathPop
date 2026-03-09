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

# Widget references (set during UI creation)
root = None
path_var = None
filter_var = None
tree = None
filter_entry = None


# --- CWD Detection ---

TERMINAL_APPS = {'gnome-terminal-server', 'gnome-terminal', 'kitty', 'alacritty',
                  'terminator', 'tilix', 'konsole', 'xterm', 'foot', 'wezterm',
                  'xfce4-terminal', 'mate-terminal', 'sakura', 'urxvt', 'st'}


def _extract_path(title):
    """Try to extract a directory path from a window title. Returns path or None."""
    if not title:
        return None
    m = re.search(r'(~[^\s:]*|/[^\s:]*)', title)
    if not m:
        return None
    path = os.path.expanduser(m.group(1))
    if not os.path.isdir(path):
        return None
    return path


def detect_cwd():
    """Read the focused window title via AT-SPI and extract a directory path.
    Returns (path, None) on success or (None, error_message) on failure.
    Collects all ACTIVE windows and prefers known terminal apps, so that
    a stale ACTIVE flag on Chrome doesn't shadow the real terminal."""
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    Atspi.init()
    desktop = Atspi.get_desktop(0)
    debug_lines = []
    active_windows = []  # list of (app_name, title, is_terminal)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if not app:
            continue
        app_name = app.get_name() or "(unnamed)"
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
            debug_lines.append(f"  app={app_name!r} win={win_title!r} role={win_role!r}{marker}")
            if is_active:
                is_terminal = app_name.lower() in TERMINAL_APPS
                active_windows.append((app_name, win.get_name(), is_terminal))
    debug_msg = "\n".join(["AT-SPI debug — all windows:"] + debug_lines)
    print(debug_msg, file=sys.stderr)
    if not active_windows:
        return None, "no active window found via AT-SPI"
    # Prefer terminal windows over non-terminal ones
    active_windows.sort(key=lambda x: (not x[2],))
    for app_name, title, is_terminal in active_windows:
        path = _extract_path(title)
        if path:
            return path, None
    # None of the active windows had a valid path — report the best candidate
    best = active_windows[0]
    if not best[1]:
        return None, f"active window ({best[0]!r}) has no title"
    return None, f"no path found in active window ({best[0]!r}) title: {best[1]!r}"


# --- Logic ---

def load_dir(dir_path):
    global cwd, all_items
    cwd = dir_path
    path_var.set(dir_path)
    all_items = []
    filter_var.set("")

    if dir_path != "/":
        all_items.append("..")

    dirs = []
    files = []
    try:
        entries = os.listdir(dir_path)
    except OSError:
        entries = []

    for name in entries:
        full = os.path.join(dir_path, name)
        if os.path.isdir(full):
            dirs.append(name + "/")
        else:
            files.append(name)

    dirs.sort(key=str.lower)
    files.sort(key=str.lower)
    all_items.extend(dirs)
    all_items.extend(files)
    apply_filter()


def apply_filter(*_args):
    tree.delete(*tree.get_children())
    pat = filter_var.get().lower()
    for item in all_items:
        if pat == "" or pat in item.lower():
            tree.insert("", tk.END, text=item)
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
    return tree.item(sel[0], "text")


def enter_dir():
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
    load_dir(os.path.dirname(cwd))


def select_item():
    item = get_selected_text()
    if item is None:
        return
    if item == "..":
        load_dir(os.path.dirname(cwd))
        return
    path = os.path.join(cwd, item.rstrip("/"))
    copy_and_exit(path)


def force_select():
    """Copy highlighted item's path regardless of type (file or directory)."""
    item = get_selected_text()
    if item is None:
        return
    if item == "..":
        path = os.path.dirname(cwd)
    else:
        path = os.path.join(cwd, item.rstrip("/"))
    copy_and_exit(path)


def copy_and_exit(path):
    rel = os.path.relpath(path, initial_cwd)
    quoted = shlex.quote(rel)
    log.debug("copy_and_exit: path=%r rel=%r quoted=%r", path, rel, quoted)
    try:
        if is_wayland:
            proc = subprocess.Popen(['wl-copy', '--', quoted],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate(timeout=5)
            if proc.returncode != 0:
                log.error("wl-copy failed (rc=%d): %s", proc.returncode, stderr.decode(errors='replace'))
            else:
                log.debug("wl-copy succeeded")
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
root.geometry("620x520")
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
tree = ttk.Treeview(frame, show="tree", selectmode="browse")
tree.column("#0", stretch=True)
scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
tree.pack(fill=tk.BOTH, expand=True)
frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

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
bind_break(filter_entry, "<Control-Return>", force_select)
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


filter_entry.bind("<Right>", on_filter_right)
filter_entry.bind("<Left>", on_filter_left)

# --- Bindings: treeview ---
bind_break(tree, "<Return>", select_item)
bind_break(tree, "<Control-Return>", force_select)
bind_break(tree, "<Right>", enter_dir)
bind_break(tree, "<Left>", go_up)
tree.bind("<Double-1>", lambda e: (select_item(), "break")[-1])

# Redirect typing from treeview back to the filter entry
NAV_KEYS = {'Up', 'Down', 'Left', 'Right', 'Return', 'space', 'Next', 'Prior', 'Home', 'End'}


def on_tree_key(event):
    if event.keysym not in NAV_KEYS:
        filter_entry.focus_set()
        filter_entry.event_generate('<Key>', keysym=event.keysym)
        return "break"


tree.bind("<Key>", on_tree_key)

# --- Escape ---
root.bind("<Escape>", lambda e: sys.exit(0))

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
