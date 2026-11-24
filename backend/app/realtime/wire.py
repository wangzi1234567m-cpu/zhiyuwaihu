"""Browser transport DTOs; internal trusted contexts never cross this boundary."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from ..contracts import DTO


class IceServer(DTO):
    urls: list[str] = Field(min_length=1, max_length=4)
    username: str | None = None
    credential: str | None = None


class RealtimeOffer(DTO):
    type: Literal['offer'] = 'offer'
    sdp: str = Field(min_length=1, max_length=65536)
    expected_state_revision: int = Field(ge=0)


class RealtimeAnswer(DTO):
    type: Literal['answer'] = 'answer'
    sdp: str
    connection_binding: UUID
    expires_at: AwareDatetime


class RealtimeIce(DTO):
    ice_servers: list[IceServer]
    expires_at: AwareDatetime


class RealtimeControl(DTO):
    type: Literal['ready', 'heartbeat', 'interrupt', 'playback_ready', 'observation', 'end']
    request_id: UUID
    playback_instance_id: UUID | None = None
    name: Literal['first_answer_audio', 'interrupt_stopped', 'speech_ended'] | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=180000, allow_inf_nan=False)

    @model_validator(mode='after')
    def required_fields(self):
        if self.type == 'playback_ready' and self.playback_instance_id is None:
            raise ValueError('playback_ready requires playback_instance_id')
        if self.type == 'observation' and (self.name is None or self.duration_ms is None):
            raise ValueError('observation requires name and duration_ms')
        return self
