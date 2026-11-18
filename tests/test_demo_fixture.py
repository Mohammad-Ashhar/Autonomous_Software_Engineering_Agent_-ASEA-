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

def test_deterministic_demo_repair_applies_and_passes_regression_suite(
    tmp_path, monkeypatch
):
    import shutil

    from asea.agents.patch_generator import PatchGeneratorAgent
    from asea.agents.patch_validator import PatchValidatorAgent

    source_repository = Path(__file__).resolve().parents[1] / "demo_repo_multi"
    working_repository = tmp_path / "demo_repo_multi"
    working_repository.mkdir()
    shutil.copy2(source_repository / "logic.py", working_repository / "logic.py")
    shutil.copy2(
        source_repository / "test_logic.py",
        working_repository / "test_logic.py",
    )

    generator = PatchGeneratorAgent()

    def model_timeout(**_kwargs):
        raise TimeoutError("model unavailable during deterministic demo")

    monkeypatch.setattr(generator.llm, "chat", model_timeout)
    patch_result = generator.generate_patch(
        working_repository / "logic.py",
        {"title": "Incorrect positive boundary"},
    )

    assert patch_result["generator"] == "deterministic_fallback"
    PatchValidatorAgent().apply_patch(
        working_repository,
        patch_result["patch"],
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_logic.py"],
        cwd=working_repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "6 passed" in output

