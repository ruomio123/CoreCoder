"""Run the test suite and hand back a compact, structured summary.

Wraps pytest directly instead of going through bash for context economy: a
full-suite dump can run to thousands of lines, and the model only needs the
counts plus one entry per failure. `--tb=line` keeps pytest's traceback down
to a single `path:line: message` line and `-rfE` asks for the FAILED/ERROR
summary lines that carry test ids, so the two halves can be paired back into
one entry per failure. A run that exits non-zero without anything parseable
still returns its raw tail - a swallowed error is worse than a long one.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

from .base import Tool

_MAX_FAILURES_SHOWN = 10
_MAX_OUTPUT_CHARS = 4000
_RELATED_CAP = 5

# the " - message" suffix is optional: nested under an outer pytest run the
# short summary drops it, so the message is recovered from the location line
_FAILED_RE = re.compile(r"^FAILED (\S+)(?: - (.+))?$")
_ERROR_RE = re.compile(r"^ERROR (\S+)$")
_ELINE_RE = re.compile(r"^E\s+(\S.*)$")
_LOC_RE = re.compile(r"^(.+?\.py):(\d+): (.+)$")

# no __pycache__ in the target tree, and no stale-bytecode read when the same
# file is edited and re-run within one second (pyc invalidation keys on
# whole-second mtime + size, and an edit that keeps the size collides)
_SUBPROCESS_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def parse_summary(text: str) -> dict:
    """Counts off pytest's final summary line ("2 failed, 1 passed in 0.01s").

    Reused by the /tdd loop, which needs to know red/green without re-parsing
    the whole tool output itself.
    """
    counts = {
        "failed": 0, "passed": 0, "errors": 0, "skipped": 0,
        "seconds": None, "no_tests": "no tests ran" in text,
    }
    for line in reversed(text.splitlines()):
        if not re.search(r" in \d", line):
            continue
        for key in ("failed", "passed", "error", "skipped"):
            m = re.search(rf"(\d+) {key}s?\b", line)
            if m:
                counts["errors" if key == "error" else key] = int(m.group(1))
        m = re.search(r" in ([\d.]+)s", line)
        if m:
            counts["seconds"] = float(m.group(1))
        break
    return counts


def _related_tests(source: Path) -> list[Path]:
    """Tests matching a source file: test_<stem>.py / <stem>_test.py next to
    it, under a sibling tests/, then anywhere under the cwd. First matches
    win, capped; this is a heuristic for a fix loop, not a dependency graph."""
    names = (f"test_{source.stem}.py", f"{source.stem}_test.py")
    seen: set[Path] = set()
    found: list[Path] = []
    candidates: list[Path] = []
    for name in names:
        candidates.extend(p for p in (source.parent / name, source.parent / "tests" / name) if p.exists())
        candidates.extend(Path.cwd().rglob(name))
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            found.append(p)
            if len(found) >= _RELATED_CAP:
                break
    return found


class RunTestsTool(Tool):
    name = "run_tests"
    description = (
        "Run the test suite (pytest) and return a compact structured summary: "
        "counts plus one 'test - message' entry per failure with its file:line. "
        "Use it to check red/green after edits and to drive a fix loop: fix the "
        "source (never the tests) and run it again until green."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Test file or directory to run (default: the current directory)",
            },
            "related_to": {
                "type": "string",
                "description": "Source file that was just edited; runs its matching test_<name>.py when found, else falls back to path",
            },
            "timeout": {
                "type": "number",
                "description": "Seconds before the run is killed (default 120, max 600)",
            },
        },
        "required": [],
    }

    def execute(self, path: str = ".", related_to: str | None = None, timeout: int = 120) -> str:
        timeout = max(1, min(int(timeout), 600))
        target = Path(path).expanduser()
        if not target.exists():
            return f"Error: {path} not found"

        note = ""
        args = [str(target)]
        if related_to:
            src = Path(related_to).expanduser()
            if not src.exists():
                return f"Error: {related_to} not found"
            tests = _related_tests(src.resolve())
            if tests:
                args = [str(t) for t in tests]
                note = f" — related tests for {src.name}: {', '.join(t.name for t in tests)}"
            else:
                note = f" — no test_{src.stem}.py / {src.stem}_test.py found, ran {path} instead"

        cmd = [
            sys.executable, "-m", "pytest", *args,
            "-q", "--no-header", "--tb=line", "-rfE", "-p", "no:cacheprovider",
        ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, errors="replace",
                timeout=timeout, env=_SUBPROCESS_ENV,
            )
        except subprocess.TimeoutExpired:
            return (
                f"Error: pytest did not finish within {timeout}s and was killed. "
                "Scope `path` to a smaller target or raise `timeout` (max 600)."
            )
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return _format(output, proc.returncode, note)


def _format(output: str, returncode: int, note: str = "") -> str:
    lines = output.splitlines()
    summary = next(
        (l.strip().rstrip("=").strip() for l in reversed(lines) if re.search(r" in \d", l)),
        "",
    )
    counts = parse_summary(output)
    head = f"{summary or 'pytest done'} (exit {returncode}){note}"

    if counts["no_tests"]:
        return f"{head}\nNo tests ran - check the path."

    failures = [(m.group(1), m.group(2) or "") for l in lines if (m := _FAILED_RE.match(l))]
    errors = [m.group(1) for l in lines if (m := _ERROR_RE.match(l))]

    if not failures and not errors and returncode == 0 and counts["passed"] > 0:
        return f"All tests passed. {head}"

    # locations are the one-line tracebacks; they only appear between the
    # FAILURES header and the short summary, so don't scan elsewhere - a
    # collection traceback's "file.py:3: in <module>" frames would be noise
    locations: list[tuple[str, str]] = []
    try:
        start = next(
            i for i, l in enumerate(lines) if l.strip().strip("=").strip() == "FAILURES"
        )
    except StopIteration:
        start = None
    if start is not None:
        for l in lines[start + 1:]:
            if "short test summary" in l:
                break
            m = _LOC_RE.match(l.strip())
            if m:
                locations.append((f"{m.group(1)}:{m.group(2)}", m.group(3)))

    parts = [head]
    if failures:
        parts.append("")
        parts.append(f"FAILURES ({len(failures)}):")
        paired = len(locations) == len(failures)
        for i, (nodeid, msg) in enumerate(failures[:_MAX_FAILURES_SHOWN], 1):
            if not msg and paired:
                msg = locations[i - 1][1]
            entry = f"{i}. {nodeid} - {msg or 'failed'}"
            if paired:
                entry += f"\n   at {locations[i - 1][0]}"
            parts.append(entry)
        if len(failures) > _MAX_FAILURES_SHOWN:
            parts.append(f"... {len(failures) - _MAX_FAILURES_SHOWN} more failure(s) not shown")
    if errors:
        parts.append("")
        parts.append("ERRORS (collection/import):")
        parts.extend(f"- {e}" for e in errors[:_MAX_FAILURES_SHOWN])
        elines = list(dict.fromkeys(m.group(1) for l in lines if (m := _ELINE_RE.match(l))))
        parts.extend(f"  E {e}" for e in elines[:3])
    if returncode != 0 and not failures and not errors:
        parts.append("")
        parts.append("Unparsed output (tail):")
        parts.append(output[-1200:])
    else:
        parts.append("")
        parts.append(
            "The suite is red: fix the failures above in the source (not the tests), "
            "then run run_tests again."
        )
    return "\n".join(parts)[:_MAX_OUTPUT_CHARS]
