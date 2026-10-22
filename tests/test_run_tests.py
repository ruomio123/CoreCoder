"""Tests for the run_tests and coverage tools."""

import importlib.util

import pytest

from corecoder.tools.coverage_tool import CoverageTool, _parse_table
from corecoder.tools.run_tests import parse_summary
from tests.conftest import get_tool

# --- parse_summary on canned -q --tb=line -rfE output ---

def test_parse_summary_reads_counts():
    text = (
        ".FF\n"
        "=================================== FAILURES ===================================\n"
        "/tmp/p/test_calc.py:7: assert -1 == 3\n"
        "FAILED test_calc.py::test_add_wrong - assert -1 == 3\n"
        "FAILED test_calc.py::test_div_zero - ZeroDivisionError: division by zero\n"
        "2 failed, 1 passed in 0.01s"
    )
    s = parse_summary(text)
    assert s["failed"] == 2
    assert s["passed"] == 1
    assert s["errors"] == 0
    assert s["seconds"] == pytest.approx(0.01)
    assert not s["no_tests"]


def test_parse_summary_reads_collection_errors():
    s = parse_summary(
        "ERROR test_broken.py\n"
        "!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!\n"
        "1 error in 0.21s"
    )
    assert s["errors"] == 1
    assert s["failed"] == 0


def test_parse_summary_flags_no_tests():
    s = parse_summary("no tests ran in 0.01s")
    assert s["no_tests"]
    assert s["passed"] == 0


# --- live runs against tiny throwaway projects ---

_FIXED = "def add(a, b):\n    return a + b\n"
_BUGGY = "def add(a, b):\n    return a - b\n"
_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def test_run_tests_green_project(tmp_path):
    (tmp_path / "calc.py").write_text(_FIXED)
    (tmp_path / "test_calc.py").write_text(_TEST)
    out = get_tool("run_tests").execute(path=str(tmp_path))
    assert "All tests passed" in out
    assert "1 passed" in out


def test_run_tests_structured_failure_summary(tmp_path):
    (tmp_path / "calc.py").write_text(_BUGGY)
    (tmp_path / "test_calc.py").write_text(_TEST)
    out = get_tool("run_tests").execute(path=str(tmp_path))
    assert "1 failed" in out
    assert "exit 1" in out
    assert "test_calc.py::test_add" in out
    assert "assert -1 == 3" in out
    assert "at " in out  # the paired file:line location
    assert "red" in out


def test_run_tests_collection_error_names_the_module(tmp_path):
    (tmp_path / "test_import_broken.py").write_text(
        "import module_that_does_not_exist\n\ndef test_x():\n    assert True\n"
    )
    out = get_tool("run_tests").execute(path=str(tmp_path))
    assert "ERRORS" in out
    assert "test_import_broken.py" in out
    assert "ModuleNotFoundError" in out


def test_run_tests_related_selection_skips_unrelated_failures(tmp_path, monkeypatch):
    """related_to=foo.py must run only test_foo.py, so a red test_bar.py
    elsewhere in the tree stays out of the verdict."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "foo.py").write_text("def f():\n    return 1\n")
    (tmp_path / "bar.py").write_text("def g():\n    return 2\n")
    (tmp_path / "test_foo.py").write_text("from foo import f\n\ndef test_f():\n    assert f() == 1\n")
    (tmp_path / "test_bar.py").write_text("from bar import g\n\ndef test_g():\n    assert g() == 99\n")
    out = get_tool("run_tests").execute(path=str(tmp_path), related_to="foo.py")
    assert "All tests passed" in out
    assert "related tests for foo.py: test_foo.py" in out


def test_run_tests_related_falls_back_to_full_suite(tmp_path):
    (tmp_path / "lonely.py").write_text("x = 1\n")
    out = get_tool("run_tests").execute(path=str(tmp_path), related_to=str(tmp_path / "lonely.py"))
    assert "no test_lonely.py" in out
    assert "No tests ran" in out


def test_run_tests_missing_target():
    assert "Error:" in get_tool("run_tests").execute(path="/does/not/exist")


def test_run_tests_kills_a_stuck_run(tmp_path):
    (tmp_path / "test_stuck.py").write_text("import time\n\ndef test_stuck():\n    time.sleep(20)\n")
    out = get_tool("run_tests").execute(path=str(tmp_path / "test_stuck.py"), timeout=2)
    assert "did not finish within 2s" in out


# --- coverage ---

def test_coverage_parse_table():
    text = (
        "Name                 Stmts   Miss  Cover   Missing\n"
        "--------------------------------------------------\n"
        "src/calc.py             12      3    75%   12, 18-20\n"
        "src/util.py              8      0   100%\n"
        "TOTAL                   20      3    85%   12, 18-20"
    )
    rows, total = _parse_table(text)
    assert rows[0] == ("src/calc.py", "75%", "12, 18-20")
    assert rows[1][1] == "100%"
    assert rows[1][2] == ""  # a fully covered file has no missing-lines column
    assert total.startswith("85%")


def test_coverage_without_pytest_cov_is_a_soft_error(tmp_path, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "pytest_cov" else real(name)
    )
    out = CoverageTool().execute(source=str(tmp_path))
    assert "pytest-cov is not installed" in out


def test_coverage_missing_source_is_an_error():
    assert "Error:" in CoverageTool().execute(source="/does/not/exist")
