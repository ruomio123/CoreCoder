"""Bounded red-green fix loop on top of the run_tests tool.

fix_until_green() runs the suite; while it is red it hands the failure
summary to the same Agent and lets it repair the source (never the tests),
one chat() per fix round, capped at max_fix_rounds so a hopeless task cannot
burn tokens forever. The report says how many fix rounds a green run took,
which is the number /tdd prints at the end.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .tools.run_tests import parse_summary

if TYPE_CHECKING:
    from .agent import Agent


def _is_green(output: str) -> bool:
    """Green means failures and errors are zero AND at least one test ran:
    an empty run ("no tests ran") must not read as a pass."""
    s = parse_summary(output)
    return s["failed"] == 0 and s["errors"] == 0 and s["passed"] > 0 and not s["no_tests"]


def fix_until_green(
    agent: Agent,
    tests: str = ".",
    max_fix_rounds: int = 5,
    on_token=None,
    on_tool=None,
) -> dict:
    """Run `tests`; while red, ask the agent to fix the source, then rerun.

    Returns {"status": "green"|"red"|"error", "fix_rounds": N, "output": the
    last test run}. Each fix round is one full agent.chat() turn, so it may
    itself call tools (edit_file, run_tests, ...); consent rules apply as
    usual inside those turns.
    """
    tool = agent._tool_by_name.get("run_tests")
    if tool is None:
        return {
            "status": "error",
            "fix_rounds": 0,
            "error": "the run_tests tool is not in this agent's toolset",
            "output": "",
        }

    fix_rounds = 0
    output = ""
    for attempt in range(max_fix_rounds + 1):
        output = tool.execute(path=tests)
        if _is_green(output):
            return {"status": "green", "fix_rounds": fix_rounds, "output": output}
        if attempt == max_fix_rounds:
            break
        agent.chat(
            "The test run below is red. Fix the source code so the suite goes "
            "green - do not weaken, skip or delete tests:\n\n" + output,
            on_token=on_token,
            on_tool=on_tool,
        )
        fix_rounds += 1
    return {"status": "red", "fix_rounds": fix_rounds, "output": output}
