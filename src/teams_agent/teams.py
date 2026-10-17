import html
import json

import httpx

from teams_agent.models import Mention
from teams_agent.settings import Settings


class DeliveryError(Exception):
    """Safe to persist in RQ: never includes the secret workflow URL."""


def card_payload(answer: str, job_id: str, mention: Mention | None) -> dict:
    # Bound UTF-8 bytes, not characters, to leave room below Teams' card limit.
    text = answer.encode("utf-8")[:16000].decode("utf-8", errors="ignore")
    if text != answer:
        text += "\n[Response truncated]"
    body = [
        {"type": "TextBlock", "text": text, "wrap": True},
        {"type": "TextBlock", "text": f"Request {job_id[-8:]}", "isSubtle": True},
    ]
    card = {
        "type": "AdaptiveCard",
        "version": "1.2",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": body,
    }
    if mention:
        tag = f"<at>{html.escape(mention.name)}</at>"
        body.insert(0, {"type": "TextBlock", "text": tag, "wrap": True})
        card["msteams"] = {
            "entities": [{"type": "mention", "text": tag, "mentioned": mention.model_dump()}]
        }
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def send(payload: dict, settings: Settings, client: httpx.Client | None = None) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > 27000:
        raise DeliveryError("Card payload is too large")
    own_client = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        try:
            response = client.post(
                settings.teams_workflow_url.get_secret_value(),
                content=data,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            raise DeliveryError("Workflow transport failure; acceptance is unknown") from None
        if not 200 <= response.status_code < 300:
            raise DeliveryError(f"Workflow returned HTTP {response.status_code}")
    finally:
        if own_client:
            client.close()
