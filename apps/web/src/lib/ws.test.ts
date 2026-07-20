import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConvWebSocket } from "./ws";

type SocketHook = (frame: string, socket: FakeWebSocket) => void;

class FakeWebSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: FakeWebSocket[] = [];

	readonly sent: string[] = [];
	readyState = FakeWebSocket.CONNECTING;
	onopen: ((event: Event) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	onmessage: ((event: MessageEvent<string>) => void) | null = null;
	onSend?: SocketHook;
	failNextSend = false;
	closeCalls = 0;
	private openListeners: Array<{ cb: () => void; once: boolean }> = [];

	constructor(readonly url: string) {
		FakeWebSocket.instances.push(this);
	}

	addEventListener(type: string, cb: () => void, options?: { once?: boolean }) {
		if (type === "open") {
			this.openListeners.push({ cb, once: options?.once === true });
		}
	}

	open() {
		this.readyState = FakeWebSocket.OPEN;
		this.onopen?.({ target: this } as unknown as Event);
		const listeners = [...this.openListeners];
		this.openListeners = this.openListeners.filter((entry) => !entry.once);
		for (const entry of listeners) entry.cb();
	}

	send(frame: string) {
		if (this.readyState !== FakeWebSocket.OPEN) {
			throw new Error("WebSocket is not open");
		}
		if (this.failNextSend) {
			this.failNextSend = false;
			throw new Error("synthetic send failure");
		}
		this.sent.push(frame);
		this.onSend?.(frame, this);
	}

	close() {
		this.closeCalls += 1;
		if (this.readyState === FakeWebSocket.CLOSED) return;
		this.readyState = FakeWebSocket.CLOSED;
		this.onclose?.({ target: this } as unknown as CloseEvent);
	}

	drop() {
		this.readyState = FakeWebSocket.CLOSED;
		this.onclose?.({ target: this } as unknown as CloseEvent);
	}

	receive(frame: string) {
		this.onmessage?.({
			data: frame,
			target: this,
		} as unknown as MessageEvent<string>);
	}
}

const memoryStorage = new Map<string, string>();

