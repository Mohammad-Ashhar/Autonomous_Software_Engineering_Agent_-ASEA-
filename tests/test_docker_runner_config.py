import pytest

from asea.tools.test_runner import DockerTestRunner


@pytest.mark.parametrize(
    "timeout",
    [0, -1, 3601, float("inf"), float("nan"), True, "120"],
)
def test_runner_rejects_invalid_timeout(monkeypatch, timeout):
    def unexpected_client():
        raise AssertionError(
            "Docker client must not initialize for invalid settings."
        )

    monkeypatch.setattr("asea.tools.test_runner.docker.from_env", unexpected_client)
    with pytest.raises(ValueError, match="timeout"):
        DockerTestRunner(timeout=timeout)


@pytest.mark.parametrize(
    "memory_limit",
    ["", "0m", "15m", "9g", "-1m", "512mb", "512 m", "512m;rm -rf /"],
)
def test_runner_rejects_invalid_memory_limits(monkeypatch, memory_limit):
    def unexpected_client():
        raise AssertionError(
            "Docker client must not initialize for invalid settings."
        )

    monkeypatch.setattr("asea.tools.test_runner.docker.from_env", unexpected_client)
    with pytest.raises(ValueError, match="Memory limit"):
        DockerTestRunner(memory_limit=memory_limit)


@pytest.mark.parametrize("image", ["", "   ", None])
def test_runner_rejects_invalid_image(monkeypatch, image):
    def unexpected_client():
        raise AssertionError("Docker client must not be initialized for invalid settings.")

    monkeypatch.setattr("asea.tools.test_runner.docker.from_env", unexpected_client)
    with pytest.raises(ValueError, match="image"):
        DockerTestRunner(image=image)


@pytest.mark.parametrize(
    ("timeout", "memory_limit"),
    [(1, "16m"), (120, "512m"), (3600, "8g"), (30.5, "536870912")],
)
def test_runner_accepts_bounded_resource_settings(
    monkeypatch, timeout, memory_limit
):
    class FakeClient:
        pass

    monkeypatch.setattr(
        "asea.tools.test_runner.docker.from_env",
        lambda: FakeClient(),
    )
    runner = DockerTestRunner(timeout=timeout, memory_limit=memory_limit)

    assert runner.timeout == float(timeout)
    assert runner.memory_limit == memory_limit.lower()
