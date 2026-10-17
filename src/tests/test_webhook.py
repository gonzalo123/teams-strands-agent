import json
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from teams_agent.api import create_app
from teams_agent.models import Activity
from teams_agent.security import signature


def post(client, activity, settings):
    body = json.dumps(activity).encode()
    return client.post(
        "/webhooks/teams",
        content=body,
        headers={"Authorization": signature(body, settings.signing_key)},
    )


def test_acknowledges_without_running_agent_and_deduplicates(settings, queue, activity):
    with TestClient(create_app(settings, queue)) as client:
        start = time.monotonic()
        first = post(client, activity, settings)
        assert time.monotonic() - start < 2
        second = post(client, activity, settings)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert queue.count == 1
    job = queue.jobs[0]
    assert job.args[0]["prompt"] == "Check Ada & checkout"
    assert job.args[0]["mention"]["id"] == "entra-user"
    assert job.meta == {}


@pytest.mark.parametrize("header", ["", "Bearer token", "HMAC invalid", "HMAC ñ"])
def test_rejects_invalid_signature(settings, queue, activity, header):
    # HTTP headers are latin-1 in TestClient; use UTF-8 bytes for the non-ASCII case.
    with TestClient(create_app(settings, queue)) as client:
        response = client.post(
            "/webhooks/teams",
            content=json.dumps(activity),
            headers={"Authorization": header.encode()},
        )
    assert response.status_code == 401
    assert queue.count == 0


def test_signature_is_over_exact_bytes(settings, queue, activity):
    body = json.dumps(activity).encode()
    with TestClient(create_app(settings, queue)) as client:
        response = client.post(
            "/webhooks/teams",
            content=body + b" ",
            headers={"Authorization": signature(body, settings.signing_key)},
        )
    assert response.status_code == 401


@pytest.mark.parametrize("resource", ["tenant", "team", "channel"])
def test_rejects_unconfigured_destination(settings, queue, activity, resource):
    activity["channelData"][resource]["id"] = "another"
    with TestClient(create_app(settings, queue)) as client:
        assert post(client, activity, settings).status_code == 403
    assert queue.count == 0


def test_does_not_acknowledge_when_queue_fails(settings, activity):
    queue = Mock()
    queue.enqueue.side_effect = ConnectionError("offline")
    with TestClient(create_app(settings, queue)) as client:
        assert post(client, activity, settings).status_code == 503


def test_signed_invalid_json(settings, queue):
    body = b"{bad"
    with TestClient(create_app(settings, queue)) as client:
        response = client.post(
            "/webhooks/teams",
            content=body,
            headers={"Authorization": signature(body, settings.signing_key)},
        )
    assert response.status_code == 400


def test_large_body_is_bounded(settings, queue):
    with TestClient(create_app(settings, queue)) as client:
        assert client.post("/webhooks/teams", content=b"x" * 65537).status_code == 413


def test_empty_question_does_not_enqueue(settings, queue, activity):
    activity["text"] = "<at>Assistant</at>"
    with TestClient(create_app(settings, queue)) as client:
        assert post(client, activity, settings).status_code == 200
    assert queue.count == 0


def test_job_id_includes_channel_scope(activity):
    first = Activity.model_validate(activity).job_id()
    activity["channelData"]["channel"]["id"] = "another"
    assert Activity.model_validate(activity).job_id() != first


@pytest.mark.parametrize("recipient", [None, "missing"])
def test_outgoing_webhook_without_recipient(settings, queue, activity, recipient):
    if recipient == "missing":
        activity.pop("recipient")
    else:
        activity["recipient"] = None
    # Outgoing Webhooks can include only client metadata, without mention entities.
    activity["entities"] = [{"type": "clientInfo", "locale": "en-US", "platform": "Mac"}]
    activity["text"] = "<at>Assistant</at> Hola"
    with TestClient(create_app(settings, queue)) as client:
        response = post(client, activity, settings)
    assert response.status_code == 200
    assert queue.count == 1
    assert queue.jobs[0].args[0]["prompt"] == "Assistant Hola"


def test_validation_logs_field_without_message_content(settings, queue, activity, caplog):
    activity["channelData"].pop("tenant")
    activity["text"] = "private-message-content"
    with TestClient(create_app(settings, queue)) as client:
        assert post(client, activity, settings).status_code == 400
    assert "channelData.tenant" in caplog.text
    assert "private-message-content" not in caplog.text
    assert settings.teams_hmac_secret.get_secret_value() not in caplog.text
    assert queue.count == 0
