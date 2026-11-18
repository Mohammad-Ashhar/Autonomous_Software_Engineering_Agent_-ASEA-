from asea.api import dev_server


def test_development_server_disables_auto_reload(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(dev_server.uvicorn, "run", fake_run)
    monkeypatch.setenv("ASEA_API_HOST", "0.0.0.0")
    monkeypatch.setenv("ASEA_API_PORT", "8123")

    dev_server.run()

    assert calls == [
        (
            ("asea.api.main:app",),
            {
                "host": "0.0.0.0",
                "port": 8123,
                "reload": False,
            },
        )
    ]
