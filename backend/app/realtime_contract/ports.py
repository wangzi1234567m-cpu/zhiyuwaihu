"""Five internal interfaces; implementations must enforce the PR0 semantics."""
from collections.abc import AsyncIterator
from typing import Protocol

from ..contracts import AcceptedTurn, Interrupted
from .dto import (BusinessSnapshot, BusinessTurnInput, ContractError, EventSubscription,
                  GenerationInterrupt, PlaybackReport, PlaybackReportResult,
                  RealtimeBusinessEvent, TrustedSessionContext, TrustedTurnContext)


class ContractFailure(Exception):
    """Acceptance/subscription error; never expose raw provider or credential details."""
    def __init__(self, error: ContractError):
        super().__init__(error.code)
        self.error = error


class RealtimeBusinessPort(Protocol):
    async def submit_turn(self, context: TrustedTurnContext,
                          user_input: BusinessTurnInput) -> AcceptedTurn:
        """Return after durable acceptance, before complete business processing."""
        ...

    def subscribe_events(self, context: TrustedSessionContext,
                         subscription: EventSubscription) -> AsyncIterator[RealtimeBusinessEvent]:
        """Replay seq > after_seq then tail without a replay/live handoff gap."""
        ...

    async def interrupt_generation(self, context: TrustedSessionContext,
                                   request: GenerationInterrupt) -> Interrupted:
        """Invalidate a generation idempotently; dispatched operations survive."""
        ...

    async def get_snapshot(self, context: TrustedSessionContext) -> BusinessSnapshot:
        """Return state and event cursor from one consistent committed view."""
        ...

    async def report_playback(self, context: TrustedSessionContext,
                              report: PlaybackReport) -> PlaybackReportResult:
        """Resolve server-owned proof references; browser observations never authorize."""
        ...
