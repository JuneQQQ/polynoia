"""Message payloads — 12 typed cards as a discriminated union.

A Message has `payload: MessagePayload`(判别 union by `kind`)+ optional `statuses`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from polynoia.domain.entities import ULID, new_ulid


# ── Inline content (for text messages) ──────────────────────────────
class MentionInline(BaseModel):
    """@-mention chip in inline text."""

    type: Literal["mention"] = "mention"
    m: str  # agent_id (ULID or short id)


class TextSegment(BaseModel):
    """Plain text segment."""

    type: Literal["text"] = "text"
    text: str


InlineContent = list[TextSegment | MentionInline]


class TextBlock(BaseModel):
    """A paragraph in TextPayload.body."""

    t: Literal["p"] = "p"
    c: str | InlineContent


# ── Status item (inline parallel sub-task checklist) ─────────────────
class StatusItem(BaseModel):
    """A line in a status checklist (attached to text messages)."""

    state: Literal["pending", "run", "done", "failed"]
    text: str


# ── 12 payload types ─────────────────────────────────────────────
class TextPayload(BaseModel):
    """Plain text message with mentions."""

    kind: Literal["text"] = "text"
    body: list[TextBlock]


class ReasoningPayload(BaseModel):
    """Model's internal thinking / reasoning (Claude thinking, Codex reasoning,
    OpenCode agent_thought). Streamed live, then folded away in the UI — shown
    again only on user click, and visually de-emphasized. Same body shape as
    text so it reuses the streaming + persistence path."""

    kind: Literal["reasoning"] = "reasoning"
    body: list[TextBlock]
    # How long the model thought, in seconds — set when the block completes so
    # the folded strip reads "思考 N 秒" even AFTER a refresh (the live client-side
    # timer is gone on reload, so this must be persisted for faithful 回显).
    seconds: int | None = None


class TaskItem(BaseModel):
    """One task in the orchestrator's task board."""

    id: ULID = Field(default_factory=new_ulid)
    state: Literal["pending", "run", "done", "failed"] = "pending"
    agent: ULID
    label: str
    note: str | None = None
    context_refs: list[ULID] = []
    retry_count: int = 0


class TasksPayload(BaseModel):
    """Orchestrator task board card."""

    kind: Literal["tasks"] = "tasks"
    title: str
    tasks: list[TaskItem]


HunkLineKind = Literal["add", "del", "ctx"]


class Hunk(BaseModel):
    """One diff hunk."""

    header: str
    lines: list[tuple[HunkLineKind, int, str]]


class DiffPayload(BaseModel):
    """Code diff card with apply/rollback."""

    kind: Literal["diff"] = "diff"
    file: str
    additions: int
    deletions: int
    reviewers: list[ULID] = []
    hunks: list[Hunk]
    applied: bool = False
    applied_at: datetime | None = None
    #: short sha of the commit this diff was recorded at — set when the card
    #: represents an edit an agent ALREADY made + committed (proactive card),
    #: vs a not-yet-applied proposal. Drives the "已改 / 撤销" UI vs "应用".
    commit_sha: str | None = None
    #: the editing agent (worker ULID) for a proactive card — folds the card
    #: into that agent's burst lane AND targets the right worktree on 撤销.
    agent_id: str | None = None


class WebPayload(BaseModel):
    """Web preview card (links to right-pane iframe)."""

    kind: Literal["web"] = "web"
    title: str
    url: str
    preview_kind: Literal["url", "static", "bundle", "fullstack"] = "url"
    deployed: bool = False
    # Optional workspace-relative path to the HTML file the card should
    # preview by default. When set, WebTab picks this file in its dropdown.
    file_path: str | None = None


class Swatch(BaseModel):
    """A color swatch."""

    hex: str
    name: str


class SwatchesPayload(BaseModel):
    """Color palette card."""

    kind: Literal["swatches"] = "swatches"
    swatches: list[Swatch]


class CtaCopy(BaseModel):
    primary: str
    secondary: str


class CopyPayload(BaseModel):
    """Copywriter output (hero variants + CTA)."""

    kind: Literal["copy"] = "copy"
    hero: list[str]
    cta: CtaCopy


class Stat(BaseModel):
    """One metric in MetricsPayload."""

    label: str
    value: str
    trend: Literal["up", "down", "flat"] = "flat"
    color: str | None = None


class MetricsPayload(BaseModel):
    """Metrics + sparkline card."""

    kind: Literal["metrics"] = "metrics"
    service: str
    stats: list[Stat]
    sparkline: list[float]


class SqlStats(BaseModel):
    rows: str
    calls: str
    avg_ms: int
    p99_ms: int


class ExplainRow(BaseModel):
    node: str
    cost: str
    rows: int
    hot: bool = False
    why: str | None = None


class SqlPayload(BaseModel):
    """SQL slow-query analysis card."""

    kind: Literal["sql"] = "sql"
    title: str
    query: str
    stats: SqlStats
    explain: list[ExplainRow]
    diagnosis: str


class SchemaField(BaseModel):
    name: str
    type: str
    null: bool = True  # 是否允许 null
    key: str | None = None  # "PK" / "FK" / None


