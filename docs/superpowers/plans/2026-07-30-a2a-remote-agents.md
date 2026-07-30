# A2A Remote Agent Discovery and Invocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local Polynoia instance safely discover an A2A v1 Agent Card, install it as a trusted contact, and invoke that remote agent through the existing PAP/orchestration runtime.

**Architecture:** A guarded discovery service owns URL policy, Agent Card validation, transport negotiation, hashing, and signature status. Installed cards remain normal `Agent` rows with `setup.adapter_id="a2a"`; an `A2AAdapter` translates official A2A SDK events into PAP so the existing WebSocket, persistence, cancellation, and burst lifecycle remain unchanged. A shared adapter registry removes routing allowlists, while remote-specific prompt policy prevents local MCP/worktree claims.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy async, httpx, official `a2a-sdk>=1.0,<2`, pytest/pytest-asyncio, React 18, TypeScript 5, Vitest.

## Global Constraints

- This release is an A2A client only; it does not expose Polynoia as an A2A server.
- Support A2A protocol major version 1 with HTTP+JSON and JSON-RPC bindings; reject gRPC-only cards.
- Resolve base URLs through `/.well-known/agent-card.json`; also accept explicit card URLs.
- Installed contacts are the trusted catalog and must be explicitly added to a conversation before dispatch.
- Production targets require HTTPS; loopback development targets may use HTTP.
- Reject link-local, multicast, unspecified, reserved, and metadata addresses; reject private networks unless `POLYNOIA_A2A_ALLOW_PRIVATE_NETWORKS=true`.
- Never persist bearer-token values; persist only an environment-variable name and resolve its value at invocation time.
- Do not mount, download into, edit, or merge a local workspace for an A2A contact.
- A card hash mismatch between preview and install returns HTTP 409 with category `card_changed`.
- A duplicate canonical card URL returns the existing installed contact.
- Remote content is untrusted data and must be length-limited and delimited in model-visible context.
- `POLYNOIA_A2A_ENABLED` defaults to true and disables both management endpoints and runtime invocation when false.
- No automatic replay is allowed after a remote task id or response has been observed.
- All implementation follows red-green-refactor TDD, and every task ends in a focused commit.

## File Map

### Backend

- `apps/server/polynoia/a2a/models.py`: internal discovery result, preview, change summary, and stable A2A error types.
- `apps/server/polynoia/a2a/security.py`: locator normalization, DNS/IP policy, redirect validation, bounded HTTP reads, and guarded JWKS retrieval.
- `apps/server/polynoia/a2a/discovery.py`: official Agent Card parsing, version/auth/binding negotiation, canonical hashing, signature verification, and direct URL discovery provider.
- `apps/server/polynoia/a2a/__init__.py`: narrow exports used by API and adapter layers.
- `apps/server/polynoia/adapters/registry.py`: one source of truth for adapter factories and local/remote runtime capabilities.
- `apps/server/polynoia/adapters/a2a.py`: A2A client/session and A2A-to-PAP event translation.
- `apps/server/polynoia/api/a2a_routes.py`: discover, install, and refresh management endpoints.
- `apps/server/polynoia/domain/entities.py`: persisted nested `A2AAgentSetup`.
- `apps/server/polynoia/settings.py`: feature flag and bounded network/task settings.
- `apps/server/polynoia/storage/bootstrap.py`: idempotent A2A provider upsert for fresh and existing databases.
- `apps/server/polynoia/api/seed.py`: A2A provider definition.
- `apps/server/polynoia/main.py`: A2A router registration.
- `apps/server/polynoia/adapters/base.py`: per-contact `adapter_config` session argument.
- `apps/server/polynoia/adapters/{claude_code,codex,opencode}.py`: accept and ignore `adapter_config`.
- `apps/server/polynoia/adapters/pool.py`: registry lookup, setup serialization, and no local workspace banner for remote sessions.
- `apps/server/polynoia/context/remote.py`: sanitized capability claims and adapter-aware worker delivery instructions.
- `apps/server/polynoia/context/assembler.py`: remote-safe group context and roster capability claims.
- `apps/server/polynoia/api/ws_conv.py`: registry-based eligibility and remote-safe burst prompt.
- `apps/server/polynoia/api/routes.py`: registry-based adapter checks used outside WebSocket routing.
- `apps/server/pyproject.toml` and `uv.lock`: bounded official SDK dependency.

### Frontend

- `apps/web/src/lib/types.ts`: A2A setup, discovery preview, and refresh response types.
- `apps/web/src/lib/api.ts`: discover/install/refresh client methods.
- `apps/web/src/components/A2AImportPanel.tsx`: focused discovery preview and install form.
- `apps/web/src/components/NewContactModal.tsx`: local/Remote A2A mode switch.
- `apps/web/src/components/drawer/AgentDetailView.tsx`: remote connection metadata and refresh action.
- `apps/web/src/components/Sidebar.tsx` and `apps/web/src/components/views/ContactsView.tsx`: `a2a` adapter label.
- `apps/web/src/lib/i18n.ts`: all new English/Chinese copy.

### Tests and operator docs

- `apps/server/tests/a2a/test_security.py`: URL, DNS, redirect, size, and peer-address policy.
- `apps/server/tests/a2a/test_discovery.py`: card parsing, negotiation, hashing, signatures, auth, and change detection.
- `apps/server/tests/api/test_a2a_routes.py`: endpoint persistence and atomicity.
- `apps/server/tests/adapters/test_a2a_adapter.py`: PAP mapping, context continuity, failure, and cancel.
- `apps/server/tests/adapters/test_adapter_registry.py`: registry availability and feature flag.
- `apps/server/tests/context/test_remote_a2a_context.py`: untrusted capability framing and remote worker handoff.
- `apps/server/tests/api/test_a2a_routing.py`: direct/group eligibility and no local workspace/Git assumptions.
- `apps/server/tests/integration/test_a2a_loopback.py`: deterministic in-process end-to-end path.
- `apps/server/tests/integration/test_a2a_official_sample.py`: opt-in official sample smoke test.
- `apps/web/src/lib/a2aApi.test.ts`: frontend request/response contract.
- `apps/web/src/components/A2AImportPanel.test.tsx`: preview and warning rendering.
- `apps/web/src/components/drawer/AgentDetailView.a2a.test.tsx`: installed-card metadata rendering.
- `docs/a2a-remote-agents.md`: operator discovery sources, local testing, security settings, and official tools.

