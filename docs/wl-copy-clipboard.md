# wl-copy clipboard behavior

`wl-copy` on Wayland stays running as a clipboard server — it doesn't exit after copying. This causes problems with `subprocess`.

## What doesn't work

- **Passing text as argument** (`wl-copy -- text`): stays resident, shows a notification, blocks if you wait for it, and killing it loses the clipboard content.
- **`proc.communicate()`**: blocks until process exits. Since wl-copy never exits, it hits the timeout, then killing the process destroys the clipboard.

## What works

Write to stdin and close it, but do NOT wait for the process to exit:

```python
proc = subprocess.Popen(['wl-copy'],
                         stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
proc.stdin.write(text.encode())
proc.stdin.close()
```

This mirrors shell behavior (`echo "text" | wl-copy`) — wl-copy gets the data, forks to background as clipboard server, and the caller moves on.
