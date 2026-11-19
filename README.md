# Agent Rule Inspector

Find the project instructions that apply to a file in Codex and Claude Code. Supply a path list from a session to compare the expected files with the observed ones. The report stays local and shows file paths and findings without copying instruction contents.

Run the bundled checkout example with Python 3.10 or later.

~~~sh
python rule_inspector.py --repo fixtures/checkout --cwd cart cart/pricing.py --codex-loaded fixtures/codex-loaded.json --claude-loaded fixtures/claude-observed.jsonl
~~~

The cart override takes precedence when Codex starts in the cart directory. The Claude list in this example contains only the root instructions, so the report names the missing cart files. Running with cwd at the root instead shows that nested Claude files may load on demand.

## Inspect your repository

~~~sh
python rule_inspector.py --repo /path/to/repo --cwd app app/example.py --html report.html
~~~

Use an existing file for the target. On Windows, pass your repository path in quotes. Open the report to compare the two providers side by side. Add --json to feed the same report to another tool.

- Codex starts at the given repo root and follows the working directory. The first nonempty candidate per directory wins. Set --codex-fallback and --codex-bytes to match your setup.
- Claude Code looks for CLAUDE.md, .claude/CLAUDE.md, and CLAUDE.local.md. The inspector follows local imports up to five levels and skips scanning files over 4 MiB. Use --claude-mode to match the setting for AGENTS.md or .claude/AGENTS.md. Nested files beyond the working directory may load on read. The inspector lists up to 100 files under the repository root's .claude/rules/ for manual review. Check session evidence to determine when those rules loaded.
- Evidence can be a JSON array or a list of loaded paths with one path per line. Claude Code InstructionsLoaded events are also accepted as JSONL when the records contain file_path. The report lists paths inside the selected repo. Claude's direct AGENTS.md loads do not fire InstructionsLoaded hooks, so a hook-only list may omit them. Check the session's debug log when a path matters.

This models repository discovery. Global and managed instructions, runtime version differences, conditional reads, and files near the byte limit need session evidence. The output makes no claim about how a model follows an instruction.

[Codex instructions guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) · [Claude Code memory guide](https://code.claude.com/docs/en/memory) · [Claude AGENTS.md modes](https://github.com/anthropics/claude-code/blob/main/mods/agents-md/README.md)

## Test

~~~sh
python -B -m unittest discover -s tests -v
~~~
