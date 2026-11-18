from pathlib import Path

import pytest
from git import Repo

from asea.integrations.repair_publisher import (
    get_approved_repair_files,
    normalize_github_repository,
    validate_source_repository,
)


def create_git_repository(
    path: Path,
    remote_url: str | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    repo = Repo.init(path)

    if remote_url:
        repo.create_remote("origin", remote_url)

    return path


def test_normalize_https_github_url():
    result = normalize_github_repository(
        "https://github.com/Mohammad-Ashhar/ASEA.git"
    )

    assert result == "mohammad-ashhar/asea"


def test_normalize_ssh_github_url():
    result = normalize_github_repository(
        "git@github.com:Mohammad-Ashhar/ASEA.git"
    )

    assert result == "mohammad-ashhar/asea"


def test_reject_non_github_url():
    with pytest.raises(ValueError):
        normalize_github_repository(
            "https://gitlab.com/user/project.git"
        )


def test_accept_matching_source_repository(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "ASEA",
        "https://github.com/Mohammad-Ashhar/ASEA.git",
    )

    source_path = repo_path / "demo_repo"
    source_path.mkdir()

    source_git_repo, git_root, repository_name = validate_source_repository(
        source_path,
    )

    assert source_git_repo.working_tree_dir is not None
    assert Path(git_root) == repo_path.resolve()
    assert repository_name == "mohammad-ashhar/asea"


def test_accept_any_github_source_repository_without_global_pin(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "another-project",
        "https://github.com/someone/another-project.git",
    )

    _, _, repository_name = validate_source_repository(repo_path)

    assert repository_name == "someone/another-project"


def test_reject_repository_without_origin(tmp_path):
    repo_path = create_git_repository(
        tmp_path / "no_remote"
    )

    with pytest.raises(
        RuntimeError,
        match="no origin remote",
    ):
        validate_source_repository(repo_path)


def test_reject_directory_outside_git_repository(tmp_path):
    outside_path = tmp_path / "outside"
    outside_path.mkdir()

    with pytest.raises(
        RuntimeError,
        match="not inside a valid Git repository",
    ):
        validate_source_repository(outside_path)

@pytest.mark.parametrize(
    ("review_history", "expected"),
    [
        ([], False),
        ([{"bug_key": "bug-1", "decision": "rejected", "attempt": 2}], False),
        ([{"bug_key": "bug-1", "decision": "approved", "attempt": 1}], False),
        (
            [
                {"bug_key": "bug-1", "decision": "approved", "attempt": 2},
                {"bug_key": "bug-1", "decision": "rejected", "attempt": 2},
            ],
            False,
        ),
        ([{"bug_key": "bug-1", "decision": "approved", "attempt": 2}], True),
    ],
)
def test_only_latest_approval_for_current_attempt_can_be_published(
    tmp_path,
    review_history,
    expected,
):
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    original_file = original / "module.py"
    working_file = working / "module.py"
    original_file.write_text("value = 1\n", encoding="utf-8")
    working_file.write_text("value = 2\n", encoding="utf-8")

    state = {
        "repaired_bugs": [
            {
                "status": "REPAIRED",
                "bug_key": "bug-1",
                "attempts": 2,
                "file": str(original_file),
            }
        ],
        "review_history": review_history,
    }

    approved = get_approved_repair_files(state, original, working)

    assert (Path("module.py") in approved) is expected

@pytest.mark.parametrize(
    "remote_url",
    [
        "https://attacker@github.com/owner/repo.git",
        "https://github.com:8443/owner/repo.git",
        "https://github.com/owner/repo.git?token=secret",
        "https://github.com/owner/../repo.git",
        "https://github.com/owner/repo%2Fother.git",
        "ssh://attacker@github.com/owner/repo.git",
    ],
)
def test_reject_unsafe_github_remote_urls(remote_url):
    with pytest.raises(ValueError):
        normalize_github_repository(remote_url)


@pytest.mark.parametrize(
    "sensitive_name",
    [
        ".ENV.staging",
        ".envrc",
        "Credentials.JSON",
        "service.pem",
        "deploy.key",
        "backup.p12",
        "api.secret",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".azure/accessTokens.json",
        ".kube/config",
        ".gnupg/private-keys-v1.d/secret.key",
        ".docker/config.json",
        ".vault-token",
        ".terraform/terraform.tfstate",
        "terraform.tfstate",
        "terraform.tfstate.backup",
        "application_default_credentials.json",
        "service-account.json",
        "service_account.json",
    ],
)
def test_sensitive_repair_files_are_never_approved_for_publishing(
    tmp_path,
    sensitive_name,
):
    original = tmp_path / "original"
    working = tmp_path / "working"
    original.mkdir()
    working.mkdir()
    original_file = original / sensitive_name
    working_file = working / sensitive_name
    original_file.parent.mkdir(parents=True, exist_ok=True)
    working_file.parent.mkdir(parents=True, exist_ok=True)
    original_file.write_text("old value\n", encoding="utf-8")
    working_file.write_text("new value\n", encoding="utf-8")

    state = {
        "repaired_bugs": [
            {
                "status": "REPAIRED",
                "bug_key": "sensitive-file",
                "attempts": 1,
                "file": str(original_file),
            }
        ],
        "review_history": [
            {
                "bug_key": "sensitive-file",
                "decision": "approved",
                "attempt": 1,
            }
        ],
    }

    assert get_approved_repair_files(state, original, working) == []



def test_reject_working_file_symlink_that_escapes_workspace(tmp_path):
    original = tmp_path / "original"
    working = tmp_path / "working"
    outside = tmp_path / "outside.py"
    original.mkdir()
    working.mkdir()
    original_file = original / "module.py"
    original_file.write_text("value = 1\\n", encoding="utf-8")
    outside.write_text("value = 2\\n", encoding="utf-8")
    (working / "module.py").symlink_to(outside)

    state = {
        "repaired_bugs": [
            {
                "status": "REPAIRED",
                "bug_key": "symlink-escape",
                "attempts": 1,
                "file": str(original_file),
            }
        ],
        "review_history": [
            {
                "bug_key": "symlink-escape",
                "decision": "approved",
                "attempt": 1,
            }
        ],
    }

    assert get_approved_repair_files(state, original, working) == []
