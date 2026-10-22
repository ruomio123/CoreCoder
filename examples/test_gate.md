# Test gate: run the related tests after every edit (zero code)

Copy [`test_gate.json`](test_gate.json) into `~/.corecoder/hooks.json` (merge it
with any hooks you already have there) and the agent becomes self-checking:
every `edit_file` / `write_file` on a `.py` file triggers a PostToolUse hook
that finds the matching `test_<name>.py` and runs it. The output goes straight
back to the model as part of the tool result, so it sees its own test failures
in the same turn and fixes them without being asked.

Two things to know:

- **Keep it fast.** A hook that runs past ten seconds is skipped with a
  warning, which is why the gate runs only the related test file, never the
  whole suite. Point the model at the `run_tests` tool for full runs.
- **It only observes.** PostToolUse hooks can't block anything; the test
  output is feedback, not a veto. For a hard veto (e.g. "no merge on red"),
  use a PreToolUse hook with exit code 2 instead.

Quick sanity check that the config is live: the REPL banner prints the number
of hooks loaded, and after one edit you should see the pytest summary show up
as part of the tool result.
