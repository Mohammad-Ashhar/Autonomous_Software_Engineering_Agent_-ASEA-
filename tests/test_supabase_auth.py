import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from asea.api import supabase_auth


def configure_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")


def test_verifies_token_and_returns_canonical_user_id(monkeypatch):
    configure_supabase(monkeypatch)
    user_id = "A0Eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"id": user_id}).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["apikey"] = request.get_header("Apikey")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(supabase_auth, "urlopen", fake_urlopen)

    result = supabase_auth.verify_supabase_access_token(" user-access-token ")

    assert result == user_id.lower()
    assert captured == {
        "url": "https://example.supabase.co/auth/v1/user",
        "authorization": "Bearer user-access-token",
        "apikey": "public-anon-key",
        "timeout": 8,
    }


def test_rejects_missing_token():
    with pytest.raises(supabase_auth.SupabaseAuthError):
        supabase_auth.verify_supabase_access_token("")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.supabase.co",
        "https://user:password@example.supabase.co",
        "https://example.supabase.co/path",
    ],
)
def test_rejects_invalid_project_url(monkeypatch, url):
    monkeypatch.setenv("SUPABASE_URL", url)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")

    with pytest.raises(supabase_auth.SupabaseAuthError):
        supabase_auth.verify_supabase_access_token("token")


def test_rejects_expired_or_invalid_token(monkeypatch):
    configure_supabase(monkeypatch)

    def fake_urlopen(_request, timeout):
        raise HTTPError("https://example.supabase.co/auth/v1/user", 401, "Unauthorized", {}, BytesIO())

    monkeypatch.setattr(supabase_auth, "urlopen", fake_urlopen)

    with pytest.raises(supabase_auth.SupabaseAuthError, match="invalid or expired"):
        supabase_auth.verify_supabase_access_token("expired-token")


def test_rejects_response_without_valid_user_id(monkeypatch):
    configure_supabase(monkeypatch)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"id":"not-a-uuid"}'

    monkeypatch.setattr(supabase_auth, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(supabase_auth.SupabaseAuthError, match="invalid user identity"):
        supabase_auth.verify_supabase_access_token("valid-looking-token")
