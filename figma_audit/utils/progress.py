from __future__ import annotations


def progress_bar(completed: int, total: int, *, width: int = 24) -> str:
    """Return a compact ASCII progress bar that renders cleanly in cmd.exe."""
    if total <= 0:
        return "[" + ("-" * width) + "] 0/0"

    completed = max(0, min(completed, total))
    filled = round((completed / total) * width)
    bar = ("#" * filled) + ("-" * (width - filled))
    return f"[{bar}] {completed}/{total}"
