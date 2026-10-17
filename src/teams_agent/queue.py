from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry as RedisRetry
from rq import Queue, Retry
from rq.exceptions import DuplicateJobError
from rq.serializers import JSONSerializer

from teams_agent.models import Task
from teams_agent.settings import Settings


def connection(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=0.5,
        socket_timeout=1,
        retry=RedisRetry(NoBackoff(), 0),
    )


def task_queue(redis: Redis) -> Queue:
    return Queue("teams", connection=redis, serializer=JSONSerializer)


def enqueue(queue: Queue, task: Task, job_id: str, settings: Settings) -> str:
    try:
        queue.enqueue(
            "teams_agent.jobs.run",
            task.model_dump(),
            job_id=job_id,
            unique=True,
            job_timeout=settings.job_timeout,
            result_ttl=settings.retention_seconds,
            failure_ttl=settings.retention_seconds,
            retry=Retry(max=3, interval=[10, 30, 60]),
            description=f"Teams request {job_id}",
        )
    except DuplicateJobError:
        pass
    return job_id