---

### Task 1: Persisted A2A setup, feature settings, provider, and adapter registry

**Files:**

- Modify: `apps/server/pyproject.toml`
- Modify: `apps/server/polynoia/domain/entities.py`
- Modify: `apps/server/polynoia/settings.py`
- Modify: `apps/server/polynoia/api/seed.py`
- Modify: `apps/server/polynoia/storage/bootstrap.py`
- Create: `apps/server/polynoia/adapters/registry.py`
- Modify: `apps/server/polynoia/adapters/pool.py`
- Test: `apps/server/tests/adapters/test_adapter_registry.py`
- Test: `apps/server/tests/storage/test_a2a_provider_bootstrap.py`

**Interfaces:**

- Produces: `A2AAgentSetup`, `AgentSetup.a2a`, `AdapterRegistration`, `get_adapter_registration(adapter_id)`, `iter_enabled_adapter_ids()`, `adapter_is_remote(adapter_id)`.
- Consumes: existing `Adapter`, `Provider`, `upsert_provider`, and settings singleton.

- [ ] **Step 1: Write failing domain and registry tests**

```python
from polynoia.adapters.registry import adapter_is_remote, iter_enabled_adapter_ids
from polynoia.domain.entities import A2AAgentSetup, AgentSetup


def test_a2a_setup_round_trips_inside_agent_setup() -> None:
    remote = A2AAgentSetup(
        card_url="https://agent.example/.well-known/agent-card.json",
        endpoint_url="https://agent.example/a2a",
        protocol_binding="JSONRPC",
        protocol_version="1.0",
        card={"name": "Reviewer"},
        card_hash="sha256:abc",
        signature_status="unsigned",
    )
    setup = AgentSetup(adapter_id="a2a", a2a=remote)
    assert setup.model_dump(mode="json")["a2a"]["endpoint_url"].endswith("/a2a")


def test_registry_includes_remote_adapter_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", True)
    assert "a2a" in iter_enabled_adapter_ids()
    assert adapter_is_remote("a2a") is True
    assert adapter_is_remote("codex") is False


def test_registry_hides_remote_adapter_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.a2a_enabled", False)
    assert "a2a" not in iter_enabled_adapter_ids()
```

Add a bootstrap test that seeds one legacy provider, runs `bootstrap_db()`, and asserts provider id `a2a` exists. This specifically guards the current early return when any provider is already present.

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/adapters/test_adapter_registry.py tests/storage/test_a2a_provider_bootstrap.py -q
```

Expected: collection/import failure because `A2AAgentSetup` and `adapters.registry` do not exist.

- [ ] **Step 3: Add the official SDK and configuration models**

Add this dependency:

```toml
"a2a-sdk>=1.0,<2",
```

Add these settings:

```python
a2a_enabled: bool = True
a2a_allow_private_networks: bool = False
a2a_connect_timeout_s: float = 5.0
a2a_read_timeout_s: float = 30.0
a2a_stream_idle_timeout_s: float = 45.0
a2a_task_timeout_s: float = 600.0
a2a_card_max_bytes: int = 262_144
a2a_response_max_bytes: int = 8_388_608
a2a_max_redirects: int = 3
```

Add the nested model with JSON-safe timestamps:

```python
class A2AAgentSetup(BaseModel):
    card_url: str
    endpoint_url: str
    protocol_binding: Literal["JSONRPC", "HTTP+JSON"]
    protocol_version: str
    card: dict[str, Any]
    card_hash: str
    etag: str | None = None
    last_checked_at: datetime = Field(default_factory=datetime.utcnow)
    signature_status: Literal["signed_valid", "unsigned"]
    bearer_env_var: str | None = None


class AgentSetup(BaseModel):
    # retain every existing field
    a2a: A2AAgentSetup | None = None
```

Run `uv lock` from the repository root after editing `pyproject.toml`.

- [ ] **Step 4: Implement the adapter registry**

Use lazy factories so importing registry code does not eagerly construct SDK or CLI adapters:

```python
@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    factory: Callable[[], Adapter]
    remote: bool
    local_workspace: bool


def _registrations() -> dict[str, AdapterRegistration]:
    rows = {
        "claudeCode": AdapterRegistration("claudeCode", ClaudeCodeAdapter, False, True),
        "opencoder": AdapterRegistration("opencoder", OpenCodeAdapter, False, True),
        "codex": AdapterRegistration("codex", CodexAdapter, False, True),
    }
    if settings.a2a_enabled:
        from polynoia.adapters.a2a import A2AAdapter
        rows["a2a"] = AdapterRegistration("a2a", A2AAdapter, True, False)
    return rows


def get_adapter_registration(adapter_id: str) -> AdapterRegistration | None:
    return _registrations().get(adapter_id)


def iter_enabled_adapter_ids() -> frozenset[str]:
    return frozenset(_registrations())


def adapter_is_remote(adapter_id: str) -> bool:
    item = get_adapter_registration(adapter_id)
    return bool(item and item.remote)
```

Change `AdapterPool._ensure_base_adapters()` to instantiate registrations on demand rather than owning a second hard-coded map.

- [ ] **Step 5: Seed the A2A provider idempotently**

Add:

```python
def a2a_provider() -> Provider:
    return Provider(
        id="a2a",
        name="A2A Remote",
        vendor="A2A",
        version="1.0",
        online=True,
        color="#6D5BD0",
        bg="#ECE8FF",
    )
