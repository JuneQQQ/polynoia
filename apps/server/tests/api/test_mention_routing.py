from __future__ import annotations

from types import SimpleNamespace

from polynoia.api.routes import (
    _effective_mention_routing_text,
    _is_bare_ack_bounce,
    _recover_leaked_dispatch,
    _recover_raw_tool_protocol,
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


def test_start_trigger_routes_by_previous_at_task() -> None:
    previous = [
        "普通历史消息",
        "这是单 @ 路由回归测试。请 @制图 直接做 sections/qa-single-at.html。",
    ]

    assert _effective_mention_routing_text(
        "开工",
        previous_user_texts=previous,
    ) == previous[-1]


def test_non_trigger_routes_by_current_text() -> None:
    assert _effective_mention_routing_text(
        "开工以后再说 @制图",
        previous_user_texts=["请 @文澜 写 alpha"],
    ) == "开工以后再说 @制图"


# --- bare-ack-bounce suppression -------------------------------------------
# A no-work turn (think + text, zero tool/diff) that @mentions back the agent
# who just pinged it is a content-free acknowledgement; spawning the pinger
# again only ping-pongs pleasantries until the depth cap. See `_is_bare_ack_bounce`.


def test_bare_ack_bounce_back_to_pinger_is_suppressed() -> None:
    # 文澜 was pinged by 阿核, did no work, replies "@阿核 收到感谢" → drop.
    assert _is_bare_ack_bounce(
        target="orch", parent_agent_id="orch", turn_did_work=False
    ) is True


def test_real_handoff_after_work_is_not_suppressed() -> None:
    # 数擎 delivered a diff, then @阿核 "done, please review" → must spawn.
    assert _is_bare_ack_bounce(
        target="orch", parent_agent_id="orch", turn_did_work=True
    ) is False


def test_fresh_mention_to_non_pinger_is_not_suppressed() -> None:
    # No work, but @mentions someone who did NOT just ping us → real ask, spawn.
    assert _is_bare_ack_bounce(
        target="writer", parent_agent_id="orch", turn_did_work=False
    ) is False


def test_root_turn_with_no_pinger_is_not_suppressed() -> None:
    # A user-initiated turn has no parent agent → nothing to bounce back to.
    assert _is_bare_ack_bounce(
        target="orch", parent_agent_id=None, turn_did_work=False
    ) is False


# --- leaked dispatch recovery (model emits dispatch as text tool-call markup) ---


def test_recover_leaked_dispatch_parses_and_strips() -> None:
    text = (
        "我先派文澜把规格定下来。\n\n"
        '<parameter name="tasks">[{"agent":"文澜","note":"写 spec","files":["docs/spec.md"]}]</parameter> '
        '<parameter name="contract">项目契约:端口 8000</parameter> '
        '<parameter name="need_continue">true</parameter> </invoke>'
    )
    cleaned, dispatch = _recover_leaked_dispatch(text)
    # markup fully stripped → only the human-readable line remains
    assert "<parameter" not in cleaned
    assert "</invoke>" not in cleaned
    assert cleaned == "我先派文澜把规格定下来。"
    # dispatch recovered with parsed tasks + flags
    assert dispatch is not None
    assert dispatch["tasks"] == [
        {"agent": "文澜", "note": "写 spec", "files": ["docs/spec.md"]}
    ]
    assert dispatch["need_continue"] is True
    assert dispatch["contract"] == "项目契约:端口 8000"


def test_recover_leaked_dispatch_strips_markup_without_valid_tasks() -> None:
    text = '收到。<parameter name="foo">bar</parameter></invoke>'
    cleaned, dispatch = _recover_leaked_dispatch(text)
    assert cleaned == "收到。"
    assert dispatch is None


def test_recover_leaked_dispatch_noop_on_plain_text() -> None:
    text = "我先看一眼当前文件，再实现。"
    cleaned, dispatch = _recover_leaked_dispatch(text)
    assert cleaned == text
    assert dispatch is None


# --- raw MCP protocol recovery (adapter leaks tool_response as text) ----------


def test_recover_raw_tool_response_synthesizes_tool_card() -> None:
    text = (
        "components.json 落盘。\n"
        '<tool_response> {"type":"write","path":"backend/data/components.json",'
        '"diff":"+ data","status":"ok"} </tool_response>\n'
        "接下来写事故数据。"
    )
    cleaned, parts = _recover_raw_tool_protocol(text)

    assert "<tool_response>" not in cleaned
    assert "backend/data/components.json" not in cleaned
    assert cleaned == "components.json 落盘。\n\n接下来写事故数据。"
    assert len(parts) == 1
    assert parts[0]["kind"] == "tool-call"
    assert parts[0]["name"] == "write"
    assert parts[0]["state"] == "completed"
    assert parts[0]["input"] == {"path": "backend/data/components.json"}
    assert parts[0]["summary"] == "backend/data/components.json"


def test_recover_incomplete_raw_tool_protocol_hides_from_marker() -> None:
    text = '准备写\n<tool_response> {"type":"write","path":"a.md"'
    cleaned, parts = _recover_raw_tool_protocol(text)

    assert parts == []
    assert cleaned == "准备写\n\n> 工具调用协议内容已隐藏。"
