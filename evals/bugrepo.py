"""Construct reproducible buggy states from a repo's fix history.

Method: inside a throwaway worktree at HEAD, `git revert --no-commit` the fix
commit, then restore every non-source path the commit touched (tests, docs,
configs). What is left is current code plus that one bug back, while the
regression tests the fix shipped with stay in place - so the suite is red
for exactly that bug, and the fix commit's diff is the ground truth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, text=True
    )


def is_source(path: str) -> bool:
    """A fix's source side: real code, not its tests, docs or configs."""
    if path.endswith(("_test.go", "_test.py")):
        return False
    if path.startswith("tests/"):
        return False
    return path.endswith((".go", ".py", ".mod", ".sum"))


def commit_source_files(repo: Path, commit: str) -> list[str]:
    """Non-test source files touched by the commit (empty = not a candidate)."""
    out = git(repo, "show", "--name-only", "--format=", commit).stdout
    return [f for f in (l.strip() for l in out.splitlines()) if f and is_source(f)]


def construct_bug(worktree: Path, commit: str) -> list[str]:
    """Revert the fix's source side inside the worktree; keep tests at HEAD.

    Returns the source paths now at their buggy state. Raises RuntimeError
    when the revert conflicts (fix too old to invert cleanly) - the caller
    discards the candidate and resets.
    """
    rev = subprocess.run(
        ["git", "-C", str(worktree), "revert", "--no-commit", commit],
        capture_output=True, text=True,
    )
    if rev.returncode != 0:
        git(worktree, "reset", "--hard", "HEAD", "-q")
        raise RuntimeError(f"revert conflict: {rev.stderr.strip().splitlines()[-1] if rev.stderr.strip() else 'unknown'}")

    status = git(worktree, "status", "--porcelain").stdout
    changed = [line[3:].strip() for line in status.splitlines() if line.strip()]
    restore = [f for f in changed if not is_source(f)]
    if restore:
        git(worktree, "checkout", "HEAD", "--", *restore)
    git(worktree, "reset", "-q")  # unstage; the buggy worktree stays
    return [f for f in changed if is_source(f)]


def reset(worktree: Path) -> None:
    git(worktree, "reset", "--hard", "HEAD", "-q")
    git(worktree, "clean", "-fdq")