```

Include it in `seed_providers()`. In `bootstrap_db()`, call `upsert_provider(session, a2a_provider())` before the existing-provider early return whenever `settings.a2a_enabled` is true, then commit. This makes existing installations upgrade without a schema migration.

- [ ] **Step 6: Run tests and quality checks**

Run:

```bash
cd apps/server
uv run pytest tests/adapters/test_adapter_registry.py tests/storage/test_a2a_provider_bootstrap.py -q
uv run ruff check polynoia/adapters/registry.py polynoia/domain/entities.py polynoia/settings.py polynoia/storage/bootstrap.py
```

Expected: all tests pass and Ruff emits no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/server/pyproject.toml uv.lock apps/server/polynoia/domain/entities.py apps/server/polynoia/settings.py apps/server/polynoia/api/seed.py apps/server/polynoia/storage/bootstrap.py apps/server/polynoia/adapters/registry.py apps/server/polynoia/adapters/pool.py apps/server/tests/adapters/test_adapter_registry.py apps/server/tests/storage/test_a2a_provider_bootstrap.py
git commit -m "feat: register A2A remote contacts"
```

### Task 2: Guarded Agent Card discovery

**Files:**

- Create: `apps/server/polynoia/a2a/models.py`
- Create: `apps/server/polynoia/a2a/security.py`
- Create: `apps/server/polynoia/a2a/discovery.py`
- Create: `apps/server/polynoia/a2a/__init__.py`
- Test: `apps/server/tests/a2a/__init__.py`
- Test: `apps/server/tests/a2a/test_security.py`
- Test: `apps/server/tests/a2a/test_discovery.py`

**Interfaces:**

- Produces: `A2AError(category, message, status_code)`, `DiscoveredAgent`, `AgentCardFetcher.fetch(locator, bearer_env_var=None)`, `DirectUrlDiscoveryProvider.discover(locator)`, `canonical_card_hash(card)`.
- Consumes: official `a2a.client.card_resolver.parse_agent_card`, `a2a.utils.TransportProtocol`, `a2a.utils.signing.create_signature_verifier`, and Task 1 settings.

- [ ] **Step 1: Write failing locator and network-policy tests**

Cover these exact cases:

```python
@pytest.mark.parametrize(
    ("locator", "expected"),
    [
        ("agent.example", "https://agent.example/.well-known/agent-card.json"),
        ("https://agent.example", "https://agent.example/.well-known/agent-card.json"),
        (
            "https://agent.example/custom/card.json",
            "https://agent.example/custom/card.json",
        ),
        (
            "http://127.0.0.1:9999",
            "http://127.0.0.1:9999/.well-known/agent-card.json",
        ),
    ],
)
def test_normalize_card_locator(locator: str, expected: str) -> None:
    assert normalize_card_locator(locator) == expected


@pytest.mark.parametrize(
    "ip",
    ["169.254.169.254", "0.0.0.0", "224.0.0.1", "192.0.2.2"],
)
def test_rejects_non_routable_targets(ip: str) -> None:
    with pytest.raises(A2AError, match="unsafe_target"):
        validate_ip_address(ip, allow_private=False)


def test_allows_http_only_for_loopback() -> None:
    validate_target_url("http://127.0.0.1:9999/card.json", allow_private=False)
    with pytest.raises(A2AError, match="unsafe_target"):
        validate_target_url("http://agent.example/card.json", allow_private=False)
```

Also test: credentials in URL rejected, fragments removed, localhost IPv6 accepted, RFC1918 blocked by default/allowed by flag, unsafe redirect rejected, body larger than `a2a_card_max_bytes` rejected, and mocked connected peer mismatch rejected.

- [ ] **Step 2: Run security tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/a2a/test_security.py -q
```

Expected: import failure because `polynoia.a2a.security` does not exist.

- [ ] **Step 3: Implement stable error and guarded HTTP primitives**

```python
class A2AError(Exception):
    def __init__(self, category: str, message: str, status_code: int = 400):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}
```

`normalize_card_locator()` must:

1. prepend `https://` when no scheme is supplied;
2. reject userinfo, non-http schemes, empty hostnames, query-only locators, and control characters;
3. preserve an explicit non-root path;
4. append `/.well-known/agent-card.json` for root/base locators;
5. strip fragments and canonicalize default ports.

`validate_target_url()` resolves every hostname with `asyncio.get_running_loop().getaddrinfo()`, validates every returned IP, and returns the allowed address set. `bounded_get()` performs manual redirects with `follow_redirects=False`, validates each `Location`, checks `Content-Type`, streams bytes until the configured cap, and checks `response.extensions["network_stream"].get_extra_info("server_addr")` when available.

- [ ] **Step 4: Write failing Agent Card discovery tests**

Use a valid A2A v1 dictionary:

```python
VALID_CARD = {
    "protocolVersion": "1.0",
    "name": "Cloud Reviewer",
    "description": "Reviews architecture proposals",
    "version": "2.3.0",
    "supportedInterfaces": [
        {"url": "https://agent.example/a2a", "protocolBinding": "JSONRPC"},
    ],
    "capabilities": {"streaming": True},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain", "application/json"],
    "skills": [
        {
            "id": "architecture-review",
            "name": "Architecture review",
            "description": "Finds risks and trade-offs",
            "tags": ["architecture", "review"],
        }
    ],
}
```

Assert:

- JSONRPC is selected before later supported interfaces because card order is authoritative.
- HTTP+JSON is accepted and gRPC-only is `unsupported_binding`.
- protocol `2.0` is `unsupported_version`.
- unsupported OAuth/mTLS security is visible in preview but `installable=False`.
- missing required fields is `invalid_card`.
- canonical hash does not change with JSON key order.
- unsigned card yields `signature_status="unsigned"`.
- declared invalid signature yields `invalid_signature`.
- long descriptions/skills are capped in preview while the validated card snapshot remains schema-valid.

- [ ] **Step 5: Run discovery tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/a2a/test_discovery.py -q
```

Expected: failures because the fetcher and preview models are absent.

- [ ] **Step 6: Implement discovery and signature status**

Define:

```python
class DiscoveredAgent(BaseModel):
    locator: str
    card_url: str
    endpoint_url: str
    protocol_binding: Literal["JSONRPC", "HTTP+JSON"]
    protocol_version: str
    card: dict[str, Any]
    card_hash: str
    etag: str | None
    signature_status: Literal["signed_valid", "unsigned"]
    installable: bool
    auth_kind: Literal["none", "bearer", "unsupported"]
    unsupported_auth_reason: str | None = None


