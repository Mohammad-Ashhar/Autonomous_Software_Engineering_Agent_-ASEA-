import pytest

from asea.api import main


def test_local_paths_are_preserved_after_trimming():
    assert main.validate_api_repository("  /tmp/my-repository  ") == (
        "/tmp/my-repository"
    )


def test_plain_https_github_url_is_accepted():
    url = "https://github.com/owner/repository"

    assert main.validate_api_repository(url) == url


@pytest.mark.parametrize(
    "repository",
    [
        "   ",
        "http://github.com/owner/repository",
        "ssh://git@github.com/owner/repository",
        "git://github.com/owner/repository",
        "github.com/owner/repository",
        "www.github.com/owner/repository",
        "https://example.com/owner/repository",
        "https://github.com/owner",
    ],
)
def test_malformed_remote_repository_is_rejected(repository):
    with pytest.raises(ValueError):
        main.validate_api_repository(repository)
