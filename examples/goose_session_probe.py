"""Reproduce context selection with Goose 1.60.1 and a loopback test provider."""

from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
from threading import Thread


MARKERS = {
    "AGENTS.md": "INSPECTOR_KELP_ABC_358",
    ".goosehints": "INSPECTOR_CORAL_GHI_482",
    "cart/.goosehints": "INSPECTOR_PINE_JKL_576",
}


def observed_paths(messages):
    system = json.dumps([item["content"] for item in messages if item["role"] == "system"])
    return [path for path, marker in MARKERS.items() if marker in system]


def run(executable, workspace, label, cwd, filenames=None):
    prompts = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompts.append(observed_paths(request["messages"]))
            response = {
                "id": "chatcmpl-local-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": request["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            if request.get("stream"):
                response["object"] = "chat.completion.chunk"
                response["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": "OK"},
                                        "finish_reason": "stop"}]
                body = f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n".encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps(response).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    isolated_state = workspace / f"state-{label}"
    isolated_state.mkdir()
    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG")
               if name in os.environ}
        env.update(GOOSE_PATH_ROOT=str(isolated_state),
                   OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
                   OPENAI_API_KEY="local-test-placeholder",
                   GOOSE_DISABLE_SESSION_NAMING="1",
                   GOOSE_TELEMETRY_ENABLED="false",
                   TEMP=str(workspace), TMP=str(workspace))
        if filenames is not None:
            env["CONTEXT_FILE_NAMES"] = json.dumps(filenames)
        try:
            process = subprocess.run([
                str(executable), "run", "--provider", "openai", "--model", "gpt-4o-mini",
                "--no-profile", "--name", label, "--text", "Return OK.",
                "--output-format", "json",
            ], cwd=cwd, env=env, capture_output=True, text=True, timeout=40, encoding="utf-8",
                errors="replace")
            if process.returncode:
                raise RuntimeError(f"{label} exit {process.returncode}: "
                                   + process.stdout + process.stderr)
            request_log = isolated_state / "state" / "logs" / "llm_request.0.jsonl"
            records = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
            native = [observed_paths(record["input"]["messages"]) for record in records if "input" in record]
            if len(prompts) != 1 or native != prompts:
                raise RuntimeError(f"{label} expected one endpoint request matching the native log")
            export = subprocess.run([
                str(executable), "session", "export", "--name", label, "--format", "json",
            ], cwd=cwd, env=env, capture_output=True, text=True, timeout=25, encoding="utf-8",
                errors="replace", check=True)
            if "Return OK." not in export.stdout:
                raise RuntimeError(f"{label} transcript export lacks user prompt")
            return {
                "case": label,
                "runtime_context_file_names": filenames,
                "working_directory": cwd.relative_to(workspace / "fixture").as_posix(),
                "provider_requests": len(prompts),
                "observed_first_request": prompts[0],
                "native_log_matches_endpoint_markers": True,
                "session_export_verified": True,
                "project_sentinels_in_transcript_export": [
                    path for path, marker in MARKERS.items() if marker in export.stdout
                ],
            }
        finally:
            server.shutdown()
            worker.join()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--goose", required=True, type=Path, help="Path to the Goose CLI binary.")
    parser.add_argument("--workdir", required=True, type=Path, help="New directory for the fixture and logs.")
    args = parser.parse_args()
    executable = args.goose.resolve(strict=True)
    version = subprocess.check_output([str(executable), "--version"], text=True).strip()
    if version.split()[-1] != "1.60.1":
        parser.error(f"This probe requires Goose CLI 1.60.1, found {version}.")
    workspace = args.workdir.resolve()
    workspace.mkdir(parents=True)
    fixture = workspace / "fixture"
    (fixture / "cart").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(fixture)], check=True)
    for path, marker in MARKERS.items():
        (fixture / path).write_text(f"Reproduction marker {marker}.\n", encoding="utf-8")
    (fixture / "cart/pricing.py").write_text("price = 100\n", encoding="utf-8")
    cases = [
        run(executable, workspace, "default", fixture),
        run(executable, workspace, "hints-only", fixture, [".goosehints"]),
        run(executable, workspace, "nested-cwd", fixture / "cart"),
    ]
    expected = [["AGENTS.md", ".goosehints"], [".goosehints"], list(MARKERS)]
    if [case["observed_first_request"] for case in cases] != expected:
        raise RuntimeError(f"Unexpected request context: {cases}")
    for case in cases:
        output = workspace / f"{case['case']}-observed.json"
        output.write_text(json.dumps(case["observed_first_request"]) + "\n", encoding="utf-8")
    print(json.dumps({"goose_version": version, "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
