import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from rq import SimpleWorker
from rq.job import Job
from rq.serializers import JSONSerializer

from teams_agent import jobs
from teams_agent.api import create_app
from teams_agent.models import Mention, Task
from teams_agent.queue import enqueue
from teams_agent.security import signature
from teams_agent.teams import DeliveryError, card_payload, send


def test_mention_has_matching_entity_and_unicode_payload_fits():
    payload = card_payload(
        "😀" * 10000, "request-12345678", Mention(id="ada@example.com", name="Ada")
    )
    card = payload["attachments"][0]["content"]
    assert card["body"][0]["text"] == card["msteams"]["entities"][0]["text"]
    assert card["msteams"]["entities"][0]["mentioned"]["id"] == "ada@example.com"
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 27000
    assert "truncated" in card["body"][1]["text"]
    assert "msteams" not in card_payload("hello", "id", None)["attachments"][0]["content"]


@pytest.mark.parametrize("status", [200, 202, 400, 429, 500, 302])
def test_workflow_status_and_secret_redaction(settings, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="secret data"))
    with httpx.Client(transport=transport) as client:
        if status in (200, 202):
            send(card_payload("hello", "id", None), settings, client)
        else:
            with pytest.raises(DeliveryError) as caught:
                send(card_payload("hello", "id", None), settings, client)
            assert str(status) in str(caught.value)
            assert "secret" not in str(caught.value)


def test_transport_failure_does_not_expose_workflow_url(settings):
    def timeout(request):
        raise httpx.ReadTimeout(str(request.url), request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as client,
        pytest.raises(DeliveryError) as caught,
    ):
        send({}, settings, client)
    assert "secret" not in str(caught.value)
    assert "unknown" in str(caught.value)


def test_delivery_retry_reuses_saved_answer(settings, monkeypatch):
    job = Mock(id="job", meta={})
    generate = Mock(return_value="answer")
    deliver = Mock(side_effect=[DeliveryError("HTTP 503"), None])
    monkeypatch.setattr(jobs, "get_current_job", lambda: job)
    monkeypatch.setattr(jobs, "answer", generate)
    monkeypatch.setattr(jobs, "send", deliver)
    task = Task(prompt="hello", source="cli").model_dump()
    with pytest.raises(DeliveryError):
        jobs.run(task)
    assert job.meta["answer"] == "answer"
    assert jobs.run(task) == {"status": "workflow_accepted"}
    assert jobs.run(task) == {"status": "workflow_accepted"}
    generate.assert_called_once()
    assert deliver.call_count == 2


def test_full_webhook_queue_worker_flow(settings, queue, activity, monkeypatch):
    delivered = []
    monkeypatch.setattr(jobs, "send", lambda payload, settings: delivered.append(payload))
    body = json.dumps(activity).encode()
    with TestClient(create_app(settings, queue)) as client:
        response = client.post(
            "/webhooks/teams",
            content=body,
            headers={"Authorization": signature(body, settings.signing_key)},
        )
    assert response.status_code == 200
    job = queue.jobs[0]
    assert not delivered
    SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(burst=True)
    job.refresh()
    assert job.is_finished
    assert job.meta["workflow_accepted"]
    assert len(delivered) == 1
    assert "Local demo" in delivered[0]["attachments"][0]["content"]["body"][1]["text"]


def test_rq_schedules_delivery_failure_for_retry(settings, queue, monkeypatch):
    monkeypatch.setattr(jobs, "send", Mock(side_effect=DeliveryError("HTTP 503")))
    job_id = enqueue(queue, Task(prompt="hello", source="cli"), "retry-test", settings)
    SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(burst=True)
    job = Job.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
    assert job.get_status().value == "scheduled"
    assert job.retries_left == 2
    assert "answer" in job.meta
