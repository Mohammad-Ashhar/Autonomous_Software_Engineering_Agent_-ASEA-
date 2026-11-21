import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from asea.api.cloud_worker import (
    CloudWorkerAPIError,
    CloudWorkerClient,
)


def test_cloud_worker_client_requires_https_outside_localhost():
    with pytest.raises(ValueError, match="HTTPS"):
        CloudWorkerClient("http://worker.example.com", "secret")
    client = CloudWorkerClient("http://127.0.0.1:8000", "secret")
    assert client.base_url == "http://127.0.0.1:8000"


def test_cloud_worker_client_sends_bearer_and_json_payload():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"status":"ok"}'

    client = CloudWorkerClient("https://asea.example", "worker-secret")
    with patch("urllib.request.urlopen", return_value=Response()) as open_url:
        result = client.request("POST", "/v1/worker/heartbeat", {"version": "test"})

    request = open_url.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer worker-secret"
    assert json.loads(request.data) == {"version": "test"}
    assert result == {"status": "ok"}


def test_cloud_worker_client_surfaces_http_status_and_safe_detail():
    failure = urllib.error.HTTPError(
        "https://asea.example/v1/worker/jobs/claim",
        409,
        "Conflict",
        hdrs=None,
        fp=io.BytesIO(b'{"detail":"Lease expired."}'),
    )
    client = CloudWorkerClient("https://asea.example", "worker-secret")
    with patch("urllib.request.urlopen", side_effect=failure):
        with pytest.raises(CloudWorkerAPIError) as caught:
            client.request("POST", "/v1/worker/jobs/claim", {})
    assert caught.value.status == 409
    assert str(caught.value) == "Lease expired."


def test_cloud_worker_client_rejects_non_object_response():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'[]'

    client = CloudWorkerClient("https://asea.example", "worker-secret")
    with patch("urllib.request.urlopen", return_value=Response()):
        with pytest.raises(CloudWorkerAPIError, match="invalid response"):
            client.request("POST", "/v1/worker/jobs/claim", {})
