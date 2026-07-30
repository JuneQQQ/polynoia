import {
	Cloud,
	LoaderCircle,
	Search,
	ShieldAlert,
	ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import type { A2ADiscoveredAgent, Agent } from "../lib/types";
import { useStore } from "../store";

export type A2AImportBusy = "idle" | "discovering" | "installing";

type Props = {
	onInstalled: (agent: Agent) => void | Promise<void>;
	onCancel: () => void;
};

type InstallA2A = (body: {
	locator: string;
	expected_card_hash: string;
	bearer_env_var?: string;
}) => Promise<{ contact: Agent; existing: boolean }>;

export async function installApprovedA2A({
	locator,
	preview,
	bearerEnvVar,
	install = api.installA2A,
	onInstalled,
}: {
	locator: string;
	preview: A2ADiscoveredAgent;
	bearerEnvVar: string;
	install?: InstallA2A;
	onInstalled: (agent: Agent) => void | Promise<void>;
}): Promise<Agent> {
	const envName = bearerEnvVar.trim();
	const result = await install({
		locator,
		expected_card_hash: preview.card_hash,
		...(envName ? { bearer_env_var: envName } : {}),
	});
	await onInstalled(result.contact);
	return result.contact;
}

export type A2AImportPanelViewProps = {
	lang: Lang;
	locator: string;
	preview: A2ADiscoveredAgent | null;
	bearerEnvVar: string;
	busy: A2AImportBusy;
	error: string | null;
	onLocatorChange: (value: string) => void;
	onBearerEnvVarChange: (value: string) => void;
	onDiscover: () => void;
	onInstall: () => void;
	onCancel: () => void;
};

export function A2AImportPanelView({
	lang,
	locator,
	preview,
	bearerEnvVar,
	busy,
	error,
	onLocatorChange,
	onBearerEnvVarChange,
	onDiscover,
	onInstall,
	onCancel,
}: A2AImportPanelViewProps) {
	const isBusy = busy !== "idle";
	const needsBearer = preview?.auth_kind === "bearer";
	const unsupported = Boolean(
		preview && (!preview.installable || preview.auth_kind === "unsupported"),
	);
	const canInstall = Boolean(
		preview && !unsupported && !isBusy && (!needsBearer || bearerEnvVar.trim()),
	);
	const skills = preview?.card.skills ?? [];
	const inputModes = preview?.card.defaultInputModes ?? [];
	const outputModes = preview?.card.defaultOutputModes ?? [];

	return (
		<div className="space-y-5">
			<div className="rounded border border-[var(--color-line)] bg-[var(--color-surface-2)]/50 px-3.5 py-3 text-[11.5px] leading-relaxed text-[var(--color-fg-2)]">
				<div className="mb-1 flex items-center gap-1.5 font-medium text-[var(--color-fg)]">
					<Cloud size={13} />
					{t("a2aRemoteTitle", lang)}
				</div>
				{t("a2aLocatorHint", lang)}
			</div>

			<div>
				<label htmlFor="a2a-locator" className="section-eyebrow mb-2 block">
					{t("a2aLocator", lang)}
					<span className="ml-1 text-[var(--color-red)]">*</span>
				</label>
				<div className="flex gap-2">
					<input
						id="a2a-locator"
						type="url"
						value={locator}
						onChange={(event) => onLocatorChange(event.target.value)}
						placeholder="https://agent.example"
						disabled={isBusy}
						className="min-w-0 flex-1 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] px-3 py-2 font-mono text-[12.5px] text-[var(--color-fg)] outline-none placeholder:text-[var(--color-fg-3)] focus:border-[var(--color-accent)] disabled:opacity-60"
					/>
					<button
						type="button"
						onClick={onDiscover}
						disabled={!locator.trim() || isBusy}
						className="inline-flex items-center gap-1.5 rounded border border-[var(--color-accent)] px-3 py-2 text-[12px] text-[var(--color-accent)] disabled:opacity-50"
					>
						{busy === "discovering" ? (
							<LoaderCircle size={13} className="animate-spin" />
						) : (
							<Search size={13} />
						)}
						{busy === "discovering"
							? t("a2aDiscovering", lang)
							: t("a2aDiscover", lang)}
					</button>
				</div>
			</div>

			{error && (
				<div className="rounded border border-[var(--color-red)]/30 bg-[var(--color-red-soft)]/40 px-3 py-2 text-[11.5px] text-[var(--color-red)]">
					{error}
				</div>
			)}

			{preview && (
				<div className="space-y-4 rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] p-4">
					<div>
						<div className="flex flex-wrap items-start justify-between gap-2">
							<div>
								<h3 className="font-display text-[17px] font-medium text-[var(--color-fg)]">
									{preview.card.name}
								</h3>
								<div className="mt-0.5 font-mono text-[10.5px] text-[var(--color-fg-3)]">
									v{preview.card.version}
								</div>
							</div>
							<div className="rounded bg-[var(--color-surface-2)] px-2 py-1 font-mono text-[10.5px] text-[var(--color-fg-2)]">
								{preview.protocol_binding} · {preview.protocol_version}
							</div>
						</div>
						{preview.card.description && (
							<p className="mt-2 text-[12px] leading-relaxed text-[var(--color-fg-2)]">
								{preview.card.description}
							</p>
						)}
					</div>

					<div className="grid gap-3 text-[11.5px] sm:grid-cols-2">
						<Meta
							label={t("a2aInputModes", lang)}
							value={inputModes.join(", ") || "—"}
						/>
						<Meta
							label={t("a2aOutputModes", lang)}
							value={outputModes.join(", ") || "—"}
						/>
						<Meta
							label={t("a2aStreaming", lang)}
							value={
								preview.card.capabilities?.streaming
									? t("a2aStreamingYes", lang)
									: t("a2aStreamingNo", lang)
							}
						/>
						<Meta label="Endpoint" value={preview.endpoint_url} />
					</div>

					<div>
						<div className="section-eyebrow mb-2">{t("a2aSkills", lang)}</div>
						{skills.length > 0 ? (
							<div className="space-y-1.5">
								{skills.map((skill) => (
									<div
										key={skill.id}
										className="rounded bg-[var(--color-surface-2)] px-2.5 py-2"
									>
										<div className="text-[12px] font-medium text-[var(--color-fg)]">
											{skill.name}
										</div>
										{skill.description && (
											<div className="mt-0.5 text-[10.5px] text-[var(--color-fg-3)]">
												{skill.description}
											</div>
										)}
									</div>
								))}
							</div>
						) : (
							<div className="text-[11px] text-[var(--color-fg-3)]">
								{t("a2aNoSkills", lang)}
							</div>
						)}
					</div>

					{preview.signature_status === "signed_valid" ? (
						<div className="flex items-start gap-2 rounded bg-[#27AE60]/10 px-3 py-2 text-[11px] text-[#218c51]">
							<ShieldCheck size={14} className="mt-0.5 shrink-0" />
							{t("a2aSigned", lang)}
						</div>
					) : (
						<div className="flex items-start gap-2 rounded bg-[var(--color-amber)]/10 px-3 py-2 text-[11px] text-[var(--color-amber)]">
							<ShieldAlert size={14} className="mt-0.5 shrink-0" />
							{t("a2aUnsigned", lang)}
						</div>
					)}

					{needsBearer && (
						<div>
							<label
								htmlFor="a2a-bearer-env"
								className="section-eyebrow mb-2 block"
							>
								{t("a2aBearerEnv", lang)}
							</label>
							<input
								id="a2a-bearer-env"
								type="text"
								value={bearerEnvVar}
								onChange={(event) => onBearerEnvVarChange(event.target.value)}
								placeholder="REMOTE_AGENT_TOKEN"
								disabled={isBusy}
								autoComplete="off"
								className="w-full rounded border border-[var(--color-line-strong)] bg-[var(--color-bg)] px-3 py-2 font-mono text-[12.5px] text-[var(--color-fg)] outline-none placeholder:text-[var(--color-fg-3)] focus:border-[var(--color-accent)] disabled:opacity-60"
							/>
							<p className="mt-1.5 text-[10.5px] leading-relaxed text-[var(--color-fg-3)]">
								{t("a2aBearerEnvHint", lang)}
							</p>
						</div>
					)}

					{unsupported && (
						<div className="rounded border border-[var(--color-red)]/30 bg-[var(--color-red-soft)]/40 px-3 py-2 text-[11.5px] text-[var(--color-red)]">
							{t("a2aUnsupported", lang)}
							{preview.unsupported_auth_reason
								? `: ${preview.unsupported_auth_reason}`
								: ""}
						</div>
					)}
				</div>
			)}

			<div className="flex justify-end gap-3 border-t border-[var(--color-line)] pt-4">
				<button
					type="button"
					onClick={onCancel}
					disabled={isBusy}
					className="text-[13px] text-[var(--color-fg-3)] hover:text-[var(--color-fg)] disabled:opacity-50"
				>
					{t("cancel", lang)}
				</button>
				{preview && (
					<button
						type="button"
						onClick={onInstall}
						disabled={!canInstall}
						className="btn-primary"
					>
						{busy === "installing"
							? t("a2aInstalling", lang)
							: unsupported
								? t("a2aUnsupportedInstall", lang)
								: t("a2aInstall", lang)}
					</button>
				)}
			</div>
		</div>
	);
}

function Meta({ label, value }: { label: string; value: string }) {
	return (
		<div className="min-w-0">
			<div className="section-eyebrow mb-1">{label}</div>
			<div className="break-all font-mono text-[10.5px] text-[var(--color-fg-2)]">
				{value}
			</div>
		</div>
	);
}

export function A2AImportPanel({ onInstalled, onCancel }: Props) {
	const lang = useStore((state) => state.lang);
	const [locator, setLocator] = useState("");
	const [preview, setPreview] = useState<A2ADiscoveredAgent | null>(null);
	const [bearerEnvVar, setBearerEnvVar] = useState("");
	const [busy, setBusy] = useState<A2AImportBusy>("idle");
	const [error, setError] = useState<string | null>(null);

	const discover = async () => {
		const value = locator.trim();
		if (!value || busy !== "idle") return;
		setPreview(null);
		setBearerEnvVar("");
		setError(null);
		setBusy("discovering");
		try {
			const result = await api.discoverA2A(value);
			setPreview(result.agent);
		} catch (cause) {
			setError(cause instanceof Error ? cause.message : String(cause));
		} finally {
			setBusy("idle");
		}
	};

	const install = async () => {
		if (!preview || busy !== "idle") return;
		setError(null);
		setBusy("installing");
		try {
			await installApprovedA2A({
				locator: locator.trim(),
				preview,
				bearerEnvVar,
				onInstalled,
			});
		} catch (cause) {
			setError(cause instanceof Error ? cause.message : String(cause));
			setBusy("idle");
		}
	};

	return (
		<A2AImportPanelView
			lang={lang}
			locator={locator}
			preview={preview}
			bearerEnvVar={bearerEnvVar}
			busy={busy}
			error={error}
			onLocatorChange={setLocator}
			onBearerEnvVarChange={setBearerEnvVar}
			onDiscover={discover}
			onInstall={install}
			onCancel={onCancel}
		/>
	);
}
