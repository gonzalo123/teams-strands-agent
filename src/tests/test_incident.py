import json

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from rq import SimpleWorker
from rq.serializers import JSONSerializer
from strands.models import Model

from teams_agent import agent as agent_module
from teams_agent import jobs
from teams_agent.agent import create_agent
from teams_agent.api import create_app
from teams_agent.cli import main
from teams_agent.incident_tools import (
    get_recent_deployments,
    get_runbook,
    get_service_metrics,
    load_scenario,
)
from teams_agent.models import Task
from teams_agent.queue import enqueue
from teams_agent.security import signature


class ScriptedIncidentModel(Model):
    """Test-only provider: ask Strands to execute tools, then return deterministic text."""

    tool_names = ("get_service_metrics", "get_recent_deployments", "get_runbook")

    def __init__(self):
        self.calls = 0
        self.results = {}
        self.offered_tools = set()

    def get_config(self):
        return {"model_id": "scripted-incident-test"}

    def update_config(self, **model_config):
        raise NotImplementedError

    def structured_output(self, *args, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.offered_tools.update(spec["name"] for spec in tool_specs or [])
        for message in messages:
            for block in message["content"]:
                if "toolResult" in block:
                    result = block["toolResult"]
                    self.results[result["toolUseId"]] = result
        index = self.calls
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if index < len(self.tool_names):
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "name": self.tool_names[index],
                            "toolUseId": f"evidence-{index}",
                        }
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps({"service": "checkout"})}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {
                "contentBlockDelta": {
                    "delta": {
                        "text": (
                            "[Simulation] Checkout latency is 840 ms versus 210 ms. "
                            "Investigate connection pool contention after deployment 1.8.2."
                        )
                    }
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


@pytest.mark.parametrize("tool", [get_service_metrics, get_recent_deployments, get_runbook])
def test_unknown_service_does_not_return_checkout_evidence(tool):
    result = tool("../../unknown")
    assert result["error"] == "unknown_service"
    assert result["available_services"] == ["catalog", "checkout"]
    assert result["source"] == "fictional demo data"
    assert "metrics" not in result
    assert "deployments" not in result


def test_tools_read_packaged_snapshot_outside_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    metrics = get_service_metrics(" CHECKOUT ")["metrics"]
    assert metrics["p95_latency_ms"] / metrics["baseline_p95_latency_ms"] == 4
    assert metrics["database_pool_active_connections"] == metrics["database_pool_max_connections"]
    deployment = get_recent_deployments("checkout")["deployments"][0]
    assert deployment["minutes_ago"] > metrics["degradation_started_minutes_ago"]
    assert get_runbook("checkout")["runbook"]["requires_human_approval"]
    healthy = get_service_metrics("catalog")["metrics"]
    assert healthy["degradation_started_minutes_ago"] is None
    assert healthy["error_rate_percent"] == healthy["baseline_error_rate_percent"]


def test_reading_a_snapshot_does_not_mutate_future_tool_results():
    result = get_service_metrics("checkout")
    result["metrics"]["p95_latency_ms"] = -1
    assert get_service_metrics("checkout")["metrics"]["p95_latency_ms"] == 840


def test_scenario_command_needs_no_credentials_or_settings(monkeypatch, tmp_path):
    for key in ["TEAMS_HMAC_SECRET", "TEAMS_WORKFLOW_URL", "AWS_PROFILE"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["scenario"])
    assert result.exit_code == 0
    assert json.loads(result.output) == load_scenario()


@pytest.mark.parametrize("source", ["teams", "cli"])
def test_worker_runs_real_strands_tools_before_delivering_report(
    settings, queue, activity, monkeypatch, source
):
    model = ScriptedIncidentModel()
    delivered = []
    monkeypatch.setenv("AGENT_BACKEND", "bedrock")
    monkeypatch.setattr(
        agent_module, "create_agent", lambda config: create_agent(config, model=model)
    )
    monkeypatch.setattr(jobs, "send", lambda payload, config: delivered.append(payload))
    prompt = "Investigate checkout latency and propose verification steps."
    if source == "teams":
        activity["text"] = "<at>Assistant</at> " + prompt
        body = json.dumps(activity).encode()
        with TestClient(create_app(settings, queue)) as client:
            response = client.post(
                "/webhooks/teams",
                content=body,
                headers={"Authorization": signature(body, settings.signing_key)},
            )
        assert response.status_code == 200
    else:
        enqueue(queue, Task(prompt=prompt, source="cli"), "incident-cli-test", settings)
    job = queue.jobs[0]
    assert not delivered
    SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(burst=True)
    job.refresh()
    assert job.is_finished
    assert job.meta["workflow_accepted"]
    assert len(delivered) == 1
    assert model.calls == 4
    assert model.offered_tools == set(model.tool_names)
    assert len(model.results) == 3
    for result in model.results.values():
        assert result["status"] == "success"
        evidence = json.loads(result["content"][0]["text"])
        assert evidence["service"] == "checkout"
        assert evidence["source"] == "fictional demo data"
    report = job.meta["answer"]
    assert "[Simulation]" in report
    assert "840 ms" in report
    assert "1.8.2" in report
