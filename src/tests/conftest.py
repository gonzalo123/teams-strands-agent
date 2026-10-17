import base64

import fakeredis
import pytest

from teams_agent.queue import task_queue
from teams_agent.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    values = {
        "TEAMS_HMAC_SECRET": base64.b64encode(b"test-secret").decode(),
        "TEAMS_TENANT_ID": "tenant",
        "TEAMS_TEAM_ID": "team",
        "TEAMS_CHANNEL_ID": "channel",
        "TEAMS_WORKFLOW_URL": "https://example.test/workflow?sig=secret",
        "AGENT_BACKEND": "demo",
        "DEMO_DELAY_SECONDS": "0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


@pytest.fixture
def queue():
    return task_queue(fakeredis.FakeRedis())


@pytest.fixture
def activity():
    return {
        "type": "message",
        "id": "activity-123",
        "channelId": "msteams",
        "text": "<at>Assistant</at> Check <at>Ada</at> &amp; checkout",
        "recipient": {"id": "assistant", "name": "Assistant"},
        "from": {"id": "user", "name": "Ada", "aadObjectId": "entra-user"},
        "channelData": {
            "tenant": {"id": "tenant"},
            "team": {"id": "team"},
            "channel": {"id": "channel"},
        },
        "entities": [
            {
                "type": "mention",
                "text": "<at>Assistant</at>",
                "mentioned": {"id": "assistant", "name": "Assistant"},
            }
        ],
    }
