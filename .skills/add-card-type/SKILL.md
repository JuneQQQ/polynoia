---
name: add-card-type
description: Standardized flow for adding a new message card type (13th payload kind). Use when adding rich UI cards beyond the existing 12 (text/diff/web/tasks/swatches/copy/metrics/sql/schema/logs/api/typing/ask-form).
---

# Skill — Add a New Card Type

> Polynoia 新加一种 MessagePart payload kind 的 5 步流程。

## 何时用

- 用户说"我想加个 X 卡"(例:profile 卡 / chart 卡 / kanban 卡)
- agent 产出有特殊结构需要专属渲染

## 不该用

- 卡内字段调整 — 直接改 Pydantic schema
- 改卡片视觉(颜色 / 字号)— 改 React 组件就行

## 步骤

### 1. Pydantic schema(后端 source-of-truth)

`apps/server/polynoia/domain/messages.py`:
```python
class XPayload(BaseModel):
    kind: Literal["x"]
    # ... 字段
```

加入 `MessagePayload` discriminated union:
```python
MessagePayload = Annotated[
    TextPayload | DiffPayload | ... | XPayload,
    Field(discriminator="kind"),
]
```

### 2. 自动生成 TS

```bash
make types
```

跑 `datamodel-code-generator` Pydantic → TS,产物在 `packages/shared/` + `apps/web/src/lib/types.ts`。

### 3. 注册表

`apps/web/src/components/parts/index.ts`:
```typescript
const PARTS_REGISTRY: Record<MessagePayload["kind"], FC<{payload: any}>> = {
  text: TextPart,
  diff: DiffPart,
  // ...
  x: XPart,                  // ← 新加
};
```

### 4. React 组件

`apps/web/src/components/parts/XPart.tsx`:
- 接 `{payload: XPayload}`,渲染
- 遵守编辑式排印规范(font-display 标题 + IBM Plex Sans 正文 + JetBrains Mono 数据)
- 用 Tailwind 4 + CSS variables
- 颜色用 `var(--color-accent)` / `var(--color-line)` 等,不写死

### 5. Demo 数据 + 测试

- `apps/web/src/lib/fixtures.ts` 加一条 demo payload
- `tests/web/components/XPart.test.tsx`(vitest renderHook 模式)
- 后端 round-trip 测试:`tests/domain/test_messages.py` 加 XPayload 反序列化测

## 关键约束

- **kind 字段必须有** — discriminated union 靠它路由
- **不要在 packages/core 内 import 任何 React 渲染层** — 跨平台干净度(P1+ React Native 复用)
- **卡内允许嵌套 MessagePart** — 如果你的卡里要塞 Diff 子卡,直接复用注册表
