# Agent Rule Inspector

See where Codex, Claude Code, and Goose look for project instructions. Supply a path list from a session to compare those observations with the expected files. The report stays local and contains paths and findings.

Run the bundled checkout example with Python 3.10 or later.

~~~sh
python rule_inspector.py --repo fixtures/checkout --cwd cart cart/pricing.py --codex-loaded fixtures/codex-loaded.json --claude-loaded fixtures/claude-observed.jsonl
~~~

The cart override takes precedence when Codex starts in the cart directory. The Claude list in this example contains only the root instructions, so the report names the missing cart files. Running with cwd at the root instead shows that nested Claude files may load on demand.

## Inspect your repository

~~~sh
python rule_inspector.py --repo /path/to/repo --cwd app app/example.py --html report.html
~~~

Use an existing file for the target. On Windows, pass your repository path in quotes. Open the report to compare the three agents side by side. Add `--json` to feed the same report to another tool.

- Codex starts at the given repo root and follows the working directory. The first nonempty candidate per directory wins. Set --codex-fallback and --codex-bytes to match your setup.
- Claude Code looks for CLAUDE.md, .claude/CLAUDE.md, and CLAUDE.local.md. The inspector follows local imports up to five levels and skips scanning files over 4 MiB. Use --claude-mode to match the setting for AGENTS.md or .claude/AGENTS.md. Nested files beyond the working directory may load on read. The inspector lists up to 100 files under the repository root's .claude/rules/ for manual review. Check session evidence to determine when those rules loaded.
- Goose checks `.goosehints` and `AGENTS.md` at startup from the Git root through the working directory. Outside a Git checkout it checks the working directory at startup. Hints below that directory can load on file access. To compare against a custom context file list, repeat `--goose-context-file` for each filename in the list.
- Evidence can be a JSON array or a list of loaded paths with one path per line. Claude Code InstructionsLoaded events are also accepted as JSONL when the records contain file_path. The report lists paths inside the selected repo. Claude's direct AGENTS.md loads do not fire InstructionsLoaded hooks, so a hook-only list may omit them. Check the session's debug log when a path matters.

This models repository discovery. Global and managed instructions, runtime version differences, conditional reads, and files near the byte limit need session evidence. The output makes no claim about how a model follows an instruction.

[Codex instructions guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) · [Claude Code memory guide](https://code.claude.com/docs/en/memory) · [Claude AGENTS.md modes](https://github.com/anthropics/claude-code/blob/main/mods/agents-md/README.md) · [Goose context files](https://goose-docs.ai/docs/guides/context-engineering/using-goosehints/)

## Reproduce a Goose session

In the Windows reproduction with Goose CLI 1.60.1, the first request included both root context files when using the default filenames. Setting `CONTEXT_FILE_NAMES=[".goosehints"]` left `AGENTS.md` out of the request while it was still on disk. Starting in the nested directory with the default context filenames also included its `.goosehints`. The [probe](examples/goose_session_probe.py) runs these three sessions against a loopback test provider and compares the fixture markers in the system messages recorded by Goose and received by the endpoint.

With Git on your `PATH`, point the probe at the [Goose 1.60.1 binary](https://github.com/aaif-goose/goose/releases/tag/v1.60.1) and a new working directory. The probe creates a synthetic Git repository and returns a fixed response from the local endpoint without model inference.

~~~sh
python examples/goose_session_probe.py --goose "/path/to/goose" --workdir goose-probe-run
python rule_inspector.py --repo goose-probe-run/fixture cart/pricing.py --goose-loaded goose-probe-run/hints-only-observed.json
~~~

The second command compares the recorded paths with Goose's default filenames and reports `AGENTS.md` missing from the supplied list. Add `--goose-context-file .goosehints` to compare the same run with the filenames it actually used.

In these sessions the conversation export contained the dialogue and omitted the project hint text. Use the request record to confirm your own path list before passing it to `--goose-loaded`. Keep raw request logs private, as they may contain prompts and file contents. The inspector models direct repository context filenames. Check the original request when your setup uses imports or ignore rules.

## Test

~~~sh
python -B -m unittest discover -s tests -v
~~~
