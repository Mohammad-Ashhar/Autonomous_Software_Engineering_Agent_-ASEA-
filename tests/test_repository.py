
import pytest

from asea.tools.repository import RepositoryManager


def test_local_repository_loading(tmp_path):
    manager = RepositoryManager(tmp_path / "workspace")

    project = tmp_path / "demo"
    project.mkdir()

    result = manager.load_local_repository(project)

    assert result == project.resolve()


def test_invalid_github_url(tmp_path):
    manager = RepositoryManager(tmp_path / "workspace")

    with pytest.raises(ValueError):
        manager.validate_github_url(
            "https://example.com/user/repo"
        )


def test_python_file_discovery(tmp_path):
    manager = RepositoryManager(tmp_path / "workspace")

    project = tmp_path / "demo"
    project.mkdir()

    (project / "main.py").write_text("print('Hello')")

    ignored = project / ".venv"
    ignored.mkdir()

    (ignored / "ignored.py").write_text("print('Ignore')")

    files = manager.get_python_files(project)

    assert len(files) == 1
    assert files[0].name == "main.py"
