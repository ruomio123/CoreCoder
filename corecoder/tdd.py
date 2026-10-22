"""Bounded red-green fix loop on top of the run_tests tool.

fix_until_green() runs the suite; while it is red it hands the failure
summary to the same Agent and lets it repair the source (never the tests),
one chat() per fix round, capped at max_fix_rounds so a hopeless task cannot
burn tokens forever. The report says how many fix rounds a green run took,
which is the number /tdd prints at the end.

test_cmd swaps the suite for any shell command (go test, make check, ...).
Both suites judge green by the same rule: zero failures AND visible evidence
that at least one test ran - exit 0 alone is not green, because `go test` on
a package with no test files also exits 0.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from .tools.run_tests import parse_summary

if TYPE_CHECKING:
    from .agent import Agent

_CMD_TIMEOUT = 600

# what a genuinely passing run leaves behind: go's "ok  <pkg>" package lines
# or "--- PASS:" cases, or a pytest-style "N passed" summary
_PASS_EVIDENCE = (r"^ok\s+\S+", r"^--- PASS:", r"\b\d+ passed\b")


def _is_green(output: str) -> bool:
    """Green means failures and errors are zero AND at least one test ran:
    an empty run ("no tests ran") must not read as a pass."""
    s = parse_summary(output)
    return s["failed"] == 0 and s["errors"] == 0 and s["passed"] > 0 and not s["no_tests"]


def run_cmd_suite(cmd: str, timeout: int = _CMD_TIMEOUT) -> tuple[bool, str]:
    """Run a custom test command; green = exit 0 plus pass evidence above."""
    try:
        proc = subprocess.run(
            cmd, shell=True, check=False, capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"Error: '{cmd}' did not finish within {timeout}s and was killed."
    output = (proc.stdout + "\n" + proc.stderr).strip()
    green = proc.returncode == 0 and any(
        re.search(pat, output, re.M) for pat in _PASS_EVIDENCE
    )
    return green, output


def fix_until_green(
    agent: Agent,
    tests: str = ".",
    max_fix_rounds: int = 5,
    test_cmd: str | None = None,
    on_token=None,
    on_tool=None,
) -> dict:
    """Run the suite; while red, ask the agent to fix the source, then rerun.

    The suite is the run_tests tool (pytest) by default, or `test_cmd` as a
    shell command when given. Returns {"status": "green"|"red"|"error",
    "fix_rounds": N, "output": the last test run}. Each fix round is one full
    agent.chat() turn, so it may itself call tools (edit_file, run_tests,
    ...); consent rules apply as usual inside those turns.
    """
    if test_cmd is None:
        tool = agent._tool_by_name.get("run_tests")
        if tool is None:
            return {
                "status": "error",
                "fix_rounds": 0,
                "error": "the run_tests tool is not in this agent's toolset",
                "output": "",
            }

        def run_suite() -> tuple[bool, str]:
            output = tool.execute(path=tests)
            return _is_green(output), output
    else:
        def run_suite() -> tuple[bool, str]:
            return run_cmd_suite(test_cmd)

    fix_rounds = 0
    green, output = run_suite()
    while not green:
        if fix_rounds >= max_fix_rounds:
            break
        agent.chat(
            "The test run below is red. Fix the source code so the suite goes "
            "green - do not weaken, skip or delete tests:\n\n" + output,
            on_token=on_token,
            on_tool=on_tool,
        )
        fix_rounds += 1
        green, output = run_suite()
    status = "green" if green else "red"
    return {"status": status, "fix_rounds": fix_rounds, "output": output}
