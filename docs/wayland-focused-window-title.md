# Getting the Focused Window Title on GNOME Wayland

Tested on Ubuntu with GNOME Shell 49.0, Wayland session, 2026-03-07.

## Problem

`detect_cwd` in `fzf.py` scans `/proc` for the process with the most recently accessed pts and uses its cwd. When multiple terminals are open, this picks the wrong terminal — whichever pts had the most recent I/O, not the one the user is actually looking at.

The fix requires reading the focused window's title (gnome-terminal sets it to the shell's cwd) and extracting the path from it.

## Methods Tested

### 1. AT-SPI (accessibility interface) — WORKS

Uses `gi.repository.Atspi` via Python. Iterates the desktop's accessible children, checks each window's state for `ACTIVE`, and reads its name (the window title).

```python
python3 -c "
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi
Atspi.init()
desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if app:
        for j in range(app.get_child_count()):
            win = app.get_child_at_index(j)
            if win and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                print(win.get_name())
"
```

Returns the window title, e.g. `~/code/rivermill`. No extra packages or extensions required — `at-spi2-core` and `gir1.2-atspi-2.0` are installed by default on Ubuntu with GNOME.

### 2. xdotool — DOES NOT WORK

`xdotool getactivewindow` returns a window ID (an XWayland proxy object), but `getwindowname` returns an empty string. Native Wayland windows have no X11 properties.

### 3. xprop — DOES NOT WORK

`xprop -root _NET_ACTIVE_WINDOW` returns a window ID, but `xprop -id <id> _NET_WM_NAME` returns "not found" for native Wayland windows.

### 4. GNOME Shell Eval (gdbus) — BLOCKED

```
gdbus call --session --dest org.gnome.Shell \
  --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval \
  "global.display.focus_window.get_title()"
```

Returns `(false, '')`. The Eval interface has been restricted to internal callers only since GNOME 41.

### 5. GNOME Shell Introspect GetWindows (gdbus) — ACCESS DENIED

```
gdbus call --session --dest org.gnome.Shell \
  --object-path /org/gnome/Shell/Introspect \
  --method org.gnome.Shell.Introspect.GetWindows
```

Returns `org.freedesktop.DBus.Error.AccessDenied: GetWindows is not allowed`.

### 6. libwnck (Python gi.repository.Wnck) — DOES NOT WORK

X11-only library. Fails with "libwnck is designed to work in X11 only" on Wayland.

### 7. GNOME Shell extensions (not installed) — WOULD WORK

Extensions such as `window-calls` or `focused-window-dbus` expose window title via D-Bus. Requires the user to install an extension. Not tested since AT-SPI already works without one.

## Recommendation

Use **AT-SPI** (method 1). It works out of the box on GNOME Wayland with no extra dependencies.
