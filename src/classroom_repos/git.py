from __future__ import annotations

import subprocess
from pathlib import Path


def is_git_worktree(path: Path) -> bool:
    result = _git(path, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def is_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain")
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
