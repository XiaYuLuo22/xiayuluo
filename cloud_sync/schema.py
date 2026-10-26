"""Closed text-only sync schema. Model credentials, screenshots and training files
have no protocol fields and must not be uploaded by a client.
"""
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_-]{1,128}$')]


def _timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Sync timestamps must be ISO 8601 and include a timezone.')
    return value  # Never normalize bytes of an idempotent event.


Timestamp = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(_timestamp)]
MAX_DOCUMENT = 1024 * 1024


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SessionPayload(Closed):
    title: str = Field(min_length=1, max_length=200)
    created_at: Timestamp


class MessagePayload(Closed):
    session_id: Identifier
    # Cloud history cannot introduce high-priority system/tool instructions.
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=64000)
    created_at: Timestamp


class MemoryPayload(Closed):
    tier: Literal['first', 'second', 'permanent']
    kind: Literal['fact', 'procedure', 'episode']
    content: str = Field(min_length=1, max_length=2000)
    created_at: Timestamp
    accessed_at: Timestamp
    expires_at: Timestamp | None
    uses: int = Field(ge=0, le=2**31-1)
    source_session: Identifier | None


class Event(Closed):
    event_id: Identifier
    entity: Literal['session', 'message', 'memory']
    entity_id: Identifier
    operation: Literal['upsert', 'delete']
    base_revision: int = Field(ge=0, le=2**63-2)
    payload: dict

    @model_validator(mode='after')
    def validate_payload(self):
        if len(json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()) > 64000:
            raise ValueError('Text sync payload exceeds its 64000-byte limit.')
        if self.operation == 'delete':
            if self.payload:
                raise ValueError('Delete events contain only an empty payload.')
        else:
            model = {'session': SessionPayload, 'message': MessagePayload, 'memory': MemoryPayload}[self.entity]
            model.model_validate(self.payload)
        return self


class EventBatch(Closed):
    events: list[Event] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_event_ids(self):
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError('An event batch must not repeat an event identifier.')
        return self


class DeviceRequest(Closed):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['phone', 'desktop']
    workspace_id: Identifier | None = None

    @model_validator(mode='after')
    def worker_has_workspace(self):
        if self.kind == 'desktop' and self.workspace_id is None:
            raise ValueError('A desktop device must be bound to an explicit workspace fingerprint.')
        if self.kind == 'phone' and self.workspace_id is not None:
            raise ValueError('A phone is not a workspace executor.')
        return self


class TaskRequest(Closed):
    id: Identifier
    workspace_id: Identifier
    session_id: Identifier
    prompt: str = Field(min_length=1, max_length=12000)


class ClaimRequest(Closed):
    workspace_id: Identifier


class LeaseRequest(Closed):
    lease_token: str = Field(min_length=20, max_length=512)


class ResultRequest(LeaseRequest):
    status: Literal['succeeded', 'failed']
    result: str = Field(max_length=64000)
