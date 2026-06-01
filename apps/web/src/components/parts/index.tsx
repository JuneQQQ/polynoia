/** MessagePart 注册表 — borrowed pattern from assistant-ui.
 *
 * 一条 message 的 payload 经此 registry 分派到对应 React 组件。
 * 加新 card type 的 5 步流程见 `.skills/add-card-type.md`(P0 后建)。
 */
import type { ComponentType } from "react";
import type { MessagePayload } from "../../lib/types";
import { ApiPart } from "./ApiPart";
import { AskFormPart } from "./AskFormPart";
import { ConflictPart } from "./ConflictPart";
import { CopyPart } from "./CopyPart";
import { DiffPart } from "./DiffPart";
import { ErrorPart } from "./ErrorPart";
import { FilePart } from "./FilePart";
import { ImagePart } from "./ImagePart";
import { LogsPart } from "./LogsPart";
import { MetricsPart } from "./MetricsPart";
import { ReasoningPart } from "./ReasoningPart";
import { SchemaPart } from "./SchemaPart";
import { SqlPart } from "./SqlPart";
import { SwatchesPart } from "./SwatchesPart";
import { TasksPart } from "./TasksPart";
import { TextPart } from "./TextPart";
import { ToolCallPart } from "./ToolCallPart";
import { TypingPart } from "./TypingPart";
import { WebPart } from "./WebPart";

type PartProps<K extends MessagePayload["kind"]> = {
	payload: Extract<MessagePayload, { kind: K }>;
	/** Optional: parts can branch on streaming state (e.g. TextPart skips
	 * markdown rendering while the stream is still actively appending). */
	isStreaming?: boolean;
	/** Optional message context — used by AskFormPart to locate its own reply
	 * in the conversation (answered-state + picked option survive refresh). */
	convId?: string;
	msgId?: string;
};
type AnyPartComponent = ComponentType<{
	payload: MessagePayload;
	isStreaming?: boolean;
	convId?: string;
	msgId?: string;
}>;

// 完整 12 + ask-form schema preserved (P0 不主动发出但渲染完整)
export const PARTS_REGISTRY: Partial<{
	[K in MessagePayload["kind"]]: ComponentType<PartProps<K>>;
}> = {
	text: TextPart,
	reasoning: ReasoningPart,
	tasks: TasksPart,
	diff: DiffPart,
	web: WebPart,
	swatches: SwatchesPart,
	copy: CopyPart,
	metrics: MetricsPart,
	sql: SqlPart,
	schema: SchemaPart,
	logs: LogsPart,
	api: ApiPart,
	typing: TypingPart,
	"tool-call": ToolCallPart,
	"ask-form": AskFormPart,
	image: ImagePart,
	file: FilePart,
	error: ErrorPart,
	conflict: ConflictPart,
};

export function MessagePart({
	payload,
	isStreaming,
	convId,
	msgId,
}: {
	payload: MessagePayload;
	isStreaming?: boolean;
	convId?: string;
	msgId?: string;
}) {
	const Component = PARTS_REGISTRY[payload.kind] as
		| AnyPartComponent
		| undefined;
	if (!Component) {
		return (
			<div className="px-3 py-2 text-[11px] text-[var(--color-fg-4)] bg-[var(--color-surface-2)] rounded border border-dashed border-[var(--color-line)] mono">
				[未注册的 part:{payload.kind}]
			</div>
		);
	}
	return (
		<Component
			payload={
				payload as Extract<MessagePayload, { kind: typeof payload.kind }>
			}
			isStreaming={isStreaming}
			convId={convId}
			msgId={msgId}
		/>
	);
}