class SchemaIndex(BaseModel):
    name: str
    cols: str
    kind: Literal["btree", "hash", "gin", "gist"] = "btree"
    existing: bool = True
    recommend: bool = False
    note: str | None = None


class SchemaPayload(BaseModel):
    """DB schema + index recommendation card."""

    kind: Literal["schema"] = "schema"
    table: str
    fields: list[SchemaField]
    indexes: list[SchemaIndex]


class LogLine(BaseModel):
    tm: str  # 时间戳串
    level: Literal["INFO", "WARN", "ERROR", "DEBUG"]
    text: str


class LogsPayload(BaseModel):
    """Service logs card (live tail)."""

    kind: Literal["logs"] = "logs"
    service: str
    lines: list[LogLine]


class TerminalPayload(BaseModel):
    """Live terminal card — streamed stdout/stderr of a `bash` tool run. Updated
    in place (same message id) as output arrives; ``running`` flips to False and
    ``exit_code`` is set when the command finishes."""

    kind: Literal["terminal"] = "terminal"
    command: str
    output: str = ""
    running: bool = True
    exit_code: int | None = None
    truncated: bool = False


class ApiParam(BaseModel):
    name: str
    in_: Literal["path", "query", "header", "body"] = Field(alias="in")
    type: str
    required: bool = False
    eg: str | None = None

    model_config = {"populate_by_name": True}


class ApiPerf(BaseModel):
    before: str
    after: str


class ApiPayload(BaseModel):
    """API endpoint spec card."""

    kind: Literal["api"] = "api"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    desc: str
    params: list[ApiParam]
    perf: ApiPerf | None = None


class TypingPayload(BaseModel):
    """Typing indicator (agent is generating)."""

    kind: Literal["typing"] = "typing"
    note: str | None = None  # e.g. "正在修改 Hero.tsx"


class ToolCallPayload(BaseModel):
    """Generic tool-call card — universal renderer for any agent tool invocation.

    Replaces TypingPayload for actual tool use (typing is now only used as
    a pure typing indicator, no tool name). The state machine:
        pending → running → completed | error

    UI: collapsed by default;header shows name + truncated arg preview + status.
    Click to expand: full input JSON + output preview + duration.
    """

    kind: Literal["tool-call"] = "tool-call"
    tool_call_id: str
    name: str  # tool name e.g. "Bash", "FileEdit", "WebFetch", "Read"
    input: dict[str, Any] = {}
    state: Literal["pending", "running", "completed", "error"] = "running"
    output: Any = None  # tool result (string or structured)
    output_text: str | None = None  # convenience for string-rendered output
    is_error: bool = False
    duration_ms: int | None = None
    summary: str | None = None  # one-line human description, e.g. "读取 src/x.py"
    # Live-streaming raw args (partial JSON) shown in the EXPANDED body while the
    # model is still generating the tool input — so a big `dispatch` shows its
    # args building inside the fold. Cleared once `input` is final.
    input_preview: str | None = None


class ErrorPayload(BaseModel):
    """A turn- or conversation-level failure, persisted as a first-class card so
    it 回显 survives a refresh.

    Errors used to be live-only WS chunks (``{"type":"error"}``) that the client
    rendered transiently and never wrote back — so on reload the turn looked like
    it had silently stopped. This payload is the persisted record.

    Distinct from a tool-call's own ``state="error"`` (one tool failed): this is
    the WHOLE turn / routing failing — upstream 401/429/500, idle-timeout, adapter
    crash, user abort, dispatch depth limit, or no routable contact.
    """

    kind: Literal["error"] = "error"
    message: str
    # The agent whose turn failed; None for conversation-level errors (routing /
    # no adapter contact), which are attributed to the "system" sender.
    agent_id: ULID | None = None
    # Why it failed — drives the icon/tone in the UI ("aborted" is neutral, the
    # rest read as a hard error).
    reason: Literal[
        "turn_failed", "exception", "timeout", "aborted", "unavailable", "depth_limit"
    ] = "exception"
    # Whether re-sending could plausibly succeed (transient upstream / timeout).
    retryable: bool = False


class AskQuestion(BaseModel):
    """One question in an ask-form."""

    id: str
    kind: Literal["single", "multi", "fill"]
    label: str
    sub: str | None = None
    optional: bool = False
    options: list[dict[str, Any]] | None = None
    default_value: Any = None
    placeholder: str | None = None


class AskFormPayload(BaseModel):
    """Blocking question form (P0 schema preserved but agents don't emit).

    P1+: Agent emits to ask user for input mid-task.
    """

    kind: Literal["ask-form"] = "ask-form"
    title: str
    blocking: bool = True
    questions: list[AskQuestion]


class FilePayload(BaseModel):
    """Generic file attachment(PDF / docx / source / etc).

    P0:src is a data URL(inline) or absolute URL. P1+ moves to a real
    upload endpoint returning server-hosted URLs.
    """

    kind: Literal["file"] = "file"
    src: str
    name: str
    media_type: str | None = None
    size_bytes: int | None = None
    caption: str | None = None


