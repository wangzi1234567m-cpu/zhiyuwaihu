"""Team boundaries. No HTTP between these ports; injected at application creation."""
import asyncio
from collections.abc import AsyncIterator
from typing import Protocol
from .contracts import (ReviewCase, KnowledgeEntry, AgentDecision, ActionCommand,
                        OperationResult, CallRecord, SessionContext, TurnInput, MessageView, ModelRequest)

class ModelPort(Protocol):
    def stream(self, request: ModelRequest, cancel: asyncio.Event) -> AsyncIterator[str]: ...

class AgentPort(Protocol):
    async def handle_turn(self, input: TurnInput, context: SessionContext,
                          history: tuple[MessageView, ...], case: ReviewCase,
                          knowledge: tuple[KnowledgeEntry, ...], verified: bool,
                          cancel: asyncio.Event) -> AgentDecision: ...

class CasePort(Protocol):
    def get_case(self, owner: str, application_id: str) -> ReviewCase: ...

class KnowledgePort(Protocol):
    def lookup(self, scenario_id: str, query: str, scope: list[str], knowledge_version: str) -> list[KnowledgeEntry]: ...

class BusinessPort(Protocol):
    async def execute(self, command: ActionCommand) -> OperationResult: ...
    async def query_operation(self, command: ActionCommand) -> OperationResult: ...
    async def writeback(self, record: CallRecord) -> None: ...

class VoiceGateway(Protocol):
    async def credentials(self, session_id: str) -> dict: ...
