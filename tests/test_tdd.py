"""The fix_until_green red-green loop and the /tdd REPL command."""

from corecoder import Agent
from corecoder.demo import ScriptedLLM
from corecoder.llm import LLM, LLMResponse, ToolCall
from corecoder.tdd import fix_until_green
from tests.conftest import repl_with

# deliberately a different length than _BUGGY: an edit that keeps the size can
# collide with pyc invalidation (whole-second mtime + size) when the suite is
# re-run within the same second
_FIXED = "def add(a, b):\n    return (a + b)\n"
_BUGGY = "def add(a, b):\n    return a - b\n"
_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _red_project(tmp_path):
    (tmp_path / "calc.py").write_text(_BUGGY)
    (tmp_path / "test_calc.py").write_text(_TEST)


def _fixing_agent(tmp_path):
    script = [
        LLMResponse(
            content="Fixing calc.py.",
            tool_calls=[ToolCall(
                id="c1", name="write_file",
                arguments={"file_path": str(tmp_path / "calc.py"), "content": _FIXED},
            )],
        ),
        LLMResponse(content="Fixed."),
    ]
    return Agent(llm=ScriptedLLM(script))


def test_green_project_needs_no_fix_rounds(tmp_path):
    (tmp_path / "calc.py").write_text(_FIXED)
    (tmp_path / "test_calc.py").write_text(_TEST)
    agent = Agent(llm=ScriptedLLM([]))
    report = fix_until_green(agent, tests=str(tmp_path))
    assert report["status"] == "green"
    assert report["fix_rounds"] == 0


def test_one_fix_round_turns_red_green(tmp_path):
    _red_project(tmp_path)
    report = fix_until_green(_fixing_agent(tmp_path), tests=str(tmp_path))
    assert report["status"] == "green"
    assert report["fix_rounds"] == 1
    assert (tmp_path / "calc.py").read_text() == _FIXED  # the fix really landed on disk


def test_red_project_gives_up_at_the_cap(tmp_path):
    _red_project(tmp_path)
    # the model "fixes" nothing on both rounds; the loop must stop at the cap
    agent = Agent(llm=ScriptedLLM([LLMResponse(content="hmm"), LLMResponse(content="still thinking")]))
    report = fix_until_green(agent, tests=str(tmp_path), max_fix_rounds=2)
    assert report["status"] == "red"
    assert report["fix_rounds"] == 2


def test_empty_run_is_not_green(tmp_path):
    # a directory with no tests at all must not read as a pass
    agent = Agent(llm=ScriptedLLM([]))
    report = fix_until_green(agent, tests=str(tmp_path), max_fix_rounds=0)
    assert report["status"] == "red"


def test_loop_without_run_tests_tool_reports_error(tmp_path):
    agent = Agent(llm=LLM.__new__(LLM), tools=[])
    report = fix_until_green(agent, tests=str(tmp_path))
    assert report["status"] == "error"
    assert "run_tests" in report["error"]


def test_tdd_slash_command_runs_the_loop(monkeypatch, capsys, tmp_path):
    _red_project(tmp_path)
    agent = _fixing_agent(tmp_path)
    repl_with(monkeypatch, agent, [f"/tdd {tmp_path}", "quit"])
    out = capsys.readouterr().out
    assert "Green after 1 fix round(s)" in out
    assert (tmp_path / "calc.py").read_text() == _FIXED
