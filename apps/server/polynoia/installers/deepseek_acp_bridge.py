"""Patch DeepSeek Harness rc.8's ACP transport into a controlled MCP bridge.

The upstream ``@deepseek-ai/dsh-acp`` rc.8 transport intentionally rejects
``session/new.mcpServers`` and only projects assistant messages onto ACP.  This
module applies a narrow, pinned-source transformation which:

* admits exactly one stdio server named ``polynoia``;
* mounts ``@deepseek-ai/dsh-mcp-client`` in the created Agent's scope;
* denies every tool outside that scoped MCP namespace;
* asks ACP permission for effectful Polynoia tools; and
* projects the durable DSH ``tool/call`` / ``tool/result`` pair as ACP
  ``tool_call`` / ``tool_call_update`` notifications.

It is deliberately version-locked.  An unknown upstream source shape fails the
installer instead of silently leaving DeepSeek Harness with an unsafe or opaque
tool surface.
"""

from __future__ import annotations

from pathlib import Path

_IMPORT_ANCHOR = 'import { isImageAdmissionError } from "@deepseek-ai/dsh-attachment";'
_IMPORT_PATCH = (
    _IMPORT_ANCHOR
    + '\nimport * as mcpClient from "@deepseek-ai/dsh-mcp-client";'
)

_OWNED_RECORD_ANCHOR = """\tconst ownedRecord = (agent) => {
\t\tconst record = sessions.get(agent.session.id);
\t\treturn record?.agent === agent ? record : void 0;
\t};"""

_OWNED_RECORD_PATCH = _OWNED_RECORD_ANCHOR + r'''
	const POLYNOIA_CONTROLLED_BRIDGE_VERSION = 2;
	const POLYNOIA_MCP_PREFIX = "mcp__polynoia__";
	const APPROVAL_FREE_TOOLS = new Set(["read", "grep", "glob", "recall", "wait", "ask_user"]);
	const rawToolName = (name) => name.startsWith(POLYNOIA_MCP_PREFIX) ? name.slice(POLYNOIA_MCP_PREFIX.length) : name;
	const acpMeta = (name) => ({
		is_mcp_tool_call: true,
		toolName: rawToolName(name),
		polynoia: {
			source: "mcp",
			provider: "deepseek-harness",
			server: "polynoia",
			tool: rawToolName(name)
		}
	});
	const parseArguments = (value) => {
		try {
			return JSON.parse(value);
		} catch {
			return { raw: value };
		}
	};
	const acpRawInput = (name, value) => {
		const arguments_ = parseArguments(value);
		return arguments_ !== null && typeof arguments_ === "object" && !Array.isArray(arguments_)
			? { ...arguments_, server: "polynoia", tool: rawToolName(name) }
			: { server: "polynoia", tool: rawToolName(name), arguments: arguments_ };
	};
	const toolKind = (name) => {
		const raw = rawToolName(name);
		if (["read", "recall"].includes(raw)) return "read";
		if (["grep", "glob"].includes(raw)) return "search";
		if (["write", "edit", "resolve_conflict"].includes(raw)) return "edit";
		if (["bash", "run_background", "wait"].includes(raw)) return "execute";
		return "other";
	};
	const acpContent = (blocks) => {
		const projected = [];
		for (const block of blocks) {
			if (block?.type === "text" && typeof block.text === "string") {
				projected.push({ type: "content", content: { type: "text", text: block.text } });
				continue;
			}
			projected.push({
				type: "content",
				content: { type: "text", text: `[DeepSeek Harness content: ${block?.type ?? "unknown"}]` }
			});
		}
		return projected;
	};
	const controlledToolPolicy = (exec, next) => {
		if (!exec.name.startsWith(POLYNOIA_MCP_PREFIX)) return Promise.resolve({
			kind: "deny",
			reason: `DeepSeek Harness native tool ${JSON.stringify(exec.name)} is disabled; only Polynoia MCP tools are permitted`
		});
		const raw = rawToolName(exec.name);
		if (APPROVAL_FREE_TOOLS.has(raw)) return next();
		return Promise.resolve({
			kind: "ask",
			reason: `Allow Polynoia MCP tool ${raw} for this call?`
		});
	};'''

_EVENT_ANCHOR = """\tctx.on("session/event", (session, event) => {
\t\tconst record = sessions.get(session.header.id);
\t\tif (record === void 0 || record.agent.session !== session) return;
\t\ttry {
\t\t\tif (event.type === "assistant/message") {"""