beforeEach(() => {
	FakeWebSocket.instances = [];
	memoryStorage.clear();
	(globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
	(globalThis as { window?: unknown }).window = {
		location: {
			search: "",
			protocol: "http:",
			host: "example.test",
			hostname: "example.test",
		},
		localStorage: {
			getItem: (key: string) => memoryStorage.get(key) ?? null,
			setItem: (key: string, value: string) => memoryStorage.set(key, value),
			removeItem: (key: string) => memoryStorage.delete(key),
		},
		matchMedia: () => ({ matches: false }),
		navigator: { userAgent: "vitest" },
	};
});

afterEach(() => {
	(globalThis as { WebSocket?: unknown }).WebSocket = undefined;
	(globalThis as { window?: unknown }).window = undefined;
});

function socketAt(index: number): FakeWebSocket {
	const socket = FakeWebSocket.instances[index];
	if (!socket) throw new Error(`missing fake socket ${index}`);
	return socket;
}

function parsedFrames(socket: FakeWebSocket): Array<Record<string, unknown>> {
	return socket.sent.map(
		(frame) => JSON.parse(frame) as Record<string, unknown>,
	);
}

function userIds(socket: FakeWebSocket): string[] {
	return parsedFrames(socket)
		.filter((frame) => frame.kind === "user_message")
		.map((frame) => String(frame.msg_id ?? ""));
}

function kinds(socket: FakeWebSocket): unknown[] {
	return parsedFrames(socket).map((frame) => frame.kind);
}

function receipt(
	type: "data-user-message-ack" | "data-user-message-nack",
	id: unknown,
	data: unknown,
): string {
	return `data: ${JSON.stringify({ type, id, data })}\n\n`;
}

function ack(id: string, duplicate = false): string {
	return receipt("data-user-message-ack", id, { duplicate });
}

function nack(id: string, reason: string, retryable: boolean): string {
	return receipt("data-user-message-nack", id, { reason, retryable });
}

async function replaceSocket(client: ConvWebSocket, oldSocket: FakeWebSocket) {
	oldSocket.drop();
	const reconnecting = client.reconnect();
	const replacement = socketAt(FakeWebSocket.instances.length - 1);
	replacement.open();
	await reconnecting;
	return replacement;
}

describe("ConvWebSocket delivery outbox", () => {
	it("queues disconnected and CONNECTING sends, then flushes FIFO before one status query", async () => {
		const client = new ConvWebSocket("conv-1");
		const first = client.sendUserMessage("one", ["a"], undefined, "m1");
		const connecting = client.connect();
		const socket = socketAt(0);
		const second = client.sendUserMessage("two", ["a"], undefined, "m2");

		expect(socket.sent).toEqual([]);
		socket.open();
		await connecting;

		expect(first).toBeInstanceOf(Promise);
		expect(second).toBeInstanceOf(Promise);
		expect(kinds(socket)).toEqual([
			"user_message",
			"user_message",
			"agent_status_query",
		]);
		expect(userIds(socket)).toEqual(["m1", "m2"]);
	});

	it("sends each pending message at most once on a physical socket", async () => {
		const client = new ConvWebSocket("conv-1");
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;

		const first = client.sendUserMessage("one", ["a"], undefined, "m1");
		const duplicate = client.sendUserMessage("one", ["a"], undefined, "m1");
		client.sendUserMessage("two", ["a"], undefined, "m2");

		expect(duplicate).toBe(first);
		expect(userIds(socket)).toEqual(["m1", "m2"]);
	});

	it("throws when a pending message id is reused for a different frame", async () => {
		const client = new ConvWebSocket("conv-1");
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;
		client.sendUserMessage("one", ["a"], undefined, "m1");

		expect(() =>
			client.sendUserMessage("changed", ["a"], undefined, "m1"),
		).toThrow(/m1/);
		expect(userIds(socket)).toEqual(["m1"]);
	});

	it("replays only unacknowledged messages in original FIFO order", async () => {
		const client = new ConvWebSocket("conv-1");
		const connecting = client.connect();
		const firstSocket = socketAt(0);
		firstSocket.open();
		await connecting;
		const p1 = client.sendUserMessage("one", ["a"], undefined, "m1");
		const p2 = client.sendUserMessage("two", ["a"], undefined, "m2");
		const p3 = client.sendUserMessage("three", ["a"], undefined, "m3");

		firstSocket.receive(ack("m2", true));
		expect(await p2).toEqual({ id: "m2", ok: true, duplicate: true });

		const secondSocket = await replaceSocket(client, firstSocket);
		expect(kinds(secondSocket)).toEqual([
			"user_message",
			"user_message",
			"agent_status_query",
		]);
		expect(userIds(secondSocket)).toEqual(["m1", "m3"]);
		expect(p1).toBeInstanceOf(Promise);
		expect(p3).toBeInstanceOf(Promise);
	});

	it("retains a pending frame when WebSocket.send throws", async () => {
		const client = new ConvWebSocket("conv-1");
		const closed = vi.fn();
		client.onClose(closed);
		const connecting = client.connect();
		const firstSocket = socketAt(0);
		firstSocket.open();
		await connecting;
		firstSocket.failNextSend = true;

		let pending: ReturnType<ConvWebSocket["sendUserMessage"]> = undefined;
		expect(() => {
			pending = client.sendUserMessage("one", ["a"], undefined, "m1");
		}).not.toThrow();
		expect(pending).toBeInstanceOf(Promise);
		expect(userIds(firstSocket)).toEqual([]);
		expect(firstSocket.closeCalls).toBe(1);
		expect(closed).toHaveBeenCalledOnce();
		expect(client.isDisconnected()).toBe(true);

		const reconnecting = client.reconnect();
		const secondSocket = socketAt(1);
		secondSocket.open();
		await reconnecting;
		expect(userIds(secondSocket)).toEqual(["m1"]);
	});

	it("does not skip later entries when send receives a synchronous ACK", async () => {
		const client = new ConvWebSocket("conv-1");
		const p1 = client.sendUserMessage("one", ["a"], undefined, "m1");
		const p2 = client.sendUserMessage("two", ["a"], undefined, "m2");
		const p3 = client.sendUserMessage("three", ["a"], undefined, "m3");
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.onSend = (frame, current) => {
			const parsed = JSON.parse(frame) as Record<string, unknown>;
			if (parsed.kind === "user_message")
				current.receive(ack(String(parsed.msg_id)));
		};

		socket.open();
		await connecting;

		expect(userIds(socket)).toEqual(["m1", "m2", "m3"]);
		expect(await Promise.all([p1, p2, p3])).toEqual([
			{ id: "m1", ok: true, duplicate: false },
			{ id: "m2", ok: true, duplicate: false },
			{ id: "m3", ok: true, duplicate: false },
		]);
	});

	it("settles and consumes terminal NACKs without adding timeline chunks", async () => {
		const client = new ConvWebSocket("conv-1");
		const chunks = vi.fn();
		client.onChunk(chunks);
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;
		const delivery = client.sendUserMessage("one", ["a"], undefined, "m1");

		socket.receive(nack("m1", "message_id_conflict", false));

		expect(await delivery).toEqual({
			id: "m1",
			ok: false,
			reason: "message_id_conflict",
			retryable: false,
		});
		expect(chunks).not.toHaveBeenCalled();
		const replacement = await replaceSocket(client, socket);
		expect(userIds(replacement)).toEqual([]);
	});

	it("keeps malformed and unknown receipts from deleting pending entries", async () => {
		const client = new ConvWebSocket("conv-1");
		const chunks = vi.fn();
		client.onChunk(chunks);
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;
		const delivery = client.sendUserMessage("one", ["a"], undefined, "m1");

		socket.receive(ack("unknown"));
		socket.receive(receipt("data-user-message-ack", "m1", {}));
		socket.receive(
			receipt("data-user-message-nack", "m1", {
				reason: "persistence_error",
				retryable: "yes",
			}),
		);
		socket.receive(receipt("data-user-message-nack", 17, {}));

		expect(chunks).not.toHaveBeenCalled();
		const replacement = await replaceSocket(client, socket);
		expect(userIds(replacement)).toEqual(["m1"]);
		replacement.receive(ack("m1"));
		expect(await delivery).toEqual({ id: "m1", ok: true, duplicate: false });
	});

	it("retains retryable NACKs, closes their current socket, and replays", async () => {
		const client = new ConvWebSocket("conv-1");
		const chunks = vi.fn();
		const closed = vi.fn();
		client.onChunk(chunks);
		client.onClose(closed);
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;
		const delivery = client.sendUserMessage("one", ["a"], undefined, "m1");
		let settled = false;
		void delivery?.then(() => {
			settled = true;
		});

		socket.receive(nack("m1", "persistence_error", true));
		await Promise.resolve();

		expect(settled).toBe(false);
		expect(socket.closeCalls).toBe(1);
		expect(closed).toHaveBeenCalledOnce();
		expect(chunks).not.toHaveBeenCalled();
		const reconnecting = client.reconnect();
		const replacement = socketAt(1);
		replacement.open();
		await reconnecting;
		expect(userIds(replacement)).toEqual(["m1"]);
		replacement.receive(ack("m1"));
		expect(await delivery).toEqual({ id: "m1", ok: true, duplicate: false });
	});

	it("handles a synchronous retryable NACK during open without sending status on the closed socket", async () => {
		const client = new ConvWebSocket("conv-1");
		const closed = vi.fn();
		client.onClose(closed);
		const delivery = client.sendUserMessage("one", ["a"], undefined, "m1");
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.onSend = (frame, current) => {
			const parsed = JSON.parse(frame) as Record<string, unknown>;
			if (parsed.kind === "user_message") {
				current.receive(nack("m1", "persistence_error", true));
			}
		};

		expect(() => socket.open()).not.toThrow();
		await connecting;
		expect(socket.closeCalls).toBe(1);
		expect(closed).toHaveBeenCalledOnce();
		expect(kinds(socket)).toEqual(["user_message"]);

		const reconnecting = client.reconnect();
		const replacement = socketAt(1);
		replacement.open();
		await reconnecting;
		expect(userIds(replacement)).toEqual(["m1"]);
		replacement.receive(ack("m1"));
		expect(await delivery).toEqual({ id: "m1", ok: true, duplicate: false });
	});

	it("lets stale ACKs settle but ignores stale retryable NACK side effects", async () => {
		const client = new ConvWebSocket("conv-1");
		const connecting = client.connect();
		const staleSocket = socketAt(0);
		staleSocket.open();
		await connecting;
		const first = client.sendUserMessage("one", ["a"], undefined, "m1");
		const second = client.sendUserMessage("two", ["a"], undefined, "m2");
		const currentSocket = await replaceSocket(client, staleSocket);
		expect(userIds(currentSocket)).toEqual(["m1", "m2"]);

		staleSocket.receive(ack("m1", true));
		expect(await first).toEqual({ id: "m1", ok: true, duplicate: true });
		staleSocket.receive(nack("m2", "persistence_error", true));
		client.sendUserMessage("three", ["a"], undefined, "m3");

		expect(currentSocket.closeCalls).toBe(0);
		expect(currentSocket.readyState).toBe(FakeWebSocket.OPEN);
		expect(userIds(currentSocket)).toEqual(["m1", "m2", "m3"]);
		currentSocket.receive(ack("m2"));
		expect(await second).toEqual({ id: "m2", ok: true, duplicate: false });
	});

	it("parses batched and partial SSE receipts while forwarding ordinary chunks", async () => {
		const client = new ConvWebSocket("conv-1");
		const chunks = vi.fn();
		client.onChunk(chunks);
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;
		const first = client.sendUserMessage("one", ["a"], undefined, "m1");
		const second = client.sendUserMessage("two", ["a"], undefined, "m2");
		const normal = `data: ${JSON.stringify({ type: "text-delta", id: "t1", delta: "hi" })}\n\n`;
		const secondAck = ack("m2", true);
		const splitAt = Math.floor(secondAck.length / 2);

		socket.receive(ack("m1") + normal + secondAck.slice(0, splitAt));
		let secondResult: unknown;
		void second?.then((result) => {
			secondResult = result;
		});
		await Promise.resolve();
		expect(await first).toEqual({ id: "m1", ok: true, duplicate: false });
		expect(secondResult).toBeUndefined();
		expect(chunks).toHaveBeenCalledOnce();
		expect(chunks).toHaveBeenCalledWith({
			type: "text-delta",
			id: "t1",
			delta: "hi",
		});

		socket.receive(secondAck.slice(splitAt));
		expect(await second).toEqual({ id: "m2", ok: true, duplicate: true });
	});

	it("keeps partial frame buffers separate for stale physical sockets", async () => {
		const client = new ConvWebSocket("conv-1");
		const connecting = client.connect();
		const staleSocket = socketAt(0);
		staleSocket.open();
		await connecting;
		const delivery = client.sendUserMessage("one", ["a"], undefined, "m1");
		const staleAck = ack("m1");
		const splitAt = Math.floor(staleAck.length / 2);
		staleSocket.receive(staleAck.slice(0, splitAt));

		const currentSocket = await replaceSocket(client, staleSocket);
		expect(userIds(currentSocket)).toEqual(["m1"]);
		staleSocket.receive(staleAck.slice(splitAt));
		let result: unknown;
		void delivery?.then((value) => {
			result = value;
		});
		await Promise.resolve();

		expect(result).toEqual({ id: "m1", ok: true, duplicate: false });
		expect(currentSocket.closeCalls).toBe(0);
	});

	it("keeps no-id and regeneration sends outside the outbox", async () => {
		const client = new ConvWebSocket("conv-1");
		expect(client.sendUserMessage("offline", ["a"])).toBeUndefined();
		const connecting = client.connect();
		const socket = socketAt(0);
		socket.open();
		await connecting;

		expect(client.sendUserMessage("ordinary", ["a"])).toBeUndefined();
		expect(
			client.sendUserMessage("regenerate", ["a"], undefined, "regen-id", {
				regenerate: true,
			}),
		).toBeUndefined();
		expect(userIds(socket)).toEqual(["", "regen-id"]);

		const replacement = await replaceSocket(client, socket);
		expect(userIds(replacement)).toEqual([]);
		expect(kinds(replacement)).toEqual(["agent_status_query"]);
	});
});
