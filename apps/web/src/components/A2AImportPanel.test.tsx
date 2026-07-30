import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { A2ADiscoveredAgent, Agent } from "../lib/types";

vi.mock("../store", () => ({
	useStore: (selector: (state: { lang: "zh"; agents: Agent[] }) => unknown) =>
		selector({ lang: "zh", agents: [] }),
}));

import {
	A2AImportPanel,
	A2AImportPanelView,
	installApprovedA2A,
} from "./A2AImportPanel";
import { NewContactModal } from "./NewContactModal";

const noop = () => {};

const preview: A2ADiscoveredAgent = {
	locator: "http://127.0.0.1:9999",
	card_url: "http://127.0.0.1:9999/.well-known/agent-card.json",
	endpoint_url: "http://127.0.0.1:9999/a2a",
	protocol_binding: "JSONRPC",
	protocol_version: "1.0",
	card: {
		name: "Cloud Reviewer",
		description: "Reviews architecture proposals",
		version: "2.3.0",
		skills: [
			{
				id: "architecture-review",
				name: "Architecture review",
				description: "Finds design risks",
				tags: ["architecture"],
			},
		],
		capabilities: { streaming: true },
		defaultInputModes: ["text/plain"],
		defaultOutputModes: ["application/json"],
	},
	card_hash: "sha256:abc",
	signature_status: "unsigned",
	installable: true,
	auth_kind: "none",
};

function renderView(
	overrides: Partial<React.ComponentProps<typeof A2AImportPanelView>> = {},
) {
	return renderToStaticMarkup(
		<A2AImportPanelView
			lang="zh"
			locator=""
			preview={null}
			bearerEnvVar=""
			busy="idle"
			error={null}
			onLocatorChange={noop}
			onBearerEnvVarChange={noop}
			onDiscover={noop}
			onInstall={noop}
			onCancel={noop}
			{...overrides}
		/>,
	);
}

describe("A2AImportPanel", () => {
	it("explains accepted locators and offers discovery in the empty state", () => {
		const html = renderToStaticMarkup(
			<A2AImportPanel onInstalled={noop} onCancel={noop} />,
		);

		expect(html).toContain("域名、基础 URL 或 Agent Card URL");
		expect(html).toContain("发现 Agent");
	});

	it("is available as a create-mode option in the contact modal", () => {
		const html = renderToStaticMarkup(
			<NewContactModal
				onClose={noop}
				onOpenAdapterManager={noop}
				onCreated={noop}
			/>,
		);

		expect(html).toContain("本地适配器");
		expect(html).toContain("Remote A2A");
	});

	it("disables actions while discovery is running", () => {
		const html = renderView({
			locator: "https://agent.example",
			busy: "discovering",
		});

		expect(html).toContain("正在发现");
		expect(html).toContain('disabled=""');
	});

	it("renders identity, skills, modes, streaming, protocol, and unsigned warning", () => {
		const html = renderView({
			locator: preview.locator,
			preview,
		});

		expect(html).toContain("Cloud Reviewer");
		expect(html).toContain("Reviews architecture proposals");
		expect(html).toContain("Architecture review");
		expect(html).toContain("Finds design risks");
		expect(html).toContain("text/plain");
		expect(html).toContain("application/json");
		expect(html).toContain("支持流式");
		expect(html).toContain("JSONRPC");
		expect(html).toContain("1.0");
		expect(html).toContain("未签名");
		expect(html).toContain("安装联系人");
	});

	it("asks bearer users for an environment-variable name, never a token value", () => {
		const html = renderView({
			locator: preview.locator,
			preview: { ...preview, auth_kind: "bearer" },
		});

		expect(html).toContain("Bearer Token 环境变量名");
		expect(html).toContain("REMOTE_AGENT_TOKEN");
		expect(html).toContain("服务端环境变量");
		expect(html).not.toContain("粘贴 Token");
		expect(html).not.toContain('type="password"');
		expect(html).toContain("不保存 Token 值");
	});

	it("shows unsupported auth reason and disables installation", () => {
		const html = renderView({
			locator: preview.locator,
			preview: {
				...preview,
				auth_kind: "unsupported",
				installable: false,
				unsupported_auth_reason: "OAuth device flow is unsupported",
			},
		});

		expect(html).toContain("OAuth device flow is unsupported");
		expect(html).toContain("暂不支持安装");
		expect(html).toContain('disabled=""');
	});

	it("renders the stable backend category and message", () => {
		const html = renderView({
			locator: "http://169.254.169.254",
			error: "unsafe_target: metadata addresses are blocked",
		});

		expect(html).toContain("unsafe_target");
		expect(html).toContain("metadata addresses are blocked");
	});

	it("passes the installed contact to the parent callback", async () => {
		const contact = {
			id: "remote-1",
			name: "Cloud Reviewer",
		} as Agent;
		const install = vi.fn().mockResolvedValue({ contact, existing: false });
		const onInstalled = vi.fn();

		await installApprovedA2A({
			locator: preview.locator,
			preview,
			bearerEnvVar: "",
			install,
			onInstalled,
		});

		expect(install).toHaveBeenCalledWith({
			locator: preview.locator,
			expected_card_hash: preview.card_hash,
		});
		expect(onInstalled).toHaveBeenCalledWith(contact);
	});
});
