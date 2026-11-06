
import json
import shutil
import subprocess
from pathlib import Path


class StaticAnalyzer:

    def __init__(self, timeout=120):
        self.timeout = timeout

    
    def run_tool(self, command, repo_path):

        try:
            result = subprocess.run(
                command,
                check=False,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout
            )

            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": None
            }

        except subprocess.TimeoutExpired:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": "Execution timed out"
            }

        except OSError as error:
            return {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "error": str(error)
            }

    
    def run_ruff(self, repo_path):

        if not shutil.which("ruff"):
            return {
                "tool": "ruff",
                "available": False,
                "findings": [],
                "error": "Ruff is not installed"
            }

        result = self.run_tool(
            ["ruff", "check", "--output-format", "json", "."],
            repo_path
        )

        if result["error"]:
            return {
                "tool": "ruff",
                "available": True,
                "findings": [],
                "error": result["error"]
            }

        try:
            raw_findings = json.loads(result["stdout"])
        except json.JSONDecodeError:
            return {
                "tool": "ruff",
                "available": True,
                "findings": [],
                "error": "Could not parse Ruff JSON output"
            }

        findings = []

        for item in raw_findings:
            findings.append({
                "tool": "ruff",
                "rule": item.get("code"),
                "message": item.get("message"),
                "file": item.get("filename"),
                "line": item.get("location", {}).get("row"),
                "severity": "warning"
            })

        return {
            "tool": "ruff",
            "available": True,
            "findings": findings,
            "error": None
        }

    def run_semgrep(self, repo_path):

        if not shutil.which("semgrep"):
            return {
                "tool": "semgrep",
                "available": False,
                "findings": [],
                "error": "Semgrep is not installed"
            }

        result = self.run_tool(
            [
                "semgrep", "scan",
                "--config", "auto",
                "--json",
                "."
            ],
            repo_path
        )

        if result["error"]:
            return {
                "tool": "semgrep",
                "available": True,
                "findings": [],
                "error": result["error"]
            }

        try:
            data = json.loads(result["stdout"])
        except json.JSONDecodeError:
            return {
                "tool": "semgrep",
                "available": True,
                "findings": [],
                "error": "Could not parse Semgrep JSON output"
            }

        findings = []

        for item in data.get("results", []):
            extra = item.get("extra", {})
            findings.append({
                "tool": "semgrep",
                "rule": item.get("check_id"),
                "message": extra.get("message"),
                "file": item.get("path"),
                "line": item.get("start", {}).get("line"),
                "severity": extra.get("severity", "INFO")
            })

        errors = data.get("errors", [])

        return {
            "tool": "semgrep",
            "available": True,
            "findings": findings,
            "error": str(errors) if errors else None
        }

    def analyze(self, repo_path):

        repo_path = Path(repo_path).resolve()

        if not repo_path.is_dir():
            raise NotADirectoryError(repo_path)

        ruff_result = self.run_ruff(repo_path)
        semgrep_result = self.run_semgrep(repo_path)

        findings = (
            ruff_result["findings"]
            + semgrep_result["findings"]
        )

        return {
            "repository": repo_path.name,
            "ruff": ruff_result,
            "semgrep": semgrep_result,
            "total_findings": len(findings),
            "findings": findings
        }