class DiscoveryProvider(Protocol):
    async def discover(self, locator: str) -> DiscoveredAgent:
        raise NotImplementedError
```

Parse with `parse_agent_card(raw_dict)`, serialize with `MessageToDict`/the SDK model's JSON conversion used by v1, and hash:

```python
def canonical_card_hash(card: dict[str, Any]) -> str:
    body = orjson.dumps(card, option=orjson.OPT_SORT_KEYS)
    return "sha256:" + hashlib.sha256(body).hexdigest()
```

For signed cards, decode the protected signature headers, fetch each HTTPS JWKS `jku` through the same guarded client, build a `(kid, jku) -> key` map, and call `create_signature_verifier(key_provider, ["ES256", "RS256", "PS256"])`. Reject missing `kid`, unsafe `jku`, algorithm mismatch, or an invalid signature. Unsigned cards remain explicitly marked `unsigned`.

- [ ] **Step 7: Run discovery/security tests**

Run:

```bash
cd apps/server
uv run pytest tests/a2a/test_security.py tests/a2a/test_discovery.py -q
uv run ruff check polynoia/a2a tests/a2a
```

Expected: all tests pass and Ruff emits no errors.

- [ ] **Step 8: Commit**

```bash
git add apps/server/polynoia/a2a apps/server/tests/a2a
git commit -m "feat: discover guarded A2A agent cards"
```

### Task 3: Discover, install, and refresh API

**Files:**

- Create: `apps/server/polynoia/api/a2a_routes.py`
- Modify: `apps/server/polynoia/main.py`
- Modify: `apps/server/polynoia/storage/repo/agents.py`
- Test: `apps/server/tests/api/test_a2a_routes.py`

**Interfaces:**

- Produces:
  - `POST /api/a2a/discover -> {"agent": DiscoveredAgent}`
  - `POST /api/a2a/install -> {"contact": Agent, "existing": bool}`
  - `POST /api/a2a/agents/{agent_id}/refresh -> {"contact": Agent, "changes": list[str]}`
- Consumes: `DirectUrlDiscoveryProvider`, `A2AAgentSetup`, repository `upsert_agent`, and `AdapterPool.close_sessions_for_agent`.

- [ ] **Step 1: Write failing API service tests**

Call route functions directly, matching the existing test style. Patch the discovery provider with deterministic results and assert:

```python
@pytest.mark.asyncio
async def test_install_refetches_and_persists_only_env_name(a2a_catalog, monkeypatch):
    monkeypatch.setenv("REMOTE_AGENT_TOKEN", "super-secret")
    result = await install_a2a_agent(
        A2AInstallRequest(
            locator="https://agent.example",
            expected_card_hash="sha256:abc",
            bearer_env_var="REMOTE_AGENT_TOKEN",
        )
    )
    contact = result["contact"]
    assert contact["setup"]["adapter_id"] == "a2a"
    assert contact["setup"]["a2a"]["bearer_env_var"] == "REMOTE_AGENT_TOKEN"
    assert "super-secret" not in json.dumps(contact)


