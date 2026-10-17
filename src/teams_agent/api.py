import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from redis.exceptions import RedisError

from teams_agent.models import Activity, Mention, Task
from teams_agent.queue import connection, enqueue, task_queue
from teams_agent.security import authenticated
from teams_agent.settings import Settings

logger = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None, queue=None) -> FastAPI:
    settings = settings or Settings()
    redis = connection(settings) if queue is None else None
    queue = queue if queue is not None else task_queue(redis)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if redis is not None:
            redis.close()

    app = FastAPI(title="Teams agent", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/webhooks/teams")
    async def webhook(request: Request):
        try:
            async with asyncio.timeout(1):
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise HTTPException(413, "Request too large")
        except TimeoutError:
            raise HTTPException(408, "Request body timed out") from None
        raw = bytes(body)
        if not authenticated(raw, request.headers.get("authorization", ""), settings.signing_key):
            raise HTTPException(401, "Invalid signature")
        try:
            activity = Activity.model_validate_json(raw)
        except ValidationError as exc:
            issues = [
                {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
            logger.warning("Invalid Teams activity fields: %s", issues)
            raise HTTPException(400, "Invalid Teams message") from None
        context = activity.channelData
        if (context.tenant.id, context.team.id, context.channel.id) != (
            settings.teams_tenant_id,
            settings.teams_team_id,
            settings.teams_channel_id,
        ):
            raise HTTPException(403, "Channel is not configured")
        prompt = activity.prompt()
        if not prompt:
            return {"type": "message", "text": "Please include a question after the mention."}
        mention = (
            Mention(id=activity.sender.aadObjectId, name=activity.sender.name)
            if activity.sender.aadObjectId
            else None
        )
        task = Task(prompt=prompt, mention=mention, source="teams")
        try:
            job_id = await asyncio.wait_for(
                asyncio.to_thread(enqueue, queue, task, activity.job_id(), settings), timeout=2
            )
        except (RedisError, TimeoutError):
            raise HTTPException(503, "Queue unavailable; retry the request") from None
        return {
            "type": "message",
            "text": (
                f"Request {job_id[-8:]} queued. I will post the result as a new message in this channel."
            ),
        }

    return app