_EVENT_PATCH = r'''	ctx.on("session/event", (session, event) => {
		const record = sessions.get(session.header.id);
		if (record === void 0 || record.agent.session !== session) return;
		try {
			if (event.type === "tool/call") {
				const { callId, name, arguments: rawArguments } = event.data;
				const callKey = callId || "__polynoia_empty_call__";
				const wireCallId = callId || `dsh-mcp-${session.header.id}-${event.data.turn}-${event.data.step}-${++record.toolSequence}`;
				const toolCall = {
					toolCallId: wireCallId,
					title: `mcp.polynoia.${rawToolName(name)}`,
					kind: toolKind(name),
					status: "in_progress",
					rawInput: acpRawInput(name, rawArguments),
					_meta: acpMeta(name)
				};
				record.toolCalls.set(callKey, { name, wire: toolCall });
				record.outputTail = record.outputTail.then(() => notify({
					sessionId: record.agent.session.id,
					update: {
						sessionUpdate: "tool_call",
						...toolCall
					}
				}));
			}
			if (event.type === "tool/result") {
				const result = event.data.message.content.find((block) => block.type === "tool-result");
				const sourceCallId = result?.toolCallId ?? event.data.message.source.callId;
				const callKey = sourceCallId || "__polynoia_empty_call__";
				const pending = record.toolCalls.get(callKey);
				const wireCallId = pending?.wire.toolCallId ?? sourceCallId;
				const blocks = result?.content ?? [];
				const isError = result?.isError === true;
				record.outputTail = record.outputTail.then(() => notify({
					sessionId: record.agent.session.id,
					update: {
						sessionUpdate: "tool_call_update",
						toolCallId: wireCallId,
						status: isError ? "failed" : "completed",
						content: acpContent(blocks),
						rawOutput: {
							content: blocks,
							isError,
							...(event.data.error === void 0 ? {} : { error: event.data.error })
						},
						_meta: acpMeta(pending?.name ?? `${POLYNOIA_MCP_PREFIX}unknown`)
					}
				})).finally(() => {
					if (record.toolCalls.get(callKey) === pending) record.toolCalls.delete(callKey);
				});
			}
			if (event.type === "assistant/message") {'''

_APPROVAL_ANCHOR = """\tctx.on("approval/request", (request, next) => {
\t\tconst record = ownedRecord(request.agent);
\t\tif (record === void 0 || request.callId === void 0) return next();
\t\treturn conn.requestPermission({
\t\t\tsessionId: record.agent.session.id,
\t\t\ttoolCall: { toolCallId: request.callId },"""

_APPROVAL_PATCH = r'''	ctx.on("approval/request", async (request, next) => {
		const record = ownedRecord(request.agent);
		if (record === void 0 || request.callId === void 0) return next();
		await record.outputTail;
		const pending = record.toolCalls.get(request.callId || "__polynoia_empty_call__");
		if (pending === void 0) return "unavailable";
		return conn.requestPermission({
			sessionId: record.agent.session.id,
			toolCall: pending.wire,
			_meta: {
				is_mcp_tool_approval: true,
				polynoia: { source: "mcp", provider: "deepseek-harness", server: "polynoia" }
			},'''

_NEW_SESSION_ANCHOR = """\t\t\tasync newSession(params) {
\t\t\t\tassertOpen();
\t\t\t\tvalidateSessionParams(params);
\t\t\t\tconst sessionId = SessionId(randomUUID());
\t\t\t\tconst handle = await agents.create({
\t\t\t\t\tsessionId,
\t\t\t\t\tmeta: { cwd: params.cwd },
\t\t\t\t\tagentOptions: agentOptions(config)
\t\t\t\t});"""

_NEW_SESSION_PATCH = r'''			async newSession(params) {
				assertOpen();
				if (sessions.size !== 0) throw invalidParams("this controlled bridge permits one ACP session per process");
				const mcpConfig = validateSessionParams(params);
				const sessionId = SessionId(randomUUID());
				const handle = await agents.create({
					sessionId,
					meta: { cwd: params.cwd },
					agentOptions: agentOptions(config)
				});
				let mcpScope;
				let disposePolicy;
				try {
					disposePolicy = handle.agent.ctx.on("tools/pre-execute", controlledToolPolicy);
					mcpScope = handle.agent.ctx.plugin(mcpClient, mcpConfig);
					await mcpScope;
				} catch (error) {
					disposePolicy?.();
					await mcpScope?.dispose();
					await handle.dispose();
					throw internalError(`Polynoia MCP setup failed: ${errorChain(error)}`);
				}'''

_DISPOSE_ANCHOR = """\t\t\t\tsessions.set(sessionId, {
\t\t\t\t\tagent: handle.agent,
\t\t\t\t\tdispose: () => handle.dispose(),
\t\t\t\t\toutputTail: Promise.resolve(),
\t\t\t\t\tinflight: void 0
\t\t\t\t});"""

_DISPOSE_PATCH = r'''				sessions.set(sessionId, {
					agent: handle.agent,
					dispose: async () => {
						disposePolicy?.();
						await mcpScope.dispose();
						await handle.dispose();
					},
					outputTail: Promise.resolve(),
					toolCalls: new Map(),
					toolSequence: 0,
					inflight: void 0
				});'''

