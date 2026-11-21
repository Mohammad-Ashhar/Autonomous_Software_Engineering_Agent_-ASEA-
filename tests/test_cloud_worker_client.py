import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from asea.api.cloud_worker_client import (
    CloudWorkerAPIError,
    CloudWorkerClient,
    normalize_api_url,
)


def test_cloud_worker_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        CloudWorkerClient("http://example.com")


def test_cloud_worker_rejects_url_credentials_and_query():
    with pytest.raises(ValueError):
        normalize_api_url("https://user:pass@example.com")
    with pytest.raises(ValueError):
        normalize_api_url("https://example.com/api?token=secret")


def test_cloud_worker_normalizes_api_url():
    assert normalize_api_url(" https://EXAMPLE.com:443/ ") == "https://example.com"


def test_authenticated_request_sends_bearer_and_json():
    response = MagicMock()
    response.read.return_value = b'{"status":"ok"}'
    response.__enter__.return_value = response
    client = CloudWorkerClient("https://example.com", "worker-secret")
    with patch(
        "asea.api.cloud_worker_client.urlopen", return_value=response
    ) as open_url:
        assert client.heartbeat("0.1.0") == {"status": "ok"}
    request = open_url.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer worker-secret"
    assert json.loads(request.data) == {"version": "0.1.0", "capabilities": {}}


def test_request_does_not_require_worker_secret_for_enrollment():
    response = MagicMock()
    response.read.return_value = b'{"worker_id":"id","worker_secret":"secret"}'
    response.__enter__.return_value = response
    client = CloudWorkerClient("https://example.com")
    with patch(
        "asea.api.cloud_worker_client.urlopen", return_value=response
    ) as open_url:
        result = client.enroll("one-time-code")
    assert result["worker_id"] == "id"
    request = open_url.call_args.args[0]
    assert request.get_header("Authorization") is None


def test_api_error_does_not_echo_authorization_header():
    error = HTTPError(
        "https://example.com", 401, "Unauthorized", {}, BytesIO(b'{"detail":"revoked"}')
    )
    client = CloudWorkerClient("https://example.com", "top-secret")
    with patch("asea.api.cloud_worker_client.urlopen", side_effect=error):
        with pytest.raises(CloudWorkerAPIError, match="revoked") as raised:
            client.heartbeat("0.1.0")
    assert "top-secret" not in str(raised.value)



def test_pause_for_review_sends_checkpoint_and_fencing_data():
    response = MagicMock()
    response.read.return_value = b'{"status":"waiting_for_review"}'
    response.__enter__.return_value = response
    client = CloudWorkerClient("https://example.com", "worker-secret")
    with patch(
        "asea.api.cloud_worker_client.urlopen", return_value=response
    ) as open_url:
        result = client.pause_for_review(
            "job-1",
            "lease-1",
            3,
            {"patch": "diff"},
            {"thread_id": "job-1", "worker_id": "worker-1"},
            "review-job-1",
        )
    assert result["status"] == "waiting_for_review"
    request = open_url.call_args.args[0]
    payload = json.loads(request.data)
    assert payload["lease_id"] == "lease-1"
    assert payload["fencing_generation"] == 3
    assert payload["checkpoint_reference"]["thread_id"] == "job-1"
