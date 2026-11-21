"""Server-side verification of Supabase Auth access tokens.

The browser's user ID is never trusted. The API validates the bearer token
against Supabase Auth and derives the identity from the authenticated response.
"""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID


class SupabaseAuthError(Exception):
    """Raised when Supabase configuration or an access token is invalid."""


def supabase_auth_configuration() -> tuple[str, str]:
    """Return validated Supabase URL and public anon key from the environment."""
    raw_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    parsed = urlparse(raw_url)

    if (
        not raw_url
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise SupabaseAuthError("SUPABASE_URL must be a valid HTTPS project URL.")
    if not anon_key:
        raise SupabaseAuthError("SUPABASE_ANON_KEY must be configured.")
    return raw_url, anon_key


def verify_supabase_access_token(access_token: str) -> str:
    """Verify a Supabase JWT with Auth and return its canonical user UUID.

    Calling the Auth user endpoint avoids trusting unverified JWT claims and
    keeps signing-key rotation and token validation with the identity provider.
    """
    if not isinstance(access_token, str) or not access_token.strip():
        raise SupabaseAuthError("A valid Supabase access token is required.")

    project_url, anon_key = supabase_auth_configuration()
    request = Request(
        f"{project_url}/auth/v1/user",
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {access_token.strip()}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in {400, 401, 403}:
            raise SupabaseAuthError("The Supabase access token is invalid or expired.") from None
        raise SupabaseAuthError("Supabase authentication is temporarily unavailable.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise SupabaseAuthError("Supabase authentication is temporarily unavailable.") from None

    if not isinstance(payload, dict):
        raise SupabaseAuthError("Supabase returned an invalid identity response.")
    try:
        return str(UUID(payload["id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SupabaseAuthError("Supabase returned an invalid user identity.") from None
