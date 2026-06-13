"""项目流水线 — gstack 的冲刺方法论装进群聊(SOP 驱动的阶段化协作)。

gstack(garrytan/gstack)是分层评审的冲刺法:每阶段有 owner 和准出物。
注意它本身也是 **prompt/slash-command 驱动的协作约定**,不是硬状态机——
agent 被指示遵循阶段,而非被代码强制。本模块同理:

  POST /api/pipelines/spawn:
    1. 按模板的角色槽位 复用/雇佣 联系人(去重:一个联系人不重复占两槽;
       缺口从 agency-agents 角色库现雇;雇佣中途失败会回滚已雇,不留孤儿)
    2. 建独立工作区 + 群聊(指定 orchestrator)
    3. 把阶段 SOP 写进会话**草稿**(用户审阅+补需求后回车才开跑)

⚠️ 边界(诚实声明):阶段顺序是写给 orchestrator 的 **SOP 指令(协作式)**,
**平台目前不硬性拦截** —— orchestrator 理论上可一次性 dispatch 全部阶段。
Polynoia 的承重件覆盖了每个动词(burst=Build、discuss=Review、
pending-edit=人类批准、merge lock=Ship、ConvMemory=Reflect),但「拿不到
准出物就阻止 dispatch 下一阶段」的硬门禁(stage state machine + dispatch
gate)尚未实现,是本功能最大的待办。每阶段返工/通过将来计入质量画像。
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from polynoia.api import role_presets

log = logging.getLogger(__name__)
router = APIRouter()

# slot: 在既有联系人里按关键词复用;不命中则从角色库按 preset_query 现雇。
TEMPLATES: dict[str, dict[str, Any]] = {
    "gstack_sprint": {
        "name": "gstack 冲刺",
        "description": "Think→Plan→Build→Review→Test→Ship→Reflect 七阶段,每阶段有 owner 和准出物",
        "stages": ["Think 需求澄清", "Plan 技术方案", "Build 并行实施", "Review 交叉评审", "Test 验收测试", "Ship 合入交付", "Reflect 复盘沉淀"],
        "slots": [
            {"label": "产品经理", "match": ["产品", "product"], "preset_query": "Product Manager", "orchestrator": True},
            {"label": "架构师", "match": ["架构", "backend", "后端"], "preset_query": "Backend Architect"},
            {"label": "前端工程师", "match": ["前端", "frontend"], "preset_query": "Frontend Developer"},
            {"label": "QA", "match": ["qa", "测试", "test"], "preset_query": "API Tester"},
        ],
    },
    "quick_delivery": {
        "name": "快速交付",
        "description": "工程师直出 + QA 把关的两阶段轻流程",
        "stages": ["Build 实施", "Test 验收"],
        "slots": [
            {"label": "全栈工程师", "match": ["全栈", "工程", "fullstack", "engineer"], "preset_query": "Senior Developer", "orchestrator": True},
            {"label": "QA", "match": ["qa", "测试", "test"], "preset_query": "API Tester"},
        ],
    },
}


def _sop_brief(tpl: dict[str, Any], roles: list[dict[str, Any]]) -> str:
    stages = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tpl["stages"]))
    members = "、".join(f"@{r['name']}({r['slot']})" for r in roles)
    return (
        f"按「{tpl['name']}」流水线推进本项目。成员:{members}。\n\n"
        f"阶段(逐阶段推进,每阶段产出准出物并在群里确认后才进入下一阶段):\n{stages}\n\n"
        "规则:\n"
        "- 每个阶段指定一名 owner;owner 产出准出物(文档/代码/测试报告)\n"
        "- Review 阶段必须由非作者成员交叉评审,提出的问题要闭环\n"
        "- Ship 前 QA 准出;Reflect 阶段把关键决策与教训写入共享记忆\n\n"
        "先从阶段 1 开始:向我澄清需求,产出一页需求纪要。我的需求是:"
        "【在这里填写你的项目需求,然后发送】"
    )


@router.get("/api/pipelines")
async def list_templates():
    return {
        "templates": [
            {"key": k, "name": t["name"], "description": t["description"],
             "stages": t["stages"], "slots": [s["label"] for s in t["slots"]]}
            for k, t in TEMPLATES.items()
        ]
    }


async def _resolve_slot(
    slot: dict[str, Any], agents: list[dict], adapter: str, model: str, taken: set[str]
) -> dict[str, Any]:
    """Reuse an existing (not-yet-assigned) contact whose name/tagline matches,
    else hire from the role-preset catalog. ``taken`` holds ids already assigned
    to earlier slots so one contact never fills two roles (loose substring
    matching could otherwise map 阿核 to both 架构师 and 前端工程师)."""
    for a in agents:
        if a.get("id") in ("you", "system") or a.get("human") or a.get("id") in taken:
            continue
        hay = f"{a.get('name', '')} {a.get('tagline', '')}".lower()
        if any(m.lower() in hay for m in slot["match"]):
            taken.add(a["id"])
            return {"id": a["id"], "name": a["name"], "slot": slot["label"], "hired": False}
    # hire from catalog
    presets = (await role_presets.list_presets(q=slot["preset_query"]))["presets"]
    # 优先 name 精确命中(目录搜索是 name+description 模糊,排序不可信)
    presets.sort(
        key=lambda pr: (
            0 if pr["name"].lower() == slot["preset_query"].lower() else
            1 if pr["name"].lower().startswith(slot["preset_query"].lower()) else 2
        )
    )
    if not presets:
        raise HTTPException(
            409,
            f"槽位「{slot['label']}」无既有联系人可复用,且角色库无匹配预设"
            "(先在角色库同步目录,或手动创建联系人)",
        )
    hired = await role_presets.hire_preset(
        presets[0]["id"], {"adapter_id": adapter, "model": model, "name": slot["label"]}
    )
    c = hired["contact"]
    return {"id": c["id"], "name": c["name"], "slot": slot["label"], "hired": True}


async def _rollback_hired(roles: list[dict[str, Any]]) -> None:
    """Best-effort delete of contacts hired during a failed spawn (no orphans)."""
    from polynoia.api.contacts_routes import delete_contact

    for r in roles:
        if r.get("hired"):
            with contextlib.suppress(Exception):
                await delete_contact(r["id"])


@router.post("/api/pipelines/spawn")
async def spawn_pipeline(body: dict):
    tpl_key = (body.get("template") or "").strip()
    tpl = TEMPLATES.get(tpl_key)
    if tpl is None:
        raise HTTPException(404, f"unknown template: {tpl_key}")
    adapter = (body.get("adapter_id") or "claudeCode").strip()
    model = (body.get("model") or "claude-sonnet-4-6").strip()
    title = (body.get("name") or tpl["name"]).strip()[:60]

    from polynoia.api.contacts_routes import list_agents  # reuse the live list

    agents = await list_agents()
    roles: list[dict[str, Any]] = []
    orch_id: str | None = None
    taken: set[str] = set()
    # If a slot fails to resolve mid-way, roll back any contacts WE hired this
    # call (don't orphan them) before surfacing the error.
    try:
        for slot in tpl["slots"]:
            r = await _resolve_slot(slot, agents, adapter, model, taken)
            roles.append(r)
            if slot.get("orchestrator"):
                orch_id = r["id"]
    except Exception:
        await _rollback_hired(roles)
        raise
    if orch_id is None and roles:
        orch_id = roles[0]["id"]  # group needs an orchestrator; default to first

    from polynoia.api.workspaces_routes import create_workspace

    ws = (await create_workspace({"name": title, "members": [r["id"] for r in roles]}))["workspace"]

    from polynoia.api.routes import create_conversation_endpoint

    conv_resp = await create_conversation_endpoint(
        {
            "workspace_id": ws["id"],
            "title": title,
            "members": ["you", *[r["id"] for r in roles]],
            "direct": False,
            "orchestrator_member_id": orch_id,
            "draft_text": _sop_brief(tpl, roles),
        }
    )
    conv = conv_resp.get("conversation") or conv_resp
    return {"conversation": conv, "workspace": ws, "roles": roles}
