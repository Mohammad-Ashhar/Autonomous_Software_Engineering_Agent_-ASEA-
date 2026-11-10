import subprocess
import sys
from pathlib import Path


def test_documented_demo_fixture_starts_with_one_known_failure():
    repository = Path(__file__).resolve().parents[1] / "demo_repo_multi"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_logic.py"],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "test_zero_is_not_positive" in output
    assert "5 passed" in output
    assert "1 failed" in output
