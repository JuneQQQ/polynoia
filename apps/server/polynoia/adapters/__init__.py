"""Polynoia Adapter Protocol (PAP) — wraps coding-agent CLIs as unified streams.

Each Adapter spawns its CLI as a subprocess and translates the CLI's wire format
into a Polynoia `AdapterEvent` stream. The Orchestrator (or single-chat handler)
consumes these events and converts them to UIMessageChunk frames for the client.
"""

from polynoia.adapters.base import (
    Adapter,
    AdapterCapabilities,
    AdapterEvent,
    AdapterMeta,
    AdapterSession,
    ExtensionEvent,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    PermissionRequestedEvent,
    PlanUpdatedEvent,
    RateLimitEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SessionUsageUpdatedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)

__all__ = [
    "Adapter",
    "AdapterCapabilities",
    "AdapterEvent",
    "AdapterMeta",
    "AdapterSession",
    "ExtensionEvent",
    "PartCompletedEvent",
    "PartDeltaEvent",
    "PartStartedEvent",
    "PermissionRequestedEvent",
    "PlanUpdatedEvent",
    "RateLimitEvent",
    "SessionEndedEvent",
    "SessionStartedEvent",
    "SessionUsageUpdatedEvent",
    "TurnCompletedEvent",
    "TurnFailedEvent",
    "TurnStartedEvent",
]