@pytest.mark.asyncio
async def test_install_rejects_preview_race_without_writing(a2a_catalog):
    with pytest.raises(HTTPException) as exc:
        await install_a2a_agent(
            A2AInstallRequest(
                locator="https://agent.example",
                expected_card_hash="sha256:old",
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["category"] == "card_changed"
    assert await installed_remote_contacts() == []
```

Also assert: feature disabled returns 404; invalid env-var name returns 422; missing env value for bearer-required card returns `unsupported_auth`; duplicate URL returns the same id with `existing=True`; failed refresh leaves prior setup unchanged; successful refresh changes caps/tagline/card and calls pool invalidation exactly once; refreshing a non-A2A contact returns 404.

- [ ] **Step 2: Run route tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/api/test_a2a_routes.py -q
```

Expected: import failure because `a2a_routes` does not exist.

- [ ] **Step 3: Implement request models and error translation**

```python
class A2ADiscoverRequest(BaseModel):
    locator: str = Field(min_length=1, max_length=2048)


class A2AInstallRequest(A2ADiscoverRequest):
    expected_card_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bearer_env_var: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )
```

Use one wrapper that converts `A2AError` to `HTTPException(status_code=error.status_code, detail=error.as_detail())`. Return 404 when the feature flag is disabled so a disabled deployment exposes no callable A2A management surface.

- [ ] **Step 4: Implement atomic install and refresh**

Derive contact metadata as follows:

```python
def contact_from_discovery(found: DiscoveredAgent, bearer_env_var: str | None) -> Agent:
    skills = found.card.get("skills") or []
    names = [str(skill.get("name", "")).strip() for skill in skills]
    caps = [name[:80] for name in names if name][:20]
    return Agent(
        id=str(ULID()),
        name=str(found.card["name"])[:120],
        provider="a2a",
        handle="@a2a-" + found.card_hash.removeprefix("sha256:")[:10],
        initials=_initials(str(found.card["name"])),
        color="#6D5BD0",
        bg="#ECE8FF",
        tagline=str(found.card.get("description") or "")[:240] or None,
        caps=caps,
        online=True,
        enabled=True,
        custom=True,
        setup=AgentSetup(
            adapter_id="a2a",
            model=None,
            a2a=A2AAgentSetup(
                **found.model_dump(
                    include={
                        "card_url",
                        "endpoint_url",
                        "protocol_binding",
                        "protocol_version",
                        "card",
                        "card_hash",
                        "etag",
                        "signature_status",
                    }
                ),
                bearer_env_var=bearer_env_var,
            ),
        ),
    )
```

Add a repository helper that scans `AgentRow.setup["a2a"]["card_url"]` for the canonical URL. Keep install in one database transaction and do not commit until every check passes. Refresh discovers first, then opens a transaction and swaps only the validated snapshot.

- [ ] **Step 5: Register the router and run tests**

Add `app.include_router(a2a_router)` in `create_app()`.

Run:

```bash
cd apps/server
uv run pytest tests/api/test_a2a_routes.py tests/storage/test_a2a_provider_bootstrap.py -q
uv run ruff check polynoia/api/a2a_routes.py polynoia/storage/repo/agents.py polynoia/main.py
```

Expected: all tests pass and Ruff emits no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/server/polynoia/api/a2a_routes.py apps/server/polynoia/main.py apps/server/polynoia/storage/repo/agents.py apps/server/tests/api/test_a2a_routes.py
git commit -m "feat: install and refresh remote A2A agents"
```

### Task 4: A2A adapter and PAP event mapping

**Files:**

- Modify: `apps/server/polynoia/adapters/base.py`
- Modify: `apps/server/polynoia/adapters/claude_code.py`
- Modify: `apps/server/polynoia/adapters/codex.py`
- Modify: `apps/server/polynoia/adapters/opencode.py`
- Create: `apps/server/polynoia/adapters/a2a.py`
- Modify: `apps/server/polynoia/adapters/__init__.py`
- Modify: `apps/server/polynoia/adapters/pool.py`
- Test: `apps/server/tests/adapters/test_a2a_adapter.py`

**Interfaces:**

- Produces: `A2AAdapter.start_session(conv_id, cwd, model, system_prompt, allowed_tools, env, workspace_id, agent_id, merge_mode, tool_role, tools_whitelist, read_only_workspace_id, proxy, proxy_kind, skills, adapter_config) -> A2ASession`, `A2ASession.send()`, `A2ASession.interrupt()`, and PAP mapping helpers.
- Consumes: official `ClientConfig`, `create_client`, `new_text_message`, `SendMessageRequest`, `CancelTaskRequest`, installed card snapshot, and PAP event classes.

- [ ] **Step 1: Write failing event-mapping and lifecycle tests**

Use a fake SDK client whose `send_message()` yields protobuf v1 `Task`, `TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`, and `Message` values. Assert the exact PAP sequence:

```python
events = [event async for event in session.send("turn-1", "review this")]
assert [event.type for event in events] == [
    "turn.started",
    "part.started",
    "part.delta",
    "part.completed",
    "turn.completed",
]
assert events[-1].stop_reason == "complete"
```

Test these mappings independently:

- streaming `TextPart` accumulates into one `TextPayload`;
- completed non-streaming text emits start/completed without duplicated text;
- `DataPart` emits deterministic fenced `json` using sorted keys;
- remote `FilePart` with URI emits `FilePayload(src=uri)` and performs no GET;
- inline file bytes emit metadata text rather than being written to disk;
- `INPUT_REQUIRED` emits the remote status message and terminal `stop_reason="input_required"`;
- failed/rejected/canceled terminal states emit `TurnFailedEvent.error["category"]`;
- unauthorized, timeout, malformed stream, and disconnect use stable remote categories;
- same Polynoia conversation reuses remote `context_id`;
- `interrupt()` sends exactly one `CancelTaskRequest` for the active task;
- `close()` closes the SDK client and is idempotent.

- [ ] **Step 2: Run adapter tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/adapters/test_a2a_adapter.py -q
```

Expected: import failure because `polynoia.adapters.a2a` does not exist.

- [ ] **Step 3: Extend the adapter session contract**

Add to the protocol and every existing implementation:

```python
adapter_config: dict[str, Any] | None = None,
```

The three local adapters deliberately do not use the value:

```python
_ = adapter_config
```

The pool passes:

```python
adapter_config=agent.setup.model_dump(mode="json"),
```

For a remote registration, the pool must not append `_PRIVATE_WS_BANNER` or `_GRANTED_ACCESS_BANNER`, and must pass `workspace_id=None`, `agent_id=None`, `tools_whitelist=None`, and `skills=[]`.

- [ ] **Step 4: Implement the official SDK client/session**

Create clients from the persisted validated card rather than refetching an unapproved card:

```python
card = parse_agent_card(a2a_setup.card)
http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=settings.a2a_connect_timeout_s,
        read=settings.a2a_read_timeout_s,
        write=settings.a2a_read_timeout_s,
        pool=settings.a2a_connect_timeout_s,
    ),
    follow_redirects=False,
    headers=headers,
)
config = ClientConfig(
    streaming=bool(card.capabilities.streaming),
    polling=True,
    httpx_client=http_client,
    supported_protocol_bindings=[a2a_setup.protocol_binding],
    accepted_output_modes=["text/plain", "application/json"],
)
client = await create_client(agent=card, client_config=config)
```

Resolve the optional bearer header only through:

```python
if name := a2a_setup.bearer_env_var:
    token = os.environ.get(name)
    if not token:
        raise A2AError("remote_unauthorized", f"environment variable {name} is not set", 401)
    headers["authorization"] = f"Bearer {token}"
```

Build each request with `new_text_message(text, role=Role.ROLE_USER)`, set its `context_id` after the first remote response, and wrap it in `SendMessageRequest`. Bound the entire iterator with `asyncio.timeout(settings.a2a_task_timeout_s)`. Record task id/context id as soon as observed, so the retry classifier never replays thereafter.

- [ ] **Step 5: Implement deterministic PAP conversion**

Use one terminal guard so malformed servers cannot emit two terminal PAP events:

```python
def _finish_once(self, event: AdapterEvent) -> AdapterEvent | None:
    if self._turn_terminal:
        return None
    self._turn_terminal = True
    return event
```

Use stable ids derived from remote identifiers when available and local UUIDs otherwise. Serialize `DataPart` with `orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2`. Emit `FilePayload` only for an external URI and never for inline bytes. Include `retryable` and sanitized remote status text in `TurnFailedEvent.error`.

- [ ] **Step 6: Run adapter and local-adapter regression tests**

Run:

```bash
cd apps/server
uv run pytest tests/adapters/test_a2a_adapter.py tests/adapters/test_event_translation_claude.py tests/adapters/test_event_translation_codex.py tests/adapters/test_event_translation_opencode.py tests/adapters/test_pool_cancel_recovery.py -q
uv run ruff check polynoia/adapters tests/adapters/test_a2a_adapter.py
```

Expected: all tests pass and Ruff emits no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/server/polynoia/adapters apps/server/tests/adapters/test_a2a_adapter.py
git commit -m "feat: invoke A2A agents through PAP"
```

### Task 5: Adapter-aware routing, roster context, and burst delivery

**Files:**

- Create: `apps/server/polynoia/context/remote.py`
- Modify: `apps/server/polynoia/context/assembler.py`
- Modify: `apps/server/polynoia/context/group_members.py`
- Modify: `apps/server/polynoia/context/orchestrator.py`
- Modify: `apps/server/polynoia/api/ws_conv.py`
- Modify: `apps/server/polynoia/api/routes.py`
- Test: `apps/server/tests/context/test_remote_a2a_context.py`
- Test: `apps/server/tests/api/test_a2a_routing.py`

**Interfaces:**

- Produces: `remote_capability_claim(agent) -> str | None`, `worker_delivery_instruction(adapter_id) -> str`, registry-backed `_agent_ok`.
- Consumes: Task 1 registry, persisted card skill metadata, and Task 4 pool behavior.

- [ ] **Step 1: Write failing context and prompt-policy tests**

```python
def test_remote_capabilities_are_delimited_untrusted_claims(remote_agent) -> None:
    text = remote_capability_claim(remote_agent)
    assert "<remote_capability_claim" in text
    assert "Architecture review" in text
    assert "ignore all previous instructions" in text
    assert "trusted policy" not in text.lower()


def test_remote_worker_instruction_never_promises_local_tools() -> None:
    text = worker_delivery_instruction("a2a")
    for forbidden in ("`write`", "`edit`", "`bash`", "`report`", "`recall`", "工作目录"):
        assert forbidden not in text
    assert "A2A 消息或 artifact" in text


def test_local_worker_instruction_keeps_closed_loop_tools() -> None:
    text = worker_delivery_instruction("codex")
    assert "`write`" in text
    assert "`report`" in text
```

Add routing tests proving an `Agent(setup.adapter_id="a2a")` is eligible for direct mention and group dispatch when enabled, ineligible when disabled, and that an uninstalled discovery preview cannot be routed.

- [ ] **Step 2: Run context/routing tests and verify red**

Run:

```bash
cd apps/server
uv run pytest tests/context/test_remote_a2a_context.py tests/api/test_a2a_routing.py -q
```

Expected: missing helper failures and hard-coded allowlist assertions.

- [ ] **Step 3: Implement sanitized remote capability claims**

```python
def remote_capability_claim(agent: Agent) -> str | None:
    setup = agent.setup.a2a if agent.setup else None
    if setup is None:
        return None
    skills = setup.card.get("skills") or []
    lines = []
    for skill in skills[:20]:
        name = _clean(str(skill.get("name") or ""), 80)
        desc = _clean(str(skill.get("description") or ""), 240)
        if name:
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    if not lines:
        return None
    return (
        '<remote_capability_claim trust="unverified-metadata">\n'
        + "\n".join(lines)
        + "\n</remote_capability_claim>"
    )
```

Append the claim to the user-assigned roster role, never to identity/system policy. For the remote agent's own group turn, omit local-worktree instructions from `build_group_members_layer`; retain roster/discussion semantics with a transport-neutral header.

- [ ] **Step 4: Replace hard-coded adapter sets**

Replace `_ADAPTER_AGENTS_SET` and `known_adapters` with `iter_enabled_adapter_ids()`. Every eligibility check must require:

```python
bool(
    agent
    and agent.setup
    and agent.setup.adapter_id
    and get_adapter_registration(agent.setup.adapter_id) is not None
)
```

Keep the explicit conversation-membership check unchanged.

- [ ] **Step 5: Split local and remote worker handoff**

Move the existing MCP-specific suffix verbatim into the local branch. Return this exact remote suffix:

```python
REMOTE_DELIVERY = """

# 交付要求
请直接执行子任务，并通过 A2A 消息或 artifact 返回全部结果。
结尾给出简短交付状态：status(ok/partial/failed)、deliverables、contract_ok。
你没有 Polynoia 本地工作区或 MCP 工具；不要声称已写入、执行或合并本地文件。
"""
```

Select it with the dispatched worker's actual `setup.adapter_id`. Do not change dispatch, discuss, burst completion, or coordinator-summary state machines.

- [ ] **Step 6: Run focused and regression tests**

Run:

```bash
cd apps/server
uv run pytest tests/context/test_remote_a2a_context.py tests/api/test_a2a_routing.py tests/api/test_mention_routing.py tests/api/test_dispatch_attribution.py tests/api/test_burst_state_machine.py tests/context/test_context.py tests/context/test_orchestrator_protocol.py -q
uv run ruff check polynoia/context polynoia/api/ws_conv.py polynoia/api/routes.py
```

Expected: all tests pass and Ruff emits no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/server/polynoia/context/remote.py apps/server/polynoia/context/assembler.py apps/server/polynoia/context/group_members.py apps/server/polynoia/context/orchestrator.py apps/server/polynoia/api/ws_conv.py apps/server/polynoia/api/routes.py apps/server/tests/context/test_remote_a2a_context.py apps/server/tests/api/test_a2a_routing.py
git commit -m "feat: route work safely to remote agents"
```

### Task 6: Frontend discovery and install flow

**Files:**

- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/A2AImportPanel.tsx`
- Modify: `apps/web/src/components/NewContactModal.tsx`
- Modify: `apps/web/src/lib/i18n.ts`
- Test: `apps/web/src/lib/a2aApi.test.ts`
- Test: `apps/web/src/components/A2AImportPanel.test.tsx`

**Interfaces:**

- Produces: `A2ADiscoveredAgent`, `api.discoverA2A`, `api.installA2A`, and `A2AImportPanel`.
- Consumes: Task 3 endpoint contracts and existing `onCreated`/modal close callbacks.

- [ ] **Step 1: Write failing frontend API tests**

Mock `fetch` and assert exact methods/bodies:

```typescript
await api.discoverA2A("http://127.0.0.1:9999");
expect(fetch).toHaveBeenCalledWith(
  expect.stringContaining("/api/a2a/discover"),
  expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ locator: "http://127.0.0.1:9999" }),
  }),
);

