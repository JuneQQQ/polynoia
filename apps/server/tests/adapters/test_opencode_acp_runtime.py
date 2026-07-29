"""Contract tests for the OpenCode ACP client runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acp.client.connection import ClientSideConnection

from polynoia.adapters import opencode as oc
from polynoia.adapters.opencode import OpenCodeSession, _OpenCodeAcpClient


class _ImmediateEof:
    async def readline(self) -> bytes:
        return b""


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = _ImmediateEof()


class _SetupConnection:
    def __init__(self) -> None:
        self.initialize_calls: list[dict[str, Any]] = []
        self.new_session_calls: list[dict[str, Any]] = []

    async def initialize(self, **kwargs: Any) -> Any:
        self.initialize_calls.append(kwargs)
        return SimpleNamespace(protocol_version=oc.PROTOCOL_VERSION)

    async def new_session(self, **kwargs: Any) -> Any:
        self.new_session_calls.append(kwargs)
        return SimpleNamespace(session_id="acp-session")


def _session(tmp_path: Any) -> OpenCodeSession:
    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    return OpenCodeSession(
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model=None,
        system_prompt=None,
        env={},
        agent_id="opencoder",
    )


@pytest.mark.asyncio
async def test_setup_advertises_only_capabilities_polynoia_implements(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    connection = _SetupConnection()
    process = _FakeProcess()
    context_closed = False

    @asynccontextmanager
    async def _spawn(*args: Any, **kwargs: Any):
        nonlocal context_closed
        try:
            yield connection, process
        finally:
            context_closed = True

    monkeypatch.setattr(oc, "spawn_agent_process", _spawn)
    monkeypatch.setattr(oc, "_opencode_executable", lambda env: "opencode")
    monkeypatch.setattr(oc, "_polynoia_opencode_data_home", lambda: str(tmp_path / "data"))

    await session._ensure_subprocess()

    initialize = connection.initialize_calls[0]
    capabilities = initialize["client_capabilities"]
    assert capabilities.fs is None
    assert capabilities.terminal is False
    assert initialize["protocol_version"] == oc.PROTOCOL_VERSION
    assert session._acp_session_id == "acp-session"

    await session.close()
    assert context_closed is True


@pytest.mark.asyncio
async def test_dead_process_is_replaced_before_next_turn(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    processes: list[_FakeProcess] = []
    closed: list[_FakeProcess] = []

    @asynccontextmanager
    async def _spawn(*args: Any, **kwargs: Any):
        process = _FakeProcess()
        processes.append(process)
        try:
            yield _SetupConnection(), process
        finally:
            closed.append(process)

    monkeypatch.setattr(oc, "spawn_agent_process", _spawn)
    monkeypatch.setattr(oc, "_opencode_executable", lambda env: "opencode")
    monkeypatch.setattr(oc, "_polynoia_opencode_data_home", lambda: str(tmp_path / "data"))

    await session._ensure_subprocess()
    processes[0].returncode = 1
    await session._ensure_subprocess()

    assert len(processes) == 2
    assert closed == [processes[0]]
    assert session._proc is processes[1]
    await session.close()


@pytest.mark.asyncio
async def test_missing_filesystem_method_returns_jsonrpc_method_not_found() -> None:
    accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.get_running_loop().create_future()
    )

    async def _accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set_result((reader, writer))

    server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client_reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
    agent_reader, agent_writer = await accepted
    connection = ClientSideConnection(
        _OpenCodeAcpClient(),
        client_writer,
        client_reader,
    )
    try:
        agent_writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "fs/read_text_file",
                        "params": {
                            "sessionId": "acp-session",
                            "path": "README.md",
                        },
                    }
                )
                + "\n"
            ).encode()
        )
        await agent_writer.drain()
        response = json.loads(await asyncio.wait_for(agent_reader.readline(), timeout=1.0))
        assert response["id"] == 7
        assert response["error"]["code"] == -32601
    finally:
        await connection.close()
        agent_writer.close()
        with contextlib.suppress(Exception):
            await agent_writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_prompt_timeout_cancels_and_discards_runtime(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    cancelled = False

    class _HangingConnection:
        async def prompt(self, **kwargs: Any) -> Any:
            await asyncio.Event().wait()

        async def cancel(self, **kwargs: Any) -> None:
            nonlocal cancelled
            cancelled = True

    session._connection = _HangingConnection()  # type: ignore[assignment]
    session._proc = _FakeProcess()  # type: ignore[assignment]
    session._process_stack = contextlib.AsyncExitStack()
    session._acp_session_id = "acp-session"

    async def _noop_ensure() -> None:
        return

    session._ensure_subprocess = _noop_ensure  # type: ignore[method-assign]
    monkeypatch.setattr(oc, "_ACP_PROMPT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(oc, "_TRAILING_FLUSH_GRACE_S", 0.0)

    events = [event async for event in session.send("task-1", "hello")]

    failed = next(event for event in events if event.type == "turn.failed")
    assert failed.error["subtype"] == "acp_timeout"
    assert cancelled is True
    assert session._proc is None
    assert session._connection is None
