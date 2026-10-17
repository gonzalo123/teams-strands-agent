import hashlib
import html
import json
import re
from typing import Literal

from pydantic import BaseModel, Field


class Identity(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(default="User", max_length=200)
    aadObjectId: str | None = None


class Entity(BaseModel):
    type: str
    text: str = ""
    mentioned: Identity | None = None


class Resource(BaseModel):
    id: str = Field(min_length=1)


class ChannelData(BaseModel):
    tenant: Resource
    team: Resource
    channel: Resource


class Activity(BaseModel):
    type: Literal["message"]
    id: str = Field(min_length=1, max_length=512)
    channelId: Literal["msteams"]
    text: str = Field(min_length=1, max_length=12000)
    recipient: Identity | None = None
    sender: Identity = Field(alias="from")
    channelData: ChannelData
    entities: list[Entity] = Field(default_factory=list)

    def prompt(self) -> str:
        # Remove only the webhook's mention, preserving mentions of other people.
        text = self.text
        for entity in self.entities:
            if (
                entity.type == "mention"
                and entity.mentioned
                and self.recipient
                and entity.mentioned.id == self.recipient.id
                and entity.text
            ):
                text = text.replace(entity.text, "")
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()

    def job_id(self) -> str:
        scope = [
            self.channelData.tenant.id,
            self.channelData.team.id,
            self.channelData.channel.id,
            self.id,
        ]
        return "teams-" + hashlib.sha256(json.dumps(scope).encode()).hexdigest()


class Mention(BaseModel):
    id: str = Field(min_length=1, max_length=320)
    name: str = Field(min_length=1, max_length=200)


class Task(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    mention: Mention | None = None
    source: Literal["teams", "cli"]