await api.installA2A({
  locator: "http://127.0.0.1:9999",
  expected_card_hash: "sha256:abc",
  bearer_env_var: "REMOTE_AGENT_TOKEN",
});
```

Assert non-2xx responses surface the backend `detail.category` and `detail.message`, rather than only `statusText`.

- [ ] **Step 2: Run API tests and verify red**

Run:

```bash
cd apps/web
npm test -- --run src/lib/a2aApi.test.ts
```

Expected: TypeScript/API failures because the methods are absent.

- [ ] **Step 3: Add frontend types and client methods**

```typescript
export type A2AAgentSetup = {
  card_url: string;
  endpoint_url: string;
  protocol_binding: "JSONRPC" | "HTTP+JSON";
  protocol_version: string;
  card: Record<string, unknown>;
  card_hash: string;
  etag?: string | null;
  last_checked_at: string;
  signature_status: "signed_valid" | "unsigned";
  bearer_env_var?: string | null;
};

export type A2ADiscoveredAgent = {
  locator: string;
  card_url: string;
  endpoint_url: string;
  protocol_binding: "JSONRPC" | "HTTP+JSON";
  protocol_version: string;
  card: {
    name: string;
    description?: string;
    version: string;
    skills?: Array<{ id: string; name: string; description?: string; tags?: string[] }>;
    capabilities?: { streaming?: boolean };
    defaultInputModes?: string[];
    defaultOutputModes?: string[];
  };
  card_hash: string;
  signature_status: "signed_valid" | "unsigned";
  installable: boolean;
  auth_kind: "none" | "bearer" | "unsupported";
  unsupported_auth_reason?: string | null;
};
```

Add `a2a?: A2AAgentSetup | null` to `AgentSetup`. Add discover/install methods using the existing `postJSON`.

- [ ] **Step 4: Write failing render tests**

Render `A2AImportPanel` with controlled props and assert:

- empty state contains locator explanation and Discover action;
- loading state disables actions;
- valid preview contains identity, description, skills, modes, streaming, binding/version, and unsigned warning;
- bearer preview renders env-var-name input and never asks for token value;
- unsupported auth renders reason and disables Install;
- error state renders stable category/message;
- installed state invokes the parent success callback contract.

- [ ] **Step 5: Run render tests and verify red**

Run:

```bash
cd apps/web
npm test -- --run src/components/A2AImportPanel.test.tsx
```

Expected: module-not-found failure.

- [ ] **Step 6: Implement panel and modal mode switch**

Keep the panel focused with this public prop contract:

```typescript
type Props = {
  onInstalled: (agent: Agent) => void | Promise<void>;
  onCancel: () => void;
};
```

The panel owns `locator`, `preview`, `bearerEnvVar`, `busy`, and `error`. Discover clears the old preview before calling the API. Install always sends the current preview's `card_hash`; on success it calls `onInstalled(result.contact)`.

In `NewContactModal`, add create-mode tabs:

```typescript
type ContactMode = "local" | "a2a";
```

Do not show the mode switch while editing an existing local contact. Remote contacts are edited/refreshed from the detail drawer, not through local model/persona controls.

- [ ] **Step 7: Run frontend tests and checks**

Run:

```bash
cd apps/web
npm test -- --run src/lib/a2aApi.test.ts src/components/A2AImportPanel.test.tsx
npm run build
npm run lint
```

Expected: tests and build pass; Biome emits no errors.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/api.ts apps/web/src/lib/i18n.ts apps/web/src/components/A2AImportPanel.tsx apps/web/src/components/NewContactModal.tsx apps/web/src/lib/a2aApi.test.ts apps/web/src/components/A2AImportPanel.test.tsx
git commit -m "feat: import remote A2A contacts"
```