_VALIDATION_ANCHOR = """function validateSessionParams(params) {
\tif (!isAbsolute(params.cwd)) throw invalidParams(`cwd must be an absolute path: ${params.cwd}`);
\tif (params.additionalDirectories !== void 0 && params.additionalDirectories.length > 0) throw invalidParams("additionalDirectories is not supported");
\tif (params.mcpServers.length > 0) throw invalidParams("mcpServers is not supported");
}"""

_VALIDATION_PATCH = r'''function validateSessionParams(params) {
	if (!isAbsolute(params.cwd)) throw invalidParams(`cwd must be an absolute path: ${params.cwd}`);
	if (params.additionalDirectories !== void 0 && params.additionalDirectories.length > 0) throw invalidParams("additionalDirectories is not supported");
	if (params.mcpServers.length !== 1) throw invalidParams("exactly one Polynoia MCP server is required");
	const server = params.mcpServers[0];
	if (server.name !== "polynoia" || "type" in server || !isAbsolute(server.command)) throw invalidParams("only an absolute-path stdio MCP server named polynoia is supported");
	if (server.args.length !== 2 || server.args[0] !== "-m" || server.args[1] !== "polynoia.mcp") throw invalidParams("the Polynoia MCP command must run -m polynoia.mcp");
	const allowedEnv = new Set([
		"POLYNOIA_CONV_ID", "POLYNOIA_AGENT_ID", "POLYNOIA_TURN_AGENT_ID",
		"POLYNOIA_AGENT_ROLE", "POLYNOIA_AGENT_TOOLS", "POLYNOIA_API_BASE",
		"POLYNOIA_SANDBOX_ROOT", "POLYNOIA_WORKSPACE_ID", "POLYNOIA_WORKTREE_ROOT",
		"POLYNOIA_WORKSPACE_ROOT", "PYTHONPATH"
	]);
	const env = {};
	for (const entry of server.env) {
		if (!allowedEnv.has(entry.name)) throw invalidParams(`unsupported Polynoia MCP environment variable: ${entry.name}`);
		if (Object.hasOwn(env, entry.name)) throw invalidParams(`duplicate Polynoia MCP environment variable: ${entry.name}`);
		env[entry.name] = entry.value;
	}
	for (const required of ["POLYNOIA_CONV_ID", "POLYNOIA_AGENT_ID", "POLYNOIA_AGENT_ROLE", "POLYNOIA_SANDBOX_ROOT", "PYTHONPATH"]) {
		if (typeof env[required] !== "string" || env[required].length === 0) throw invalidParams(`missing Polynoia MCP environment variable: ${required}`);
	}
	if (process.env.POLYNOIA_CONV_ID !== void 0 && env.POLYNOIA_CONV_ID !== process.env.POLYNOIA_CONV_ID) throw invalidParams("Polynoia MCP conversation identity does not match this ACP process");
	return {
		transport: "stdio",
		serverName: "polynoia",
		command: server.command,
		args: [...server.args],
		env,
		cwd: params.cwd,
		toolCallTimeoutMs: 30 * 60 * 1000,
		failOnStartupError: true,
		reconnect: { enabled: false }
	};
}'''

_PATCHES = (
    (_IMPORT_ANCHOR, _IMPORT_PATCH),
    (_OWNED_RECORD_ANCHOR, _OWNED_RECORD_PATCH),
    (_EVENT_ANCHOR, _EVENT_PATCH),
    (_APPROVAL_ANCHOR, _APPROVAL_PATCH),
    (_NEW_SESSION_ANCHOR, _NEW_SESSION_PATCH),
    (_DISPOSE_ANCHOR, _DISPOSE_PATCH),
    (_VALIDATION_ANCHOR, _VALIDATION_PATCH),
)

_PATCH_MARKER = "POLYNOIA_CONTROLLED_BRIDGE_VERSION = 2"


def patch_source(source: str) -> str:
    """Return a controlled-bridge transform of pinned rc.8 source."""

    if _PATCH_MARKER in source:
        return source
    patched = source
    for original, replacement in _PATCHES:
        count = patched.count(original)
        if count != 1:
            raise RuntimeError(
                "DeepSeek Harness ACP bridge patch no longer matches pinned rc.8 "
                f"source (anchor count={count}): {original[:90]!r}"
            )
        patched = patched.replace(original, replacement)
    return patched


def patch_installed_bridge(demo_root: Path) -> bool:
    """Patch the installed ``@deepseek-ai/dsh-acp`` and return if changed."""

    bridge = demo_root / "node_modules" / "@deepseek-ai" / "dsh-acp" / "lib" / "index.js"
    source = bridge.read_text(encoding="utf-8")
    patched = patch_source(source)
    if patched == source:
        return False
    bridge.write_text(patched, encoding="utf-8")
    return True
