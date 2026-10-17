import json
import uuid

import click
import httpx
from redis import Redis
from rq.job import Job
from rq.serializers import JSONSerializer
from rq.worker import SpawnWorker

from teams_agent.models import Mention, Task
from teams_agent.queue import connection, enqueue, task_queue
from teams_agent.security import signature
from teams_agent.settings import Settings


@click.group()
def main():
    """Run the API, consume jobs, or start a proactive Teams message."""


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
def api(host, port):
    import uvicorn

    uvicorn.run("teams_agent.api:create_app", factory=True, host=host, port=port)


@main.command()
@click.option("--burst", is_flag=True, help="Exit after processing available jobs.")
def worker(burst):
    settings = Settings()
    # Blocking dequeue needs a longer socket timeout than the HTTP producer.
    redis = Redis.from_url(settings.redis_url)
    SpawnWorker([task_queue(redis)], connection=redis, serializer=JSONSerializer).work(
        with_scheduler=True, burst=burst
    )


@main.command()
def scenario():
    """Inspect the fictional incident data locally, without AWS or posting to Teams."""
    from teams_agent.incident_tools import load_scenario

    click.echo(json.dumps(load_scenario(), indent=2, ensure_ascii=False))


@main.command()
@click.argument("prompt")
@click.option("--mention-id", help="Microsoft Entra object ID or user principal name.")
@click.option("--mention-name", help="Display name paired with --mention-id.")
def send(prompt, mention_id, mention_name):
    """Ask the agent to write a new message in the configured Teams channel."""
    if bool(mention_id) != bool(mention_name):
        raise click.UsageError("Supply both --mention-id and --mention-name")
    settings = Settings()
    mention = Mention(id=mention_id, name=mention_name) if mention_id else None
    task = Task(prompt=prompt, mention=mention, source="cli")
    with connection(settings) as redis:
        job_id = enqueue(task_queue(redis), task, "cli-" + uuid.uuid4().hex, settings)
    click.echo(job_id)


@main.command()
@click.argument("job_id")
def status(job_id):
    """Inspect a job locally; no public job-status endpoint is exposed."""
    with connection(Settings()) as redis:
        job = Job.fetch(job_id, connection=redis, serializer=JSONSerializer)
        click.echo(
            json.dumps(
                {
                    "id": job.id,
                    "status": job.get_status().value,
                    "workflow_accepted": bool(job.meta.get("workflow_accepted")),
                }
            )
        )


@main.command("demo-request")
@click.argument("prompt", default="What is the status of checkout?")
@click.option("--url", default="http://127.0.0.1:8000/webhooks/teams")
@click.option("--activity-id", default=None, help="Reuse an ID to demonstrate deduplication.")
def demo_request(prompt, url, activity_id):
    """Send a signed synthetic activity to the demo API."""
    settings = Settings()
    payload = {
        "type": "message",
        "id": activity_id or uuid.uuid4().hex,
        "channelId": "msteams",
        "text": f"<at>Assistant</at> {prompt}",
        "from": {"id": "demo-user", "name": "Demo User"},
        "recipient": {"id": "demo-webhook", "name": "Assistant"},
        "channelData": {
            "tenant": {"id": settings.teams_tenant_id},
            "team": {"id": settings.teams_team_id},
            "channel": {"id": settings.teams_channel_id},
        },
        "entities": [
            {
                "type": "mention",
                "text": "<at>Assistant</at>",
                "mentioned": {"id": "demo-webhook", "name": "Assistant"},
            }
        ],
    }
    body = json.dumps(payload).encode()
    response = httpx.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": signature(body, settings.signing_key),
        },
        timeout=5,
    )
    click.echo(f"HTTP {response.status_code} in {response.elapsed.total_seconds():.3f}s")
    click.echo(response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
