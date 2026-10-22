"""Run the red-green fix loop over the eval bug set (needs an LLM key).

Each bug lives in evals/bugs.json: either a controlled mutation (a small
semantic patch, verified to turn a handful of tests red) or a real
historical bug reconstructed by reverting the fix's source side while
keeping its regression tests. For each: fresh worktree at the target repo's
HEAD, apply the bug, fix_until_green, record status / rounds / tokens /
elapsed / diff. A run whose diff touches test files is flagged: the loop's
prompt forbids it, the report verifies it. Results append to
evals/results.jsonl.

Pilot one bug first:
    python3 evals/run_bug_loop.py --id sw-bm25-order
Then the whole set:
    python3 evals/run_bug_loop.py

Key config comes from the environment or a .env in this repo or in ~
(CORECODER_API_KEY, CORECODER_BASE_URL, CORECODER_MODEL).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bugrepo import construct_bug, git, is_source  # noqa: E402
from corecoder import Agent  # noqa: E402
from corecoder.config import Config  # noqa: E402
from corecoder.llm import LLM  # noqa: E402
from corecoder.tdd import fix_until_green  # noqa: E402


def apply_bug(worktree: Path, bug: dict) -> None:
    if bug["type"] == "mutation":
        patch = Path(__file__).parent / bug["patch"]
        out = subprocess.run(["git", "-C", str(worktree), "apply", str(patch)],
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"patch {bug['patch']} failed: {out.stderr.strip()[:200]}")
    else:
        construct_bug(worktree, bug["commit"])


def run_one(llm: LLM, repo: Path, bug: dict, rounds: int, log=print) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="bugloop-"))
    git(repo, "worktree", "add", "--detach", str(tmp / "w"), "HEAD")
    wt = tmp / "w"
    os.chdir(wt)  # the agent's relative-path tools follow the process cwd
    p0, c0 = llm.total_prompt_tokens, llm.total_completion_tokens
    t0 = time.time()
    try:
        apply_bug(wt, bug)
        agent = Agent(llm=llm)

        def on_tool(name, kwargs):
            log(f"    > {name}({', '.join(f'{k}={str(v)[:40]}' for k, v in kwargs.items())})")

        report = fix_until_green(
            agent, tests=bug.get("tests", "."), max_fix_rounds=rounds,
            test_cmd=bug.get("test_cmd"), on_tool=on_tool,
        )
        changed = git(wt, "diff", "--name-only").stdout.split()
        rec = {
            "id": bug["id"], "repo": bug["repo"], "subject": bug.get("subject", ""),
            "status": report["status"], "fix_rounds": report["fix_rounds"],
            "elapsed_s": round(time.time() - t0, 1),
            "prompt_tokens": llm.total_prompt_tokens - p0,
            "completion_tokens": llm.total_completion_tokens - c0,
            "files_changed": changed,
            "touched_tests": [f for f in changed
                              if not is_source(f) or f.endswith(("_test.go", "_test.py"))],
            "output_tail": report.get("output", "")[-600:],
            "model": llm.model,
        }
        log(f"  -> {rec['status']} rounds={rec['fix_rounds']} "
            f"tokens={rec['prompt_tokens']}+{rec['completion_tokens']} {rec['elapsed_s']}s")
        return rec
    finally:
        os.chdir(REPO)
        git(repo, "worktree", "remove", "--force", str(wt))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bugs", type=Path, default=Path(__file__).parent / "bugs.json")
    ap.add_argument("--id", action="append", help="run only these bug ids (repeatable)")
    ap.add_argument("--rounds", type=int, default=5)
    args = ap.parse_args()

    config = Config.from_env()  # before any chdir: picks up .env here or in ~
    if not config.api_key:
        sys.exit("no API key: set CORECODER_API_KEY (or OPENAI_API_KEY) / .env first")
    llm = LLM(model=config.model, api_key=config.api_key, base_url=config.base_url,
              temperature=config.temperature, max_tokens=config.max_tokens)

    data = json.loads(args.bugs.read_text())
    bugs = data["bugs"]
    if args.id:
        bugs = [b for b in bugs if b["id"] in args.id]
    repos = {k: Path(v).expanduser().resolve() for k, v in data["repos"].items()}
    print(f"model={config.model} bugs={[b['id'] for b in bugs]} rounds_cap={args.rounds}")

    results_path = Path(__file__).parent / "results.jsonl"
    records = []
    for i, bug in enumerate(bugs):
        print(f"[{i + 1}/{len(bugs)}] {bug['id']}: {bug.get('subject', '')[:60]}")
        rec = run_one(llm, repos[bug["repo"]], bug, args.rounds)
        records.append(rec)
        with results_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    green = [r for r in records if r["status"] == "green" and not r["touched_tests"]]
    rounds = [r["fix_rounds"] for r in green]
    print(f"\nsummary: {len(green)}/{len(records)} fixed without touching tests"
          f"{f', avg {sum(rounds) / len(rounds):.1f} rounds' if rounds else ''}")
    for r in records:
        mark = "OK " if r["status"] == "green" and not r["touched_tests"] else "!! "
        print(f"  {mark}{r['id']:20s} {r['status']:6s} rounds={r['fix_rounds']} "
              f"tests_touched={r['touched_tests']}")


if __name__ == "__main__":
    main()