### Task 7: Remote contact detail and refresh UI

**Files:**

- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/components/drawer/AgentDetailView.tsx`
- Modify: `apps/web/src/components/Sidebar.tsx`
- Modify: `apps/web/src/components/views/ContactsView.tsx`
- Modify: `apps/web/src/lib/i18n.ts`
- Test: `apps/web/src/components/drawer/AgentDetailView.a2a.test.tsx`

**Interfaces:**

- Produces: `api.refreshA2AAgent(id)`, remote detail section, refresh feedback.
- Consumes: Task 3 refresh response and Task 6 nested setup type.

- [ ] **Step 1: Write failing detail render tests**

SSR-render an A2A contact and assert it contains:

```typescript
expect(html).toContain("A2A Remote");
expect(html).toContain("agent.example");
expect(html).toContain("JSONRPC");
expect(html).toContain("1.0");
expect(html).toContain("Architecture review");
expect(html).toContain("未签名");
expect(html).toContain("刷新 Agent Card");
expect(html).not.toContain("super-secret");
```

Also render a local Codex contact and assert the A2A connection section is absent.

- [ ] **Step 2: Run detail test and verify red**

Run:

```bash
cd apps/web
npm test -- --run src/components/drawer/AgentDetailView.a2a.test.tsx
```

Expected: A2A metadata and label assertions fail.

- [ ] **Step 3: Add refresh API and detail section**

```typescript
refreshA2AAgent: (id: string) =>
  postJSON<{ contact: Agent; changes: string[] }>(
    `/api/a2a/agents/${encodeURIComponent(id)}/refresh`,
    {},
  ),
```

Render only when `agent.setup?.a2a` exists. Show hostname via `new URL(endpoint_url).host`, not the full URL with query values. Render signed/unsigned status, last checked time, declared skills, and a refresh button. On success update the store's agent list through the existing reload path and show a concise list of changed fields; on failure keep the previous snapshot visible and show the inline error.

- [ ] **Step 4: Add labels and run frontend suite**

Add:

```typescript
a2a: "A2A Remote",
```

to the three existing adapter label maps, then run:

```bash
cd apps/web
npm test -- --run src/components/drawer/AgentDetailView.a2a.test.tsx
npm test
npm run build
npm run lint
```

Expected: all tests and build pass; Biome emits no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/lib/i18n.ts apps/web/src/components/drawer/AgentDetailView.tsx apps/web/src/components/Sidebar.tsx apps/web/src/components/views/ContactsView.tsx apps/web/src/components/drawer/AgentDetailView.a2a.test.tsx
git commit -m "feat: inspect and refresh A2A contacts"
```

