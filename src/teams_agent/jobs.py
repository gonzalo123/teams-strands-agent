import logging

from rq import get_current_job

from teams_agent.agent import answer
from teams_agent.models import Task
from teams_agent.settings import Settings
from teams_agent.teams import card_payload, send

logger = logging.getLogger(__name__)


def run(data: dict) -> dict:
    job = get_current_job()
    if job is None:
        raise RuntimeError("Run this function through the RQ worker")
    settings = Settings()
    task = Task.model_validate(data)
    if job.meta.get("workflow_accepted"):
        return {"status": "workflow_accepted"}
    if "answer" not in job.meta:
        job.meta["answer"] = answer(task.prompt, settings)
        job.save_meta()  # Delivery retries reuse this answer without another model call.
    send(card_payload(job.meta["answer"], job.id, task.mention), settings)
    job.meta["workflow_accepted"] = True
    job.save_meta()
    logger.info("Workflow accepted request %s", job.id)
    return {"status": "workflow_accepted"}
