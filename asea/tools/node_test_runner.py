"""Restricted Docker runner for dependency-free Node.js projects.

Package manifests are inspected without executing npm. Dependencies are never
installed, lifecycle hooks are never run, and only Node's built-in test runner
is executed inside the isolated container.
"""

import json
import time
from pathlib import Path

import docker
from docker.errors import DockerException


_SCAN_SCRIPT = r"""
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const root = "/workspace";
const ignored = new Set([
  ".git", "node_modules", ".next", "dist", "build", "coverage"
]);
const sourceExtensions = new Set([".js", ".mjs", ".cjs"]);
const files = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (!ignored.has(entry.name)) walk(path.join(dir, entry.name));
    } else if (entry.isFile()) {
      const full = path.join(dir, entry.name);
      if (sourceExtensions.has(path.extname(entry.name))) files.push(full);
    }
  }
}
walk(root);
const syntaxErrors = [];
for (const file of files) {
  const check = spawnSync(process.execPath, ["--check", file], {
    cwd: root, encoding: "utf8", timeout: 15000
  });
  if (check.status !== 0) {
    syntaxErrors.push({
      file: path.relative(root, file),
      output: (check.stderr || check.stdout || "Syntax check failed").slice(0, 4000)
    });
  }
}
const tests = files.filter(file =>
  /(?:^|[\\/])(?:test|tests)[\\/].+\.js$/.test(file) ||
  /\.(?:test|spec)\.(?:js|mjs|cjs)$/.test(file)
);
if (syntaxErrors.length) {
  process.stdout.write(JSON.stringify({
    status: "SYNTAX_FAILED",
    syntax_errors: syntaxErrors,
    test_files: tests.map(f => path.relative(root, f)),
    syntax_checked: files.length
  }));
  process.exit(2);
}
if (!tests.length) {
  process.stdout.write(JSON.stringify({
    status: "NO_TESTS",
    syntax_errors: [],
    test_files: [],
    syntax_checked: files.length
  }));
  process.exit(5);
}
const result = spawnSync(
  process.execPath,
  ["--test", ...tests],
  {
    cwd: root,
    encoding: "utf8",
    timeout: 90000,
    maxBuffer: 4 * 1024 * 1024
  }
);
process.stdout.write(JSON.stringify({
  status: result.status === 0 ? "PASSED" : "FAILED",
  syntax_errors: [],
  syntax_checked: files.length,
  test_files: tests.map(f => path.relative(root, f)),
  output: (result.stdout || "") + (result.stderr || "")
}));
process.exit(result.status === null ? 1 : result.status);
"""


class NodeTestRunner:
    """Run safe Node.js checks without installing or executing npm scripts."""

    def __init__(
        self,
        image="asea-sandbox:node22",
        timeout=120,
        memory_limit="512m",
    ):
        self.client = docker.from_env()
        self.image = image
        self.timeout = timeout
        self.memory_limit = memory_limit

    @staticmethod
    def inspect_package_manifest(repository_path: Path) -> dict:
        """Reject manifests requiring dependencies or arbitrary npm scripts."""
        manifest_path = repository_path / "package.json"
        if not manifest_path.exists():
            return {"package_manifest": False, "package_manager": None}

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return {
                "status": "INVALID_MANIFEST",
                "success": False,
                "reason": f"Could not safely parse package.json: {error}",
            }

        if not isinstance(manifest, dict):
            return {
                "status": "INVALID_MANIFEST",
                "success": False,
                "reason": "package.json must contain a JSON object.",
            }

        dependency_sections = (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        )
        dependency_data = {}
        for section in dependency_sections:
            values = manifest.get(section, {}) or {}
            if not isinstance(values, dict):
                return {
                    "status": "INVALID_MANIFEST",
                    "success": False,
                    "reason": f"package.json {section} must be an object.",
                }
            dependency_data[section] = values
        declared = sorted(
            name
            for values in dependency_data.values()
            for name in values
        )
        if declared:
            return {
                "status": "UNSUPPORTED_DEPENDENCIES",
                "success": False,
                "reason": (
                    "This project declares npm dependencies. ASEA does not "
                    "install or execute third-party packages yet."
                ),
                "declared_dependencies": declared,
                "package_manifest": True,
                "package_manager": "npm",
            }

        scripts = manifest.get("scripts", {}) or {}
        if not isinstance(scripts, dict):
            return {
                "status": "INVALID_MANIFEST",
                "success": False,
                "reason": "package.json scripts must contain a JSON object.",
            }

        unsupported_hooks = sorted(
            name for name in ("pretest", "posttest") if scripts.get(name)
        )
        if unsupported_hooks:
            return {
                "status": "UNSUPPORTED_LIFECYCLE",
                "success": False,
                "reason": (
                    "ASEA does not execute npm lifecycle hooks: "
                    + ", ".join(unsupported_hooks)
                ),
                "package_manifest": True,
                "package_manager": "npm",
            }

        test_script = scripts.get("test")
        if test_script and test_script != "node --test":
            return {
                "status": "UNSUPPORTED_TEST_SCRIPT",
                "success": False,
                "reason": (
                    "Only the exact package test script 'node --test' is "
                    "supported. Arbitrary npm scripts are never executed."
                ),
                "package_manifest": True,
                "package_manager": "npm",
            }

        return {
            "package_manifest": True,
            "package_manager": "npm",
            "test_script": test_script,
            "dependency_installation": "not_required",
        }

    def run_tests(self, repository_path):
        repo_path = Path(repository_path).resolve()
        if not repo_path.is_dir():
            raise NotADirectoryError(repo_path)

        manifest_result = self.inspect_package_manifest(repo_path)
        if manifest_result.get("success") is False:
            return manifest_result

        container = None
        started = time.monotonic()
        try:
            container = self.client.containers.create(
                image=self.image,
                command=["node", "-e", _SCAN_SCRIPT],
                working_dir="/workspace",
                volumes={str(repo_path): {"bind": "/workspace", "mode": "ro"}},
                network_disabled=True,
                read_only=True,
                user="1000:1000",
                mem_limit=self.memory_limit,
                nano_cpus=1_000_000_000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=64m,uid=1000,gid=1000"
                },
                environment={"HOME": "/tmp", "TMPDIR": "/tmp", "CI": "1"},
                detach=True,
            )
            container.start()
            timed_out = False
            while True:
                container.reload()
                if container.status == "exited":
                    break
                if time.monotonic() - started >= self.timeout:
                    timed_out = True
                    container.kill()
                    break
                time.sleep(0.25)

            raw = container.logs(
                stdout=True, stderr=True
            ).decode("utf-8", errors="replace")
            duration = round(time.monotonic() - started, 2)
            if timed_out:
                return {
                    **manifest_result,
                    "status": "TIMEOUT",
                    "success": False,
                    "timed_out": True,
                    "duration": duration,
                    "output": raw,
                }
            exit_code = container.attrs["State"]["ExitCode"]
            try:
                payload = json.loads(raw.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                payload = {"status": "FAILED", "output": raw}
            status = payload.get("status", "FAILED")
            return {
                **manifest_result,
                **payload,
                "status": status,
                "success": status == "PASSED" and exit_code == 0,
                "timed_out": False,
                "exit_code": exit_code,
                "duration": duration,
            }
        except DockerException as error:
            raise RuntimeError(f"Node Docker execution failed: {error}") from error
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
