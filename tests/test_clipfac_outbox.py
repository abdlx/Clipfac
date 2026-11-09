"""Persistent callback IDs, retries, leases and secret handling."""
import json
import pytest
from clipfac import outbox


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "outbox.db"))
    return tmp_path


def enqueue():
    return outbox.enqueue("job-1", "https://callback.example/hook", "private-secret",
                          {"event": "job.completed", "job_id": "job-1", "clips": []},
                          {"status": "completed", "result": {"clips": []}, "logs": []})


def test_duplicate_enqueue_keeps_one_stable_delivery_id(db):
    identity = enqueue()
    assert enqueue() == identity
    events = outbox.deliveries("job-1")
    assert len(events) == 1 and events[0]["state"] == "pending"
    assert "secret" not in events[0] and "body" not in events[0]
    assert outbox.terminal("job-1")["status"] == "completed"


def test_claim_and_expired_lease_are_exclusive(db):
    enqueue()
    event = outbox.claim(now=10**12)
    assert event is not None and event["attempts"] == 1
    assert outbox.claim(now=10**12 + 1) is None
    reclaimed = outbox.claim(now=10**12 + 91)
    assert reclaimed["id"] == event["id"]
    assert reclaimed["lease"] != event["lease"]
    # An old worker cannot acknowledge a newer worker's lease.
    outbox.settle(event, True, now=10**12 + 92)
    assert outbox.deliveries("job-1")[0]["state"] == "sending"
    outbox.settle(reclaimed, True, now=10**12 + 93)
    assert outbox.deliveries("job-1")[0]["state"] == "delivered"


def test_retry_backoff_and_dead_letter_replay(db):
    identity = enqueue()
    event = outbox.claim(now=10**12)
    outbox.settle(event, False, "HTTP 503", now=10**12)
    assert outbox.claim(now=10**12 + 9) is None
    event = outbox.claim(now=10**12 + 10)
    assert event["attempts"] == 2
    outbox.settle(event, False, "HTTP 410", permanent=True, now=10**12 + 10)
    assert outbox.deliveries("job-1")[0]["state"] == "dead_letter"
    assert outbox.retry(identity)
    assert outbox.deliveries("job-1")[0]["attempts"] == 0
    assert outbox.deliveries("job-1")[0]["id"] == identity


def test_staged_submission_does_not_store_callback_secret(db):
    from clipfac.models import SubmitAnalysis
    from clipfac.pipeline_api import safe_arguments
    body = SubmitAnalysis(source_url="https://example.com/v", confirm_rights=True,
                          idempotency_key="analysis-callback", webhook_secret="private-secret")
    arguments = safe_arguments(body)
    assert "private-secret" not in json.dumps(arguments)
    assert "webhook_secret" not in arguments
    assert len(arguments["webhook_secret_digest"]) == 64


def test_delivery_signatures_and_connect_time_guard(db, monkeypatch):
    import httpx
    import ssrf_guard
    import security_utils
    from contextlib import nullcontext
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, content, headers):
            captured.update(headers=headers, body=content)
            return httpx.Response(204)
    monkeypatch.setattr(outbox.httpx, "Client", Client)
    monkeypatch.setattr(ssrf_guard, "guarded", nullcontext)
    monkeypatch.setattr(security_utils, "assert_public_url", lambda url: url)
    enqueue()
    event = outbox.claim(now=10**12)
    assert outbox.send_once(event) == (True, None, False)
    assert captured["headers"]["X-Clipfac-Delivery-ID"] == event["id"]
    assert captured["headers"]["X-Clipfac-Signature"].startswith("v1=")
    assert captured["headers"]["X-OpenShorts-Signature"].startswith("sha256=")
    assert json.loads(captured["body"])["delivery_id"] == event["id"]