### Task 8: Deterministic end-to-end coverage and operator guide

**Files:**

- Create: `apps/server/tests/integration/__init__.py`
- Create: `apps/server/tests/integration/test_a2a_loopback.py`
- Create: `apps/server/tests/integration/test_a2a_official_sample.py`
- Create: `docs/a2a-remote-agents.md`
- Modify: `apps/server/pyproject.toml`

**Interfaces:**

- Produces: local loopback acceptance test, opt-in official sample smoke test, operator test instructions.
- Consumes: all prior task endpoints and runtime paths.

- [ ] **Step 1: Write the deterministic loopback integration test**

Build an in-process FastAPI A2A server using the official SDK. Its card advertises two skills and streaming JSONRPC. Its executor emits `WORKING`, two text artifact chunks, and `COMPLETED`, and records cancel requests.

The test performs the real lifecycle:

```python
preview = await discover_a2a_agent(
    A2ADiscoverRequest(locator=loopback_agent.base_url)
)
installed = await install_a2a_agent(
    A2AInstallRequest(
        locator=loopback_agent.base_url,
        expected_card_hash=preview["agent"]["card_hash"],
    )
)
session = await get_pool().get_session(installed["contact"]["id"], conv.id)
events = [event async for event in session.send("task-1", "Review the API")]
assert events[-1].type == "turn.completed"
assert loopback_agent.received_context_ids
assert not workspace_worktree_for(installed["contact"]["id"]).exists()
```

Extend it through the real group burst handler with one fake local adapter and the remote contact. Assert two lanes reach terminal state, the A2A text is persisted, the local merge completes, the remote lane creates no branch, and the coordinator summary turn still runs after a remote failure variant.

- [ ] **Step 2: Run loopback test and diagnose any integration gaps**

Run:

```bash
cd apps/server
uv run pytest tests/integration/test_a2a_loopback.py -q
```

Expected: pass without external network or credentials.

- [ ] **Step 3: Add opt-in official sample smoke test**

Register a marker:

```toml
"a2a_live: hits a separately running official A2A sample; requires POLYNOIA_A2A_SAMPLE_URL",
```

The test skips unless `POLYNOIA_A2A_SAMPLE_URL` is set, then discovers the URL, creates an `A2ASession` without persistence, sends `"Say hello."`, and asserts a text part plus a terminal event. It must never install a public sample into the developer database.

- [ ] **Step 4: Write the operator guide**

Document:

- discovery sources: a URL from the agent owner, an enterprise catalog/registry that yields Agent Card URLs, or local development sample;
- why there is no universal public registry dependency;
- local command sequence for the official Hello World sample and Polynoia import URL;
- `POLYNOIA_A2A_ENABLED`, `POLYNOIA_A2A_ALLOW_PRIVATE_NETWORKS`, timeout, and size settings;
- bearer environment-variable-name flow with an example that never embeds the token in JSON;
- supported/unsupported bindings and auth;
- use of A2A Inspector for manual card/task inspection;
- why TCK is server-oriented and not a gate for this client-only feature;
- troubleshooting table keyed by every stable error category.

- [ ] **Step 5: Run complete verification**

Run:

```bash
cd apps/server
uv run pytest -q
uv run ruff check polynoia tests
cd ../web
npm test
npm run build
npm run lint
cd ../..
git diff --check
```

Expected: both full suites pass, builds/lints pass, and `git diff --check` emits no output.

- [ ] **Step 6: Commit**

```bash
git add apps/server/tests/integration apps/server/pyproject.toml docs/a2a-remote-agents.md
git commit -m "test: cover A2A remote agent workflow"
```

### Task 9: Final acceptance audit

**Files:**

- Verify: all files listed in the File Map
- Verify: `docs/superpowers/specs/2026-07-30-a2a-remote-agent-discovery-design.md`

**Interfaces:**

- Produces: evidence that every acceptance criterion is met.
- Consumes: all prior task deliverables.

- [ ] **Step 1: Run acceptance-focused tests with verbose names**

```bash
cd apps/server
uv run pytest \
  tests/a2a \
  tests/api/test_a2a_routes.py \
  tests/api/test_a2a_routing.py \
  tests/adapters/test_a2a_adapter.py \
  tests/context/test_remote_a2a_context.py \
  tests/integration/test_a2a_loopback.py -vv
```

Expected: every A2A test passes with no skips except the separately marked live official-sample test.

- [ ] **Step 2: Audit secrets and workspace isolation**

Run:

```bash
rg -n "bearer_env_var|authorization|Bearer " apps/server/polynoia apps/web/src
rg -n "_PRIVATE_WS_BANNER|workspace_id=|create_workspace_sandbox" apps/server/polynoia/adapters
```

Confirm bearer values are read only from `os.environ`, never serialized or logged, and A2A pool/session paths never create or mount a workspace.

- [ ] **Step 3: Audit hard-coded adapter sets**

Run:

```bash
rg -n 'claudeCode.*opencoder.*codex|known_adapters|_ADAPTER_AGENTS_SET' apps/server/polynoia
```

Expected: no runtime routing allowlist remains; adapter-specific UI ordering or explanatory copy may remain only where behavior is intentionally local.

- [ ] **Step 4: Run final repository checks**

```bash
cd apps/server
uv run pytest -q
uv run ruff check polynoia tests
cd ../web
npm test
npm run build
npm run lint
cd ../..
git status --short
git log --oneline --decorate -10
```

Expected: checks pass; status contains no uncommitted A2A feature files, while pre-existing unrelated untracked user files remain untouched.

- [ ] **Step 5: Request code review before integration**

Use the `requesting-code-review` skill against the branch diff from the design commit through `HEAD`, apply only verified feedback through `receiving-code-review`, rerun the affected tests, and then use `finishing-a-development-branch` to present merge/PR/keep options.