class FilesPanelItem(BaseModel):
    """One file inside a FilesPayload panel."""

    src: str
    name: str
    media_type: str | None = None
    size_bytes: int | None = None


class LinkItem(BaseModel):
    """One external link inside a FilesPayload panel — covers both web URLs
    (e.g. a preview server, container, deployed static site) and download URLs
    (e.g. a source zip). Same panel surface as files; rendering branches on
    ``kind``."""

    url: str
    label: str | None = None
    # web → clickable, opens in new tab (default).
    # download → triggers a download with the `label` as the suggested filename.
    kind: Literal["web", "download"] = "web"
    bytes: int | None = None
    # Free-form note (e.g. "临时·30 分钟后过期", "container · port 8080").
    note: str | None = None


class FilesPayload(BaseModel):
    """A panel bundling deliverables an agent presented in ONE `present` call
    (the orchestrator's hand-off). Two parallel lists — produced FILES (preview
    + download from the sandbox) and external LINKS (a deployed URL, a download
    of a built zip). Either list may be empty; at least one entry total. Rendered
    as a single card: a one-line ``message`` to the user + the entry list."""

    kind: Literal["files"] = "files"
    # One-line feedback shown above the entry list (the agent's hand-off note).
    message: str | None = None
    files: list[FilesPanelItem] = []
    links: list[LinkItem] = []


class ImagePayload(BaseModel):
    """Image attachment in a message.

    P0: inline data URLs (small images pasted from clipboard / file picker).
    P1+: switch to a server-hosted upload endpoint returning short URLs.
    """

    kind: Literal["image"] = "image"
    # Either a data: URL (P0) or an absolute http(s) URL (P1+ upload)
    src: str
    # Optional original filename + media type for accessibility / download
    name: str | None = None
    media_type: str | None = None
    # Width/height in pixels — for layout reservation. Optional.
    width: int | None = None
    height: int | None = None
    # Caption text rendered below the image
    caption: str | None = None


# ── Conflict (multi-agent same-file merge conflict, PR#4 closed-loop) ──
ConflictType = Literal["content", "add_add", "modify_delete", "rename", "binary"]


class ConflictFile(BaseModel):
    """One conflicted file inside a ConflictPayload.

    Which blobs are present depends on ``ctype``: ``content`` has text markers
    + 3-way stage blobs; ``add_add`` has no base; ``modify_delete`` has a
    missing side and NO markers; ``binary`` is take-side only (never decoded).
    """

    path: str
    ctype: ConflictType = "content"
    # Conflicted working-tree content with <<<<<<< ||||||| ======= >>>>>>>
    # markers — only for text ``content`` conflicts.
    markers: str | None = None
    ours: str | None = None       # git stage :2: (main side); None if missing
    theirs: str | None = None     # git stage :3: (branch side); None if missing
    base: str | None = None       # git stage :1: (merge base); None for add_add
    is_binary: bool = False
    # Final resolved content (text ``content`` conflicts), filled on resolve.
    resolution: str | None = None
    # For non-content conflicts the resolution is a side choice, not text.
    side: Literal["ours", "theirs", "delete"] | None = None
    state: Literal["conflict", "resolved"] = "conflict"


class ConflictPayload(BaseModel):
    """Merge-conflict card: a branch that failed to auto-merge into main.

    Four-state machine: open → resolving → resolved | abandoned. Re-emitted
    with the same message id to flip state in place. Resolution happens via the
    manual ConflictResolvePane or an LLM repair turn; both re-merge for real.
    """

    kind: Literal["conflict"] = "conflict"
    conflict_id: ULID
    conv_id: ULID
    branch: str                   # agent/<id>/conv-<id> that failed to merge
    agent_id: str                 # branch author (branch.split('/')[1])
    base_agents: list[str] = Field(default_factory=list)
    into: str = "main"
    status: Literal["open", "resolving", "resolved", "abandoned"] = "open"
    files: list[ConflictFile] = []
    resolved_by: str | None = None       # agent_id or "you"
    resolved_sha: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: datetime | None = None


# ── Discriminated union ──────────────────────────────────────────
MessagePayload = Annotated[
    Union[
        TextPayload,
        ReasoningPayload,
        TasksPayload,
        DiffPayload,
        WebPayload,
        SwatchesPayload,
        CopyPayload,
        MetricsPayload,
        SqlPayload,
        SchemaPayload,
        LogsPayload,
        TerminalPayload,
        ApiPayload,
        TypingPayload,
        ToolCallPayload,
        AskFormPayload,
        ImagePayload,
        FilePayload,
        FilesPayload,
        ErrorPayload,
        ConflictPayload,
    ],
    Field(discriminator="kind"),
]


# ── Envelope ────────────────────────────────────────────────────
class Message(BaseModel):
    """A message in a conversation."""

    id: ULID = Field(default_factory=new_ulid)
    conv_id: ULID
    sender_id: ULID  # Agent.id 或 "you"
    payload: MessagePayload
    statuses: list[StatusItem] | None = None
    in_reply_to: ULID | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    edited_at: datetime | None = None
