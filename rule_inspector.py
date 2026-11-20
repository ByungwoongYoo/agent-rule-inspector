"""Inspect where Codex, Claude Code, and Goose look for repository instructions."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

CODEX_DOC = "https://learn.chatgpt.com/docs/agent-configuration/agents-md"
CLAUDE_DOC = "https://code.claude.com/docs/en/memory"
CLAUDE_PLUGIN = "https://github.com/anthropics/claude-code/blob/main/mods/agents-md/README.md"
GOOSE_DOC = "https://goose-docs.ai/docs/guides/context-engineering/using-goosehints/"
GOOSE_CONTEXT_FILES = (".goosehints", "AGENTS.md")
PROVIDERS = ("Codex", "Claude Code", "Goose")
IMPORT = re.compile(r"(?<!\S)@([^\s\x60<>]+)")
FENCE = re.compile(r"^\s{0,3}(" + chr(96) + r"{3,}|~{3,})")
MAX_CLAUDE_FILE = 4 * 1024 * 1024
MAX_EVIDENCE = 512 * 1024


@dataclass(frozen=True)
class Rule:
    path: str
    phase: str
    basis: str
    bytes: int
    state: str = "expected"


def within(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if within(path, root) else "[outside repo]"


def anchored(root: Path, path: str | Path) -> Path:
    return (root / Path(path)).resolve()


def chain(root: Path, leaf: Path) -> list[Path]:
    parts = leaf.relative_to(root).parts
    return [root.joinpath(*parts[:i]) for i in range(len(parts) + 1)]


def instruction(directory: Path, filename: str, root: Path) -> tuple[Path, Path] | None:
    candidate = directory / filename
    path = candidate.resolve()
    if within(path, root) and path.is_file() and path.stat().st_size:
        return candidate, path
    return None


def codex_rules(root: Path, cwd: Path, target: Path, budget: int,
                fallbacks: list[str]) -> list[Rule]:
    names = list(dict.fromkeys(["AGENTS.override.md", "AGENTS.md", *fallbacks]))
    selected: list[Rule] = []
    available = budget
    for directory in chain(root, cwd):
        for filename in names:
            entry = instruction(directory, filename, root)
            if entry is None:
                continue
            alias, path = entry
            size = path.stat().st_size
            phase = "startup"
            state = "expected" if size <= available else "budget review"
            if available == 0:
                state = "budget exhausted"
            selected.append(Rule(relative(alias, root), phase,
                                 f"First nonempty match in {relative(directory, root)}",
                                 size, state))
            available = max(0, available - size)
            break
    startup_dirs = set(chain(root, cwd))
    for directory in chain(root, target.parent):
        if directory in startup_dirs:
            continue
        for filename in names:
            entry = instruction(directory, filename, root)
            if entry is not None:
                alias, path = entry
                selected.append(Rule(relative(alias, root), "target scope",
                                     "Relevant to the target path. Check session evidence.",
                                     path.stat().st_size, "check target read"))
                break
    return selected


def markdown_imports(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    imports = []
    fence = ""
    for line in text.splitlines():
        match = FENCE.match(line)
        if match:
            marker = match[1]
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence or line.startswith(("    ", "\t")):
            continue
        for piece in re.split(r"(" + chr(96) + r"+[^" + chr(96) + r"]*" + chr(96) + r"+)", line):
            if not piece.startswith(chr(96)):
                imports.extend(IMPORT.findall(piece))
    return imports


def claude_rules(root: Path, cwd: Path, target: Path, mode: str) -> tuple[list[Rule], list[str]]:
    seen: set[Path] = set()
    selected: list[Rule] = []
    notes: list[str] = []

    def include(alias: Path, path: Path, phase: str, basis: str, depth: int = 0) -> None:
        if path in seen:
            return
        seen.add(path)
        size = path.stat().st_size
        state = "check target read" if phase == "on read" else "expected"
        if size > MAX_CLAUDE_FILE:
            state = "inspector size cap"
        selected.append(Rule(relative(alias, root), phase, basis, size, state))
        if size > MAX_CLAUDE_FILE or phase == "path rule":
            return
        if depth >= 5:
            notes.append(f"Import limit reached from {relative(alias, root)}.")
            return
        try:
            imports = markdown_imports(path)
        except (OSError, UnicodeError):
            notes.append(f"Could not read imports in {relative(alias, root)}.")
            return
        for spec in imports:
            if "://" in spec or spec.startswith(("//", "\\\\")):
                continue
            location = (path.parent / spec).expanduser().resolve()
            if not within(location, root):
                notes.append(f"Import outside the repo in {relative(alias, root)}.")
            elif not location.is_file():
                notes.append(f"Unresolved import in {relative(alias, root)}.")
            else:
                include(location, location, phase, f"Imported by {relative(alias, root)}", depth + 1)

    startup_dirs = chain(root, cwd)
    startup_claude = any(
        instruction(directory, filename, root)
        for directory in startup_dirs
        for filename in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
    )
    if mode == "managed-only":
        notes.append("Managed and user instructions are outside this repo inspection.")
        notes.append("Nested native Claude instructions may still load when a file is read.")

    def visit(directory: Path, phase: str) -> None:
        found = [
            entry for filename in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
            if (entry := instruction(directory, filename, root)) is not None
        ]
        read_agents = mode == "both" or (
            mode == "claude-or-agents" and not (startup_claude if phase == "startup" else found)
        )
        if read_agents:
            for filename in ("AGENTS.md", ".claude/AGENTS.md"):
                agent_file = instruction(directory, filename, root)
                if agent_file is not None:
                    found.append(agent_file)
        for alias, path in found:
            include(alias, path, phase, f"Instruction in {relative(directory, root)}")

    if mode != "managed-only":
        for directory in startup_dirs:
            visit(directory, "startup")
    for directory in chain(root, target.parent):
        if directory not in startup_dirs:
            visit(directory, "on read")
    rule_dir = (root / ".claude" / "rules").resolve()
    if within(rule_dir, root) and rule_dir.is_dir():
        for path in sorted(rule_dir.rglob("*.md"))[:100]:
            resolved = path.resolve()
            if within(resolved, root) and resolved.is_file():
                selected.append(Rule(relative(path, root), "path rule",
                                     "Check scope and session evidence.",
                                     resolved.stat().st_size, "check path scope"))
    return selected, notes


def goose_rules(root: Path, cwd: Path, target: Path, names: list[str] | tuple[str, ...]) -> list[Rule]:
    git_root = next((directory for directory in (cwd, *cwd.parents)
                     if (directory / ".git").exists()), None)
    if git_root is None:
        startup_dirs = [cwd]
    else:
        startup_dirs = chain(git_root if within(git_root, root) else root, cwd)
    target_dirs = chain(cwd, target.parent)[1:] if within(target.parent, cwd) else []
    selected = []
    for directories, phase, state in (
        (startup_dirs, "startup", "expected"),
        (target_dirs, "on access", "check target access"),
    ):
        for directory in directories:
            for name in names:
                entry = instruction(directory, name, root)
                if entry is not None:
                    alias, path = entry
                    selected.append(Rule(relative(alias, root), phase,
                                         f"Context file in {relative(directory, root)}",
                                         path.stat().st_size, state))
    return selected


def loaded_paths(evidence: Path, root: Path, *, allow_claude_events: bool = True) -> tuple[set[str], int]:
    if evidence.stat().st_size > MAX_EVIDENCE:
        raise ValueError("Observed path list exceeds 512 KiB.")
    raw = evidence.read_text(encoding="utf-8-sig").lstrip()
    if raw.startswith("["):
        entries = json.loads(raw)
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            raise ValueError("Expected a JSON array of path strings.")
    else:
        entries = []
        for line in raw.splitlines():
            value = line.strip()
            if not value:
                continue
            if value.startswith("{"):
                if not allow_claude_events:
                    raise ValueError("Goose evidence must be a path list, not a raw session or request log.")
                event = json.loads(value)
                path = event.get("file_path")
                if event.get("hook_event_name") == "InstructionsLoaded" and isinstance(path, str):
                    entries.append(path)
            else:
                entries.append(value)

    observed = set()
    excluded = 0
    for item in entries:
        path = anchored(root, item)
        if within(path, root):
            observed.add(relative(path, root))
        else:
            excluded += 1
    return observed, excluded


def evidence_rows(rows: list[Rule], evidence: Path | None, root: Path,
                  *, allow_claude_events: bool = True) -> dict:
    result = {"files": [asdict(rule) for rule in rows], "evidence_provided": evidence is not None}
    if evidence is not None:
        loaded, excluded = loaded_paths(evidence, root, allow_claude_events=allow_claude_events)
        predicted = {rule.path for rule in rows}
        startup = {rule.path for rule in rows if rule.phase == "startup" and rule.state == "expected"}
        result.update(observed=sorted(loaded), expected_missing_from_list=sorted(startup - loaded),
                      observed_elsewhere=sorted(loaded - predicted), external_paths_excluded=excluded)
    return result


def make_report(root: Path, cwd: Path, target: Path, options: argparse.Namespace) -> dict:
    if not root.is_dir():
        raise ValueError("Repository directory does not exist.")
    if not within(cwd, root) or not cwd.is_dir():
        raise ValueError("Working directory must exist inside the repository.")
    if not within(target, root) or not target.is_file():
        raise ValueError("Target must be an existing file inside the repository.")
    if not 0 <= options.codex_bytes <= 1024 * 1024:
        raise ValueError("Codex byte limit must be between 0 and 1048576.")
    if any(Path(n).name != n or n in (".", "..") for n in options.codex_fallback):
        raise ValueError("Codex fallback names must be bare file names.")
    goose_names = tuple(dict.fromkeys(getattr(options, "goose_context_file", None) or GOOSE_CONTEXT_FILES))
    if any(not name or "/" in name or "\\" in name or Path(name).name != name or name in (".", "..")
           for name in goose_names):
        raise ValueError("Goose context names must be bare file names.")

    codex = codex_rules(root, cwd, target, options.codex_bytes, options.codex_fallback)
    claude, notes = claude_rules(root, cwd, target, options.claude_mode)
    goose = goose_rules(root, cwd, target, goose_names)
    return {
        "working_directory": relative(cwd, root),
        "target": relative(target, root),
        "codex_bytes": options.codex_bytes,
        "claude_mode": options.claude_mode,
        "Codex": evidence_rows(codex, options.codex_loaded, root),
        "Claude Code": evidence_rows(claude, options.claude_loaded, root),
        "Goose": {
            **evidence_rows(goose, getattr(options, "goose_loaded", None), root, allow_claude_events=False),
            "context_file_names": list(goose_names),
        },
        "notes": [
            "Repository files only. User and managed rules may change the session.",
            "Codex startup budget uses file sizes. Runtime loading can differ near the limit.",
            "A listed file does not establish how the model applies its instructions.",
            "Claude's direct AGENTS.md loads may be absent from InstructionsLoaded logs.",
            "Goose predictions cover direct context files. Imports, ignored files and access-triggered loads need request evidence.",
            *notes,
        ],
    }


def render_text(report: dict) -> str:
    result = [
        f"Working directory: {report['working_directory']}",
        f"Target: {report['target']}",
    ]
    for provider in PROVIDERS:
        data = report[provider]
        result.extend(["", provider])
        for row in data["files"]:
            result.append("  {phase} | {path} | {state} | {basis}".format(**row))
        if not data["files"]:
            result.append("  No matching repository instruction files.")
        if data["evidence_provided"]:
            for key, label in (
                ("observed", "Observed paths"),
                ("expected_missing_from_list", "Expected at startup, missing from the supplied list"),
                ("observed_elsewhere", "Observed paths outside this prediction"),
            ):
                result.append(f"  {label}: {', '.join(data[key]) or 'none'}")
            if data["external_paths_excluded"]:
                result.append(f"  Outside-repo paths excluded: {data['external_paths_excluded']}")
        else:
            result.append("  No observation list supplied.")
    result.extend(["", *report["notes"]])
    return "\n".join(result)


def render_html(report: dict) -> str:
    e = html.escape
    parts = [
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Agent Rule Inspector</title>",
        "<style>body{font:16px system-ui;max-width:82rem;margin:2.5rem auto;padding:0 1.25rem;"
        "background:#faf8f2;color:#212823}h1{font-size:clamp(1.8rem,4vw,3rem)}"
        ".providers{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,25rem),1fr));gap:1rem}"
        "section{background:white;border:1px solid #d7ded6;padding:1.25rem;border-radius:.75rem}"
        "li{margin-bottom:.8rem}code{overflow-wrap:anywhere;color:#163c35}small{display:block;color:#4c5852}"
        "a{color:#11564d}</style><h1>Agent Rule Inspector</h1>",
        f"<p>Working directory <code>{e(report['working_directory'])}</code> · "
        f"Target <code>{e(report['target'])}</code></p><div class=providers>",
    ]
    for provider in PROVIDERS:
        data = report[provider]
        parts.append(f"<section><h2>{provider}</h2><ol>")
        for row in data["files"]:
            parts.append(f"<li><code>{e(row['path'])}</code>"
                         f"<small>{e(row['phase'])} · {e(row['state'])} · {e(row['basis'])}</small></li>")
        parts.append("</ol>")
        if not data["files"]:
            parts.append("<p>No matching repository instruction files.</p>")
        if data["evidence_provided"]:
            parts.append(f"<p><strong>Observed paths</strong> {e(', '.join(data['observed']) or 'none')}</p>")
            parts.append("<p><strong>Expected at startup, missing from the supplied list</strong> "
                         f"{e(', '.join(data['expected_missing_from_list']) or 'none')}</p>")
            parts.append("<p><strong>Observed paths outside this prediction</strong> "
                         f"{e(', '.join(data['observed_elsewhere']) or 'none')}</p>")
        else:
            parts.append("<p>No observation list supplied.</p>")
        parts.append("</section>")
    parts.append("</div><ul>" + "".join(f"<li>{e(note)}</li>" for note in report["notes"]) + "</ul>")
    parts.append(f"<p><a href='{CODEX_DOC}'>Codex discovery rules</a> · "
                 f"<a href='{CLAUDE_DOC}'>Claude Code memory</a> · "
                 f"<a href='{CLAUDE_PLUGIN}'>Claude AGENTS.md modes</a> · "
                 f"<a href='{GOOSE_DOC}'>Goose context files</a></p>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Existing target file, relative to --repo or absolute.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root.")
    parser.add_argument("--cwd", default=".", help="Agent working directory, relative to --repo.")
    parser.add_argument("--codex-fallback", action="append", default=[], help="Additional filename to check.")
    parser.add_argument("--codex-bytes", type=int, default=32768, help="Project instruction byte budget.")
    parser.add_argument("--claude-mode", choices=("claude-or-agents", "claude-only", "both", "managed-only"),
                        default="claude-or-agents")
    parser.add_argument("--codex-loaded", type=Path, help="Observed paths, one per line or a JSON array.")
    parser.add_argument("--claude-loaded", type=Path, help="Observed paths or InstructionsLoaded JSONL.")
    parser.add_argument("--goose-context-file", action="append", help="Override Goose context filenames. Repeat per filename.")
    parser.add_argument("--goose-loaded", type=Path, help="Observed paths from a Goose request, as a path list or JSON array.")
    parser.add_argument("--json", action="store_true", help="Write machine-readable output.")
    parser.add_argument("--html", type=Path, help="Save a local standalone HTML report.")
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        report = make_report(root, anchored(root, args.cwd), anchored(root, args.target), args)
        if args.html:
            args.html.write_text(render_html(report), encoding="utf-8")
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
