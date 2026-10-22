"""Sieve SignalWatch's history for revertible bug candidates (no LLM needed).

For every commit that touched non-test source: build the buggy state with
bugrepo.construct_bug in a throwaway worktree, run `go test` on the affected
packages, and compare against a clean baseline. Eligible = at least one test
newly red and at most --max-fails of them, no build breakage, revert applied
cleanly. Writes the candidate table to evals/signalwatch_candidates.json.

Usage:  python3 evals/sieve_signalwatch.py /path/to/SignalWatch
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bugrepo import commit_source_files, construct_bug, git, is_source, reset

_FAIL_PKG = re.compile(r"^FAIL\s+(\S+)", re.M)
_FAIL_TEST = re.compile(r"^--- FAIL: (\S+)", re.M)


def go_test(cwd: Path, pkgs: list[str], timeout: int = 300):
    try:
        proc = subprocess.run(
            ["go", "test", *pkgs], cwd=str(cwd), check=False,
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "", {"<timeout>"}, {"<timeout>": ["timeout"]}
    out = proc.stdout + "\n" + proc.stderr
    fail_pkgs = set(_FAIL_PKG.findall(out))
    fail_tests: dict[str, list[str]] = {}
    if fail_pkgs:
        for pkg in fail_pkgs:
            names = re.findall(rf"^--- FAIL: (\S+)", out, re.M)
            fail_tests[pkg] = names
    return out, fail_pkgs, fail_tests


def affected_packages(files: list[str]) -> list[str]:
    pkgs = set()
    for f in files:
        if f.endswith((".go",)) and "/" in f and not f.startswith("cmd/"):
            pkgs.add(f"./{f.rsplit('/', 1)[0]}/")
        elif f.startswith("cmd/"):
            pkgs.add(f"./{f.rsplit('/', 1)[0]}/")
    return sorted(pkgs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path, help="path to the SignalWatch checkout")
    ap.add_argument("--max-fails", type=int, default=8)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "signalwatch_candidates.json")
    args = ap.parse_args()
    repo = args.repo.resolve()

    tmp = Path(tempfile.mkdtemp(prefix="sieve-"))
    git(repo, "worktree", "add", "--detach", str(tmp / "w"), "HEAD")
    wt = tmp / "w"

    # baseline: which packages/tests are red on a clean tree (no MySQL etc.)
    print("baseline: go test ./... on clean tree ...", flush=True)
    _, base_pkgs, _ = go_test(wt, ["./..."], timeout=600)
    print(f"baseline red packages (excluded): {sorted(base_pkgs) or 'none'}", flush=True)

    commits = git(repo, "rev-list", "--no-merges", "HEAD").stdout.split()
    rows = []
    for i, commit in enumerate(reversed(commits)):  # oldest first
        subject = git(repo, "log", "--format=%s", "-1", commit).stdout.strip()
        files = commit_source_files(repo, commit)
        row = {"commit": commit, "subject": subject}
        if not files:
            row["reason"] = "no source (tests/docs only)"
            rows.append(row)
            continue
        pkgs = affected_packages(files)
        if not pkgs:
            row.update(reason="source outside packages", files=files)
            rows.append(row)
            continue
        try:
            reverted = construct_bug(wt, commit)
        except RuntimeError as e:
            row.update(reason=str(e)[:60])
            rows.append(row)
            reset(wt)
            continue
        out, fail_pkgs, _ = go_test(wt, pkgs)
        reset(wt)
        if "[build failed]" in out or "build failed" in out:
            row.update(reason="revert breaks build", packages=pkgs)
        else:
            new = fail_pkgs - base_pkgs
            if not new:
                row.update(reason="tests stay green", packages=pkgs)
            elif len(new) > args.max_fails:
                row.update(reason=f"{len(new)} pkgs red (not isolated)", packages=sorted(new))
            else:
                n_tests = len(_FAIL_TEST.findall(out))
                row.update(
                    eligible=True, test_cmd="go test " + " ".join(pkgs),
                    packages=pkgs, reverted_files=reverted,
                    red_packages=sorted(new), red_tests=n_tests,
                )
        rows.append(row)
        flag = "ELIGIBLE" if row.get("eligible") else row.get("reason", "?")
        print(f"[{i + 1}/{len(commits)}] {commit[:7]} {flag:32s} {subject[:48]}", flush=True)

    git(repo, "worktree", "remove", "--force", str(wt))

    eligible = [r for r in rows if r.get("eligible")]
    args.out.write_text(json.dumps({"eligible": eligible, "all": rows}, indent=2, ensure_ascii=False))
    print(f"\n{len(eligible)} eligible of {len(rows)} commits -> {args.out}")
    for r in eligible:
        print(f"  {r['commit'][:7]} red={r['red_packages']} tests={r['red_tests']}  {r['subject'][:60]}")


if __name__ == "__main__":
    main()
