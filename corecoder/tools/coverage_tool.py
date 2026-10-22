"""Missing-line coverage report, via pytest-cov.

The point is the Missing column: the agent gets "which lines does no test
reach" and can write tests against exactly those gaps, then re-check. When
pytest-cov isn't installed the tool says so instead of dumping a traceback -
coverage is an optional extra, not a dependency of the loop.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

from .base import Tool

_ROW_RE = re.compile(r"^(\S+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%\s*(.*)$")
_TOTAL_RE = re.compile(r"^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%\s*(.*)$")

# same rationale as run_tests: no __pycache__ litter, no stale-bytecode reads
# when a file is edited and re-measured within one second
_SUBPROCESS_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _parse_table(text: str) -> tuple[list[tuple[str, str, str]], str]:
    """Rows of (file, cover%, missing-lines) plus the TOTAL line, out of a
    term-missing report. A 100% row has no Missing column, hence the optional
    trailing group."""
    rows: list[tuple[str, str, str]] = []
    total = ""
    for line in text.splitlines():
        line = line.strip()
        m = _ROW_RE.match(line)
        if m:
            rows.append((m.group(1), f"{m.group(4)}%", m.group(5).strip()))
            continue
        m = _TOTAL_RE.match(line)
        if m:
            total = f"{m.group(3)}% ({m.group(2)}/{m.group(1)} stmts missing)"
    return rows, total


class CoverageTool(Tool):
    name = "coverage"
    description = (
        "Run the test suite under coverage (pytest-cov) and report, per source "
        "file, the lines no test reaches. Use it to find gaps, write tests for "
        "the missing lines, then re-check. Requires pytest-cov."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "File or directory to measure coverage for",
            },
            "tests": {
                "type": "string",
                "description": "Test file or directory to run (default: the whole suite)",
            },
            "timeout": {
                "type": "number",
                "description": "Seconds before the run is killed (default 300, max 600)",
            },
        },
        "required": ["source"],
    }

    def execute(self, source: str, tests: str = ".", timeout: int = 300) -> str:
        if importlib.util.find_spec("pytest_cov") is None:
            return (
                "Error: pytest-cov is not installed. Install it with "
                "`pip install pytest-cov`, or measure coverage through bash yourself."
            )
        timeout = max(1, min(int(timeout), 600))
        src = Path(source).expanduser()
        if not src.exists():
            return f"Error: {source} not found"
        target = Path(tests).expanduser()
        if not target.exists():
            return f"Error: {tests} not found"

        cmd = [
            sys.executable, "-m", "pytest", str(target), f"--cov={src}",
            "--cov-report=term-missing", "-q", "--no-header", "-p", "no:cacheprovider",
        ]
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, errors="replace",
                timeout=timeout, env=_SUBPROCESS_ENV,
            )
        except subprocess.TimeoutExpired:
            return f"Error: the coverage run did not finish within {timeout}s and was killed."

        output = (proc.stdout + "\n" + proc.stderr).strip()
        rows, total = _parse_table(output)
        if not rows:
            return "Coverage report could not be parsed (raw tail):\n" + output[-1200:]

        parts = [f"Coverage of {source} (tests: {tests})" + (f" — TOTAL {total}" if total else ""), ""]
        for name, pct, missing in rows[:30]:
            line = f"{name} - {pct}"
            if missing:
                line += f", missing lines: {missing}"
            parts.append(line)
        if len(rows) > 30:
            parts.append(f"... {len(rows) - 30} more file(s) not shown")
        parts.append("")
        parts.append("Write tests that exercise the missing lines above, then re-check with this tool.")
        return "\n".join(parts)[:4000]
