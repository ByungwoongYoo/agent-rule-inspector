import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import rule_inspector as tool


REPO = Path(__file__).resolve().parents[1]


class RuleInspectorTest(unittest.TestCase):
    def setUp(self):
        scope = tempfile.TemporaryDirectory(dir=os.environ.get("RULE_TEST_TMP"))
        self.addCleanup(scope.cleanup)
        self.root = Path(scope.name).resolve()
        self.cart = self.root / "cart"
        self.cart.mkdir()
        self.target = self.cart / "pricing.py"
        self.target.touch()
        (self.root / "AGENTS.md").write_text("base", encoding="utf-8")
        (self.cart / "AGENTS.md").write_text("cart", encoding="utf-8")
        (self.cart / "AGENTS.override.md").write_text("override", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("import @AGENTS.md", encoding="utf-8")
        (self.cart / "CLAUDE.md").write_text("@notes.md\n", encoding="utf-8")
        (self.cart / "notes.md").write_text("@CLAUDE.md", encoding="utf-8")

    def options(self, **overrides):
        settings = dict(codex_bytes=32768, codex_fallback=[], claude_mode="claude-or-agents",
                        codex_loaded=None, claude_loaded=None)
        settings.update(overrides)
        return argparse.Namespace(**settings)

    def test_working_dir_has_provider_specific_files(self):
        report = tool.make_report(self.root, self.cart, self.target, self.options())
        self.assertEqual([r["path"] for r in report["Codex"]["files"]],
                         ["AGENTS.md", "cart/AGENTS.override.md"])
        self.assertEqual([r["path"] for r in report["Claude Code"]["files"]],
                         ["CLAUDE.md", "AGENTS.md", "cart/CLAUDE.md", "cart/notes.md"])

    def test_target_scope_does_not_claim_startup(self):
        report = tool.make_report(self.root, self.root, self.target, self.options())
        self.assertEqual(report["Codex"]["files"][1]["phase"], "target scope")
        self.assertEqual(report["Claude Code"]["files"][2]["phase"], "on read")

    def test_expected_observed_difference(self):
        evidence = self.root / "observed.json"
        evidence.write_text(json.dumps(["AGENTS.md", "../outside.md"]), encoding="utf-8")
        report = tool.make_report(self.root, self.cart, self.target, self.options(codex_loaded=evidence))
        self.assertEqual(report["Codex"]["expected_missing_from_list"], ["cart/AGENTS.override.md"])
        self.assertEqual(report["Codex"]["external_paths_excluded"], 1)
        self.assertNotIn("outside.md", tool.render_html(report))

    def test_budget_and_boundary(self):
        report = tool.make_report(self.root, self.cart, self.target, self.options(codex_bytes=4))
        self.assertEqual(report["Codex"]["files"][1]["state"], "budget exhausted")
        with self.assertRaisesRegex(ValueError, "Target must"):
            tool.make_report(self.root, self.cart, self.root / ".." / "out.md", self.options())

    def test_empty_override_and_fallback(self):
        (self.cart / "AGENTS.override.md").write_text("", encoding="utf-8")
        (self.cart / "AGENTS.md").unlink()
        (self.cart / "PROJECT.md").write_text("Build", encoding="utf-8")
        report = tool.make_report(self.root, self.cart, self.target, self.options(codex_fallback=["PROJECT.md"]))
        self.assertEqual(report["Codex"]["files"][1]["path"], "cart/PROJECT.md")

    def test_local_claude_blocks_startup_fallback(self):
        (self.root / "CLAUDE.md").unlink()
        (self.root / "CLAUDE.local.md").write_text("local", encoding="utf-8")
        result = tool.make_report(self.root, self.root, self.target, self.options())
        self.assertEqual(result["Claude Code"]["files"][0]["path"], "CLAUDE.local.md")
        self.assertNotIn("AGENTS.md", [r["path"] for r in result["Claude Code"]["files"]])

    def test_dot_claude_agents_fallback(self):
        (self.root / "CLAUDE.md").unlink()
        (self.root / "AGENTS.md").unlink()
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "AGENTS.md").write_text("fallback", encoding="utf-8")
        result = tool.make_report(self.root, self.root, self.target, self.options())
        self.assertEqual(result["Claude Code"]["files"][0]["path"], ".claude/AGENTS.md")

    def test_nested_local_claude_blocks_fallback_and_precedes_agents_in_both(self):
        (self.cart / "CLAUDE.md").unlink()
        (self.cart / "CLAUDE.local.md").write_text("local", encoding="utf-8")
        default = tool.make_report(self.root, self.root, self.target, self.options())
        self.assertEqual([r["path"] for r in default["Claude Code"]["files"] if r["phase"] == "on read"],
                         ["cart/CLAUDE.local.md"])
        both = tool.make_report(self.root, self.root, self.target, self.options(claude_mode="both"))
        self.assertEqual([r["path"] for r in both["Claude Code"]["files"] if r["phase"] == "on read"],
                         ["cart/CLAUDE.local.md", "cart/AGENTS.md"])

    def test_unresolved_import_token_is_excluded_from_output(self):
        token = "illustrative-private-name.md"
        (self.root / "CLAUDE.md").write_text(f"import @{token}", encoding="utf-8")
        report = tool.make_report(self.root, self.root, self.target, self.options())
        self.assertNotIn(token, json.dumps(report))
        self.assertNotIn(token, tool.render_html(report))

    def test_managed_mode_keeps_conditional_nested_native_rules(self):
        result = tool.make_report(self.root, self.root, self.target,
                                  self.options(claude_mode="managed-only"))
        rows = result["Claude Code"]["files"]
        self.assertEqual(rows[0]["path"], "cart/CLAUDE.md")
        self.assertEqual(rows[0]["phase"], "on read")
        self.assertTrue(all(r["phase"] != "startup" for r in rows))

    def test_goose_distinguishes_config_drift_from_deferred_hints(self):
        (self.root / ".git").mkdir()
        (self.root / ".goosehints").write_text("root hints", encoding="utf-8")
        (self.cart / ".goosehints").write_text("cart hints", encoding="utf-8")
        evidence = self.root / "goose-observed.json"
        evidence.write_text(json.dumps([".goosehints"]), encoding="utf-8")
        report = tool.make_report(self.root, self.root, self.target, self.options(goose_loaded=evidence))
        default = report["Goose"]
        self.assertEqual([(row["path"], row["phase"]) for row in default["files"]],
                         [(".goosehints", "startup"), ("AGENTS.md", "startup"),
                          ("cart/.goosehints", "on access"), ("cart/AGENTS.md", "on access")])
        self.assertEqual(default["expected_missing_from_list"], ["AGENTS.md"])
        self.assertIn("Goose", tool.render_text(report))
        configured = tool.make_report(self.root, self.root, self.target,
                                      self.options(goose_loaded=evidence, goose_context_file=[".goosehints"]))
        self.assertEqual(configured["Goose"]["expected_missing_from_list"], [])
        self.assertEqual(configured["Goose"]["context_file_names"], [".goosehints"])

    def test_goose_startup_depends_on_git_root_and_cwd(self):
        no_git = tool.make_report(self.root, self.cart, self.target, self.options())
        self.assertEqual([row["path"] for row in no_git["Goose"]["files"]], ["cart/AGENTS.md"])
        (self.root / ".git").mkdir()
        git_repo = tool.make_report(self.root, self.cart, self.target, self.options())
        self.assertEqual([(row["path"], row["phase"]) for row in git_repo["Goose"]["files"]],
                         [("AGENTS.md", "startup"), ("cart/AGENTS.md", "startup")])

    def test_goose_rejects_native_request_log_as_path_list(self):
        raw_log = self.root / "llm_request.0.jsonl"
        raw_log.write_text('{"input":{"messages":[]}}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Goose evidence must be a path list"):
            tool.make_report(self.root, self.root, self.target, self.options(goose_loaded=raw_log))

    def test_example_cli_writes_html(self):
        output = self.root / "report.html"
        result = subprocess.run(
            [sys.executable, "-B", "rule_inspector.py", "--repo", "fixtures/checkout", "--cwd", "cart",
             "cart/pricing.py", "--claude-loaded", "fixtures/claude-observed.jsonl", "--json",
             "--html", str(output)],
            check=True, cwd=REPO, capture_output=True, text=True, encoding="utf-8")
        report = json.loads(result.stdout)
        self.assertIn("cart/CLAUDE.md", report["Claude Code"]["expected_missing_from_list"])
        self.assertIn("cart/CLAUDE.md", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
