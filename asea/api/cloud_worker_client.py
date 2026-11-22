"""HTTPS client for the ASEA cloud-worker protocol.

This module contains transport and enrollment primitives only. Job execution is
provided by the separate cloud-worker runtime added in a later delivery step.
"""
from __future__ import annotations

import json
import platform
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class CloudWorkerAPIError(RuntimeError):
    """A cloud-worker API request failed."""

    def __init__(self, status_code: int | None, message: str):
        self.status_code = status_code
        super().__init__(message)


def normalize_api_url(api_url: str) -> str:
    """Validate and normalize a cloud API base URL; HTTPS is mandatory."""
    if not isinstance(api_url, str):
        raise ValueError("The ASEA API URL must be an HTTPS URL.")
    parsed = urlsplit(api_url.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("The ASEA API URL contains an invalid port.") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The ASEA cloud worker requires a valid HTTPS API URL.")
    host = parsed.hostname.lower()
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


class CloudWorkerClient:
    """Small synchronous HTTPS client for cloud worker enrollment and leases."""

    def __init__(
        self,
        api_url: str,
        worker_secret: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_url = normalize_api_url(api_url)
        self.worker_secret = worker_secret
        if timeout <= 0:
            raise ValueError("Request timeout must be positive.")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        authenticated: bool = True,
    ) -> dict:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API request path must be an absolute path.")
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self.worker_secret:
                raise CloudWorkerAPIError(
                    None, "Worker credentials are not configured."
                )
            headers["Authorization"] = f"Bearer {self.worker_secret}"
        request = Request(
            f"{self.api_url}{path}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_100_000)
        except HTTPError as error:
            try:
                response_body = error.read(16_384).decode("utf-8", errors="replace")
                detail = json.loads(response_body).get("detail", "Request rejected.")
            except (ValueError, AttributeError):
                detail = "Request rejected by the cloud API."
            raise CloudWorkerAPIError(error.code, str(detail)[:500]) from None
        except (URLError, TimeoutError, OSError) as error:
            # Do not include request headers or credential-bearing objects.
            reason = getattr(error, "reason", None)
            detail = (
                type(reason).__name__ if reason is not None else type(error).__name__
            )
            raise CloudWorkerAPIError(
                None, f"Cloud API connection failed ({detail})."
            ) from None

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise CloudWorkerAPIError(
                None, "Cloud API returned an invalid JSON response."
            ) from None
        if not isinstance(decoded, dict):
            raise CloudWorkerAPIError(
                None, "Cloud API returned an unexpected response."
            )
        return decoded

    def enroll(self, enrollment_code: str) -> dict:
        """Exchange a one-time enrollment code for a worker credential."""
        return self.request(
            "POST",
            "/v1/worker/enroll",
            {
                "code": enrollment_code,
                "version": "0.1.0",
                "capabilities": {
                    "os": platform.system(),
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                },
            },
            authenticated=False,
        )

    def heartbeat(self, version: str, capabilities: dict | None = None) -> dict:
        return self.request(
            "POST",
            "/v1/worker/heartbeat",
            {"version": version, "capabilities": capabilities or {}},
        )

    def claim_job(self) -> dict:
        return self.request("POST", "/v1/worker/jobs/claim", {})

    def renew_lease(self, job_id: str, lease_id: str, generation: int) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job_id}/heartbeat",
            {"lease_id": lease_id, "fencing_generation": generation},
        )

    def append_event(
        self,
        job_id: str,
        lease_id: str,
        generation: int,
        event_type: str,
        payload: dict,
        idempotency_key: str,
    ) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job_id}/events",
            {
                "lease_id": lease_id,
                "fencing_generation": generation,
                "event_type": event_type,
                "payload": payload,
                "idempotency_key": idempotency_key,
            },
        )

    def pause_for_review(
        self,
        job_id: str,
        lease_id: str,
        generation: int,
        review_request: dict,
        checkpoint_reference: dict,
        idempotency_key: str,
    ) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job_id}/pause-for-review",
            {
                "lease_id": lease_id,
                "fencing_generation": generation,
                "review_request": review_request,
                "checkpoint_reference": checkpoint_reference,
                "idempotency_key": idempotency_key,
            },
        )

    def complete_job(
        self,
        job_id: str,
        lease_id: str,
        generation: int,
        result: dict,
        idempotency_key: str,
    ) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job_id}/complete",
            {
                "lease_id": lease_id,
                "fencing_generation": generation,
                "result": result,
                "idempotency_key": idempotency_key,
            },
        )

    def fail_job(
        self,
        job_id: str,
        lease_id: str,
        generation: int,
        error: str,
        idempotency_key: str,
    ) -> dict:
        return self.request(
            "POST",
            f"/v1/worker/jobs/{job_id}/fail",
            {
                "lease_id": lease_id,
                "fencing_generation": generation,
                "error": error[:1800],
                "idempotency_key": idempotency_key,
            },
        )


def _credential_username(api_url: str, profile: str = "default") -> str:
    """Return an isolated credential-vault key for an API URL and profile."""
    if not isinstance(profile, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", profile
    ):
        raise ValueError(
            "Worker profile must be 1–32 characters: letters, numbers, dots, "
            "underscores, or hyphens."
        )
    normalized_url = normalize_api_url(api_url)
    normalized_profile = profile.lower()
    # Preserve the original credential key so existing default enrollments
    # continue working without requiring users to enroll again.
    if normalized_profile == "default":
        return normalized_url
    return f"{normalized_url}|{normalized_profile}"


def save_worker_credentials(
    api_url: str,
    worker_id: str,
    worker_secret: str,
    profile: str = "default",
) -> None:
    """Store a profile-specific worker secret in the OS credential vault."""
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError(
            'Install the cloud-worker extra with "pip install -e .[cloud-worker]".'
        ) from error
    keyring.set_password(
        "asea-cloud-worker",
        _credential_username(api_url, profile),
        json.dumps({"worker_id": worker_id, "worker_secret": worker_secret}),
    )


def load_worker_credentials(api_url: str, profile: str = "default") -> dict | None:
    """Read profile-specific worker credentials from the OS credential vault."""
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError(
            'Install the cloud-worker extra with "pip install -e .[cloud-worker]".'
        ) from error
    username = _credential_username(api_url, profile)
    stored = keyring.get_password("asea-cloud-worker", username)
    if stored is None:
        return None
    try:
        credentials = json.loads(stored)
    except ValueError:
        raise RuntimeError("Stored ASEA worker credentials are invalid.") from None
    if not isinstance(credentials, dict) or not all(
        isinstance(credentials.get(key), str) and credentials[key]
        for key in ("worker_id", "worker_secret")
    ):
        raise RuntimeError("Stored ASEA worker credentials are invalid.")
    return credentials
