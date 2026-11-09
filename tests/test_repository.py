
from pathlib import Path

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


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/user/repo?redirect=example",
        "https://github.com/user/repo#fragment",
        "https://user@github.com/user/repo",
        "https://github.com:8443/user/repo",
        "https://github.com/user/%2e%2e",
        "https://github.com/user/repo/extra",
    ],
)
def test_github_url_rejects_ambiguous_or_unsafe_urls(tmp_path, url):
    manager = RepositoryManager(tmp_path / "workspace")

    with pytest.raises(ValueError):
        manager.validate_github_url(url)


def test_github_url_accepts_https_repository_with_git_suffix(tmp_path):
    manager = RepositoryManager(tmp_path / "workspace")

    assert manager.validate_github_url(
        "https://github.com/example/project.git"
    )


def test_github_clones_with_same_repository_name_use_distinct_paths(
    tmp_path,
    monkeypatch,
):
    manager = RepositoryManager(tmp_path / "workspace")
    destinations = []

    def fake_clone_from(url, destination):
        destinations.append(Path(destination))
        Path(destination).mkdir(parents=True)

    import asea.tools.repository as repository_module

    monkeypatch.setattr(repository_module.Repo, "clone_from", fake_clone_from)

    first = manager.clone_repository("https://github.com/owner-one/project")
    second = manager.clone_repository("https://github.com/owner-two/project")

    assert first != second
    assert first.name == "owner-one__project"
    assert second.name == "owner-two__project"
    assert destinations == [first, second]
