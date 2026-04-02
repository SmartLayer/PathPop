# PathPop

A keyboard-driven file selector that pops up as a GUI window over your terminal. Bind it to a global shortcut key and it opens a file browser rooted at your terminal's current directory. Pick one or more files, hit Enter, and the paths are typed into your terminal automatically.

## The problem

You're working in a terminal and need to reference a file path. The standard tool for this is `fzf` — but fzf is a terminal program. It takes over your shell's stdin/stdout to show its UI, which means it only works when you're sitting at a plain bash prompt.

The moment you're inside something that already owns the terminal — Claude Code, a REPL, `vim`, `docker exec`, an SSH session within an SSH session — fzf can't draw its picker. You'd have to exit what you're doing, run fzf, copy the path, go back in, and paste it. Or just give up and type the path by hand.

PathPop solves this by being a GUI window (Tk) bound to a global shortcut key. It doesn't need the terminal at all. It pops up over whatever you're doing, you pick a file, and the path appears at your cursor — whether that cursor is in bash, Claude Code, vim's command line, or anything else.

## How it works

1. Press your GNOME shortcut key
2. PathPop opens, showing the contents of the directory your terminal was in
3. Type to filter, arrow keys to navigate
4. Hit Enter — the window closes and the path appears at your cursor

Selected paths are relative to the terminal's working directory (e.g. `src/main.c`, `../lib/utils.py`) and single-quoted if they contain spaces or special characters.

## Keybindings

| Key | Action |
|-----|--------|
| typing | Filter the file list |
| Up / Down | Move selection |
| Page Up / Page Down | Move selection by 10 |
| Home / End | Jump to first / last item |
| Enter | Select file (pastes path and closes), or enter directory |
| Right | Enter directory (when cursor is at end of filter) |
| Left | Go to parent directory (when cursor is at start of filter) |
| Backspace | Go to parent directory (when filter is empty) |
| ~ | Navigate to home directory and switch to tilde-relative paths |
| Shift+Enter | Enter multi-selection mode |
| Escape | Cancel (or exit multi-selection mode) |

### Multi-selection mode

Press Shift+Enter to enter multi-selection mode. The currently highlighted item is automatically checked, and each item in the list gains a `[x]`/`[ ]` checkbox prefix. The `..` entry and directory navigation are disabled while in this mode.

| Key | Action |
|-----|--------|
| Space | Toggle checkbox on highlighted item |
| Enter | Submit all checked paths (space-separated, each shell-quoted) |
| Escape | Exit multi-selection mode without submitting |
| Shift+Enter | Exit multi-selection mode without submitting |

The path label shows the number of checked items. Both files and directories can be checked. Filtering still works — checked items that are filtered out retain their state.

## Requirements

Only tested on **Ubuntu 25.10** with **GNOME 49** (X11 and Wayland).

**Wayland:**
```
sudo apt install python3-tk wl-clipboard ydotool
```

**X11:**
```
sudo apt install python3-tk xclip ydotool
```

- `python3-tk` — Python Tk bindings (the UI)
- `wl-clipboard` / `xclip` — copies path to clipboard (auto-detected via `WAYLAND_DISPLAY`)
- `ydotool` — simulates Ctrl+Shift+V to paste into the terminal after the window closes

AT-SPI (`gir1.2-atspi-2.0`) is used for CWD detection and is installed by default on Ubuntu with GNOME.

## Setup

Bind to a GNOME keyboard shortcut:

```
Settings > Keyboard > Custom Shortcuts
Command: python3 /path/to/pathpop.py
```

### Terminal title

CWD detection relies on your shell setting the terminal title to the current directory (most shells do this by default via `PS1` or `PROMPT_COMMAND`). Programs that override the terminal title will break detection — for example, Claude Code sets the title to "Claude Code" instead of the path.

If you use Claude Code, disable its title override:

```
export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1
```

## CWD detection on GNOME Wayland

When launched from a keyboard shortcut, PathPop reads the active terminal's window title via AT-SPI (`gi.repository.Atspi`) and extracts the directory path from it. Since the tool is launched by a global shortcut key (not from a terminal), its parent process is gnome-shell — not the user's shell. Reading `/proc/ppid/cwd` would give gnome-shell's working directory, not the terminal's, so the CWD must come from the window title.

A small delay (400ms) is used before querying AT-SPI to let the window manager settle after processing the shortcut key.

### Terminal compatibility

AT-SPI only works with terminals that expose accessibility info. **GNOME Terminal** works. **cool-retro-term** (Qt/QML) does not appear in AT-SPI at all, so CWD detection fails even though its window title contains the correct path.
