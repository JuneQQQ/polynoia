from __future__ import annotations

from types import SimpleNamespace

from polynoia.api.routes import (
    _single_direct_mention_target,
    _with_orchestrator_mention_routing_hint,
)


def _agents():
    return {
        "orch": SimpleNamespace(name="阿核"),
        "writer": SimpleNamespace(name="文澜"),
        "chart": SimpleNamespace(name="制图"),
        "data": SimpleNamespace(name="数擎"),
    }


def _agent_ok(aid: str) -> bool:
    return aid in {"writer", "chart", "data"}


def test_multi_mentions_route_through_orchestrator_hint() -> None:
    text = "请 @文澜 先写 alpha, @制图 随后读取 alpha 再写 beta"

    routed = _with_orchestrator_mention_routing_hint(
        text,
        mentioned_ids=["writer", "chart"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_by_id=_agents(),
    )

    assert routed.startswith(text)
    assert "用户点名了多位群成员:@文澜、@制图" in routed
    assert "不是平台已经把消息直接转交给这些成员" in routed
    assert "必须分阶段 `dispatch`" in routed
    assert "合并到 main 后" in routed


def test_single_member_mention_does_not_add_orchestrator_hint() -> None:
    text = "@文澜 看一下这个文件"

    routed = _with_orchestrator_mention_routing_hint(
        text,
        mentioned_ids=["writer"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_by_id=_agents(),
    )

    assert routed == text


def test_orchestrator_or_non_member_mentions_do_not_add_hint() -> None:
    text = "@阿核 帮我拆一下,顺便参考 @数擎"

    routed = _with_orchestrator_mention_routing_hint(
        text,
        mentioned_ids=["orch", "data"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_by_id=_agents(),
    )

    assert routed == text


def test_single_real_member_mention_routes_directly() -> None:
    assert _single_direct_mention_target(
        ["chart"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_ok=_agent_ok,
    ) == "chart"


def test_multi_mentions_do_not_route_directly() -> None:
    assert _single_direct_mention_target(
        ["writer", "chart"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_ok=_agent_ok,
    ) is None


def test_orchestrator_plus_member_mention_stays_with_orchestrator() -> None:
    assert _single_direct_mention_target(
        ["orch", "chart"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_ok=_agent_ok,
    ) is None


def test_unknown_mentions_are_ignored_for_direct_route() -> None:
    assert _single_direct_mention_target(
        ["missing", "writer"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_ok=_agent_ok,
    ) == "writer"
