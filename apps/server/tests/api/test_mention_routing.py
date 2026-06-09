from __future__ import annotations

from types import SimpleNamespace

from polynoia.api.routes import _with_orchestrator_mention_routing_hint


def _agents():
    return {
        "orch": SimpleNamespace(name="阿核"),
        "writer": SimpleNamespace(name="文澜"),
        "chart": SimpleNamespace(name="制图"),
        "data": SimpleNamespace(name="数擎"),
    }


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


def test_single_member_mention_still_routes_through_orchestrator() -> None:
    text = "@文澜 看一下这个文件"

    routed = _with_orchestrator_mention_routing_hint(
        text,
        mentioned_ids=["writer"],
        member_ids={"you", "orch", "writer", "chart"},
        orch_id="orch",
        agent_by_id=_agents(),
    )

    assert "用户点名了一位群成员:@文澜" in routed
    assert "唯一协调入口" in routed
    assert "只能通过 `dispatch`" in routed


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
