from __future__ import annotations

import time
from typing import TYPE_CHECKING

from teams_agent.prompts import INCIDENT_SYSTEM_PROMPT
from teams_agent.settings import Settings

if TYPE_CHECKING:
    from strands import Agent
    from strands.models import Model


def create_agent(settings: Settings, model: Model | None = None) -> Agent:
    from strands import Agent

    from teams_agent.incident_tools import (
        get_recent_deployments,
        get_runbook,
        get_service_metrics,
    )

    if model is None:
        import boto3
        from strands.models import BedrockModel

        session = boto3.Session(region_name=settings.aws_region, profile_name=settings.aws_profile)
        model = BedrockModel(
            model_id=settings.bedrock_model_id,
            boto_session=session,
            temperature=0.2,
            max_tokens=1200,
        )
    return Agent(
        model=model,
        tools=[get_service_metrics, get_recent_deployments, get_runbook],
        callback_handler=None,
        system_prompt=INCIDENT_SYSTEM_PROMPT,
    )


def answer(prompt: str, settings: Settings) -> str:
    if settings.agent_backend == "demo":
        time.sleep(settings.demo_delay_seconds)
        return f"[Local demo — no LLM] Received: {prompt}"
    return str(create_agent(settings)(prompt))
