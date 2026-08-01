from __future__ import annotations

import importlib.metadata
import inspect

import pytest
from a2a.server.agent_execution import RequestContextBuilder
from a2a.server.request_handlers import DefaultRequestHandlerV2, RequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.tasks import TaskStore

from polynoia_a2a_bridge.sdk_contract import (
    A2A_SDK_VERSION,
    A2A_TCK_COMMIT,
    assert_supported_sdk,
)


def test_exact_sdk_and_tck_pins() -> None:
    assert importlib.metadata.version("a2a-sdk") == "1.1.2"
    assert A2A_SDK_VERSION == "1.1.2"
    assert A2A_TCK_COMMIT == "5996b79f9cefa6fc390980e383e358a66fb9e49e"
    assert_supported_sdk()


def test_public_extension_seams_match_the_reviewed_sdk() -> None:
    assert inspect.isabstract(RequestContextBuilder)
    assert inspect.isabstract(ServerCallContextBuilder)
    assert inspect.isabstract(TaskStore)
    assert "request_context_builder" in inspect.signature(DefaultRequestHandlerV2).parameters
    assert "context_builder" in inspect.signature(create_jsonrpc_routes).parameters
    assert "card_url" in inspect.signature(create_agent_card_routes).parameters
    assert {"on_message_send", "on_message_send_stream"} <= set(dir(RequestHandler))


def test_version_guard_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.2.0")
    with pytest.raises(RuntimeError, match=r"requires a2a-sdk==1\.1\.2"):
        assert_supported_sdk()
