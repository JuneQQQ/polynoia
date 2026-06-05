/** MobileHome — 微信/QQ 风格的移动端首页(4 tab:消息 / 联系人 / 项目 / 我)。
 *
 * 仅用于移动端单列布局的「首页」(App.tsx 移动分支,无 activeConv 时)。聊天页、
 * 桌面/web 端一律不经过这里 —— 这是独立的移动首页壳。
 *
 * 设计来源:移动端设计/home.jsx —— 配色用设计稿自带的暖调浅/深色板(LIGHT/DARK),
 * 不走全局 CSS 变量(那套在这页不好看)。主题跟随全局 dataset.theme;在「我」页
 * 切换会同步写全局,所以聊天页也跟着变深/浅。
 *
 * 数据全接真实后端:
 *   - 消息   → api.conversations()
 *   - 联系人 → store.agents(按适配器分组)+ 搜索 + 新建联系人(默认适配器预选)
 *   - 项目   → store.workspaces → 项目会话子页
 *   - 我     → 外观 / 语言 / 默认适配器(接入新建联系人创建流)
 */
import {
	Check,
	ChevronLeft,
	ChevronRight,
	Cpu,
	FolderTree,
	Globe,
	MessageSquare,
	Moon,
	Plus,
	Search,
	Sun,
	User,
	Users,
} from "lucide-react";
import {
	createContext,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react";
import {
	api,
	type ConversationSummary,
	type EnabledAdapter,
} from "../../lib/api";
import { type Lang, saveLang } from "../../lib/i18n";
import type { Agent, Workspace } from "../../lib/types";
import { useStore } from "../../store";
import { NewContactModal } from "../NewContactModal";

/* ── 设计稿调色板 ── */
const LIGHT = {
	bg: "#ECE6D7",
	bgTop: "#EFEADC",
	card: "#F4EFE3",
	ink: "#2B2722",
	ink2: "#6F685C",
	ink3: "#9A9384",
	line: "rgba(43,39,34,0.08)",
	accent: "#C2612F",
	accentSoft: "rgba(194,97,47,0.06)",
	chip: "rgba(43,39,34,0.06)",
	chipInk: "#857C6C",
	segBg: "rgba(43,39,34,0.06)",
	segOn: "#FBF7EE",
};
const DARK = {
	bg: "#1E1B17",
	bgTop: "#25211C",
	card: "#2A251F",
	ink: "#EDE7DA",
	ink2: "#B3AB9C",
	ink3: "#7E776A",
	line: "rgba(237,231,218,0.10)",
	accent: "#D8743C",
	accentSoft: "rgba(216,116,60,0.10)",
	chip: "rgba(237,231,218,0.07)",
	chipInk: "#B3AB9C",
	segBg: "rgba(237,231,218,0.07)",
	segOn: "#473E33",
};
type Pal = typeof LIGHT;
const ONLINE = "#7BC47F";

/* ── 文案 ── */
const STR = {
	zh: {
		brand: "Polynoia",
		searchConv: "搜索会话",
		tabChat: "消息",
		tabContacts: "联系人",
		tabFolder: "项目",
		tabMe: "我",
		contactsTitle: "联系人",
		searchAgent: "搜索联系人",
		newContact: "新建联系人",
		online: "在线",
		offline: "离线",
		projectsTitle: "项目",
		searchProject: "搜索项目",
		owner: "Owner",
		local: "本机",
		agents: (n: number) => `${n} 个智能体`,
		allProjects: (n: number) => `全部项目 · ${n}`,
		noConvs: "还没有会话 · 点联系人或项目开始",
		noProjectConvs: "该项目还没有对话",
		noResult: "没有匹配结果",
		members: (n: number) => `${n} 位成员`,
		me: "我",
		profileSub: "本机用户",
		secAdapter: "适配器",
		secGeneral: "通用",
		adapter: "默认适配器",
		adapterHint: "新建联系人默认使用的引擎",
		adapterEmpty: "暂无可用适配器 · 请在网页端添加",
		appearance: "外观",
		light: "浅色",
		dark: "深色",
		language: "语言",
	},
	en: {
		brand: "Polynoia",
		searchConv: "Search chats",
		tabChat: "Chats",
		tabContacts: "Agents",
		tabFolder: "Projects",
		tabMe: "Me",
		contactsTitle: "Agents",
		searchAgent: "Search agents",
		newContact: "New agent",
		online: "Online",
		offline: "Offline",
		projectsTitle: "Projects",
		searchProject: "Search projects",
		owner: "Owner",
		local: "Local",
		agents: (n: number) => `${n} ${n === 1 ? "agent" : "agents"}`,
		allProjects: (n: number) => `All projects · ${n}`,
		noConvs: "No chats yet · tap an agent or project to start",
		noProjectConvs: "No conversations in this project yet",
		noResult: "No matches",
		members: (n: number) => `${n} members`,
		me: "Me",
		profileSub: "Local user",
		secAdapter: "ADAPTER",
		secGeneral: "GENERAL",
		adapter: "Default adapter",
		adapterHint: "Engine used for new agents",
		adapterEmpty: "No adapters yet · add one on the web app",
		appearance: "Appearance",
		light: "Light",
		dark: "Dark",
		language: "Language",
	},
};

const ENGINES = ["OpenCode", "Claude Code", "Codex"] as const;
const ADAPTER_PREF_KEY = "polynoia-default-adapter";
/** 旧值(显示名)→ 后端 adapter id,用于迁移历史 localStorage。 */
const ADAPTER_ID: Record<string, string> = {
	"Claude Code": "claudeCode",
	Codex: "codex",
	OpenCode: "opencoder",
};
/** 后端 adapter id → 友好显示名。 */
const FRIENDLY: Record<string, string> = {
	claudeCode: "Claude Code",
	codex: "Codex",
	opencoder: "OpenCode",
};
function friendlyAdapter(id: string): string {
	return FRIENDLY[id] ?? id;
}
/** 把历史存的显示名归一化成 id(新值本身就是 id)。 */
function normalizeAdapterId(raw: string): string {
	return ADAPTER_ID[raw] ?? raw;
}

function engineOf(a: Agent): string {
	const id = (a.setup?.adapter_id ?? a.provider ?? "").toLowerCase();
	if (id.includes("claude")) return "Claude Code";
	if (id.includes("codex")) return "Codex";
	if (id.includes("opencod")) return "OpenCode";
	return "Claude Code";
}

/* ── context ── */
type AppCtx = {
	pal: Pal;
	t: (typeof STR)["zh"];
	lang: Lang;
	setLang: (l: Lang) => void;
	dark: boolean;
	setDark: (d: boolean) => void;
	/** Selected default adapter id (e.g. "claudeCode"). "" = none enabled. */
	adapter: string;
	setAdapter: (a: string) => void;
	/** Existing enabled adapters — mobile only SELECTS among these; adding a new
	 * adapter is a web/desktop flow. */
	adapters: EnabledAdapter[];
	onNewContact: () => void;
};
const Ctx = createContext<AppCtx | null>(null);
const useApp = () => useContext(Ctx) as AppCtx;

function getTheme(): "light" | "dark" {
	if (typeof document === "undefined") return "dark";
	return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}
function applyTheme(next: "light" | "dark") {
	document.documentElement.dataset.theme = next;
	try {
		window.localStorage.setItem("polynoia-theme", next);
	} catch {
		// ignore
	}
}

type Tab = "chat" | "contacts" | "folder" | "me";
type Props = {
	onSelectConv: (convId: string, members: string[], title: string) => void;
};

export function MobileHome({ onSelectConv }: Props) {
	const [tab, setTab] = useState<Tab>("chat");
	const [dark, setDarkState] = useState(() => getTheme() === "dark");
	const lang = useStore((s) => s.lang) as Lang;
	const setLangStore = useStore((s) => s.setLang);
	const [adapter, setAdapterState] = useState<string>(() => {
		try {
			return normalizeAdapterId(
				window.localStorage.getItem(ADAPTER_PREF_KEY) || "",
			);
		} catch {
			return "";
		}
	});
	const [adapters, setAdapters] = useState<EnabledAdapter[]>([]);
	const [newContactOpen, setNewContactOpen] = useState(false);

	// Load the existing enabled adapters (mobile only selects among these). Once
	// loaded, if the persisted default isn't among them, fall back to the first.
	useEffect(() => {
		api
			.listEnabledAdapters()
			.then((list) => {
				setAdapters(list);
				setAdapterState((cur) => {
					if (cur && list.some((a) => a.id === cur)) return cur;
					return list[0]?.id ?? "";
				});
			})
			.catch(() => setAdapters([]));
	}, []);

	const setDark = (d: boolean) => {
		setDarkState(d);
		applyTheme(d ? "dark" : "light");
	};
	const setLang = (l: Lang) => {
		setLangStore(l);
		saveLang(l);
	};
	const setAdapter = (a: string) => {
		setAdapterState(a);
		try {
			window.localStorage.setItem(ADAPTER_PREF_KEY, a);
		} catch {
			// ignore
		}
	};

	const pal = dark ? DARK : LIGHT;
	const ctx: AppCtx = {
		pal,
		t: STR[lang],
		lang,
		setLang,
		dark,
		setDark,
		adapter,
		setAdapter,
		adapters,
		onNewContact: () => setNewContactOpen(true),
	};

	return (
		<Ctx.Provider value={ctx}>
			<div
				style={{
					height: "100%",
					display: "flex",
					flexDirection: "column",
					background: pal.bg,
					overflow: "hidden",
				}}
			>
				<div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
					{tab === "chat" && <ChatListScreen onSelectConv={onSelectConv} />}
					{tab === "contacts" && <ContactsScreen onSelectConv={onSelectConv} />}
					{tab === "folder" && <ProjectsScreen onSelectConv={onSelectConv} />}
					{tab === "me" && <MeScreen />}
				</div>
				<TabBar tab={tab} setTab={setTab} />
			</div>
			{newContactOpen && (
				<NewContactModal
					prefill={{ adapter_id: adapter || undefined }}
					onOpenAdapterManager={() => {
						/* 适配器管理是桌面端流程;移动端默认引擎已接入,这里不再展开 */
					}}
					onClose={() => setNewContactOpen(false)}
					onCreated={async () => {
						try {
							const list = await api.agents();
							useStore.setState({ agents: list });
						} catch {
							// ignore
						}
					}}
				/>
			)}
		</Ctx.Provider>
	);
}

/* ─────────────────── 共用头部 ─────────────────── */

function LargeHeader({
	title,
	count,
	dot,
	showAdd,
}: {
	title: string;
	count?: number;
	dot?: boolean;
	showAdd?: boolean;
}) {
	const { pal, onNewContact } = useApp();
	return (
		<div
			style={{
				flexShrink: 0,
				background: pal.bgTop,
				borderBottom: `0.5px solid ${pal.line}`,
			}}
		>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					padding: "8px 16px 12px",
				}}
			>
				<span
					style={{
						fontFamily: 'Georgia, "Songti SC", serif',
						fontSize: 21,
						fontWeight: 600,
						color: pal.ink,
						letterSpacing: 0.2,
					}}
				>
					{title}
				</span>
				{dot && (
					<span
						style={{
							width: 6,
							height: 6,
							borderRadius: 99,
							background: pal.accent,
							alignSelf: "center",
							marginLeft: 8,
						}}
					/>
				)}
				{count != null && (
					<span
						style={{
							marginLeft: 8,
							fontSize: 12.5,
							color: pal.chipInk,
							background: pal.chip,
							padding: "1px 8px",
							borderRadius: 99,
							fontWeight: 600,
						}}
					>
						{count}
					</span>
				)}
				{showAdd && (
					<button
						type="button"
						onClick={onNewContact}
						style={{
							marginLeft: "auto",
							border: "none",
							background: "none",
							padding: 6,
							cursor: "pointer",
						}}
						aria-label="新建联系人"
					>
						<Plus size={23} color={pal.ink2} />
					</button>
				)}
			</div>
		</div>
	);
}

function SearchInput({
	value,
	onChange,
	placeholder,
}: {
	value: string;
	onChange: (v: string) => void;
	placeholder: string;
}) {
	const { pal } = useApp();
	return (
		<div style={{ padding: "12px 16px 6px" }}>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					gap: 8,
					height: 38,
					padding: "0 12px",
					background: pal.segBg,
					borderRadius: 12,
				}}
			>
				<Search size={17} color={pal.ink3} />
				<input
					value={value}
					onChange={(e) => onChange(e.target.value)}
					placeholder={placeholder}
					style={{
						flex: 1,
						minWidth: 0,
						border: "none",
						outline: "none",
						background: "transparent",
						fontSize: 15,
						color: pal.ink,
					}}
				/>
			</div>
		</div>
	);
}

function Avatar({
	initials,
	color,
	size = 52,
	radius = 16,
}: {
	initials: string;
	color: string;
	size?: number;
	radius?: number;
}) {
	return (
		<div
			style={{
				width: size,
				height: size,
				borderRadius: radius,
				background: color,
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				color: "#fff",
				fontSize: size * 0.34,
				fontWeight: 600,
				fontFamily: 'Georgia, "Songti SC", serif',
				flexShrink: 0,
			}}
		>
			{initials}
		</div>
	);
}

function Empty({ text }: { text: string }) {
	const { pal } = useApp();
	return (
		<div
			style={{
				padding: "64px 24px",
				textAlign: "center",
				fontSize: 13,
				color: pal.ink3,
			}}
		>
			{text}
		</div>
	);
}

const scrollStyle: React.CSSProperties = {
	flex: 1,
	overflowY: "auto",
	WebkitOverflowScrolling: "touch",
};

/* ─────────────────── 消息 ─────────────────── */

function ChatListScreen({ onSelectConv }: Props) {
	const { pal, t } = useApp();
	const agents = useStore((st) => st.agents);
	const [convs, setConvs] = useState<ConversationSummary[]>([]);
	const [q, setQ] = useState("");

	useEffect(() => {
		api
			.conversations()
			.then((list) =>
				setConvs(
					list
						.filter((c) => !c.archived)
						.sort((a, b) =>
							(b.last_message_at ?? b.created_at).localeCompare(
								a.last_message_at ?? a.created_at,
							),
						),
				),
			)
			.catch(() => setConvs([]));
	}, []);

	const agentFor = (c: ConversationSummary) =>
		agents.find((a) => a.id === c.members.find((m) => m !== "you"));
	const titleFor = (c: ConversationSummary) =>
		c.title || agentFor(c)?.name || "对话";

	const shown = useMemo(() => {
		const k = q.trim().toLowerCase();
		if (!k) return convs;
		return convs.filter((c) => titleFor(c).toLowerCase().includes(k));
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [convs, q, agents]);

	return (
		<>
			<LargeHeader title={t.brand} dot showAdd />
			<div style={scrollStyle}>
				<SearchInput value={q} onChange={setQ} placeholder={t.searchConv} />
				{shown.length === 0 && (
					<Empty text={q ? t.noResult : t.noConvs} />
				)}
				{shown.map((c, i) => {
					const a = agentFor(c);
					return (
						<div key={c.id}>
							<button
								type="button"
								onClick={() => onSelectConv(c.id, c.members, titleFor(c))}
								style={rowBtn}
							>
								<div style={{ position: "relative", flexShrink: 0 }}>
									<Avatar
										initials={a?.initials ?? titleFor(c).slice(0, 2)}
										color={a?.color ?? pal.accent}
									/>
									{a?.online && <OnlineDot pal={pal} />}
								</div>
								<div style={{ flex: 1, minWidth: 0 }}>
									<div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
										<span style={nameStyle(pal)}>{titleFor(c)}</span>
										{c.group && <EngineChip pal={pal} text="群" />}
										<span style={{ marginLeft: "auto", fontSize: 11.5, color: pal.ink3, whiteSpace: "nowrap" }}>
											{fmtTime(c.last_message_at)}
										</span>
									</div>
									<div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
										<span style={subStyle(pal)}>
											{a?.tagline ?? a?.role ?? t.members(c.members.length)}
										</span>
										{c.unread > 0 && <UnreadBadge pal={pal} n={c.unread} />}
									</div>
								</div>
							</button>
							{i < shown.length - 1 && <Divider pal={pal} indent={83} />}
						</div>
					);
				})}
				<div style={{ height: 12 }} />
			</div>
		</>
	);
}

/* ─────────────────── 联系人 ─────────────────── */

function ContactsScreen({ onSelectConv }: Props) {
	const { pal, t, onNewContact } = useApp();
	const agents = useStore((st) => st.agents);
	const [q, setQ] = useState("");

	const contacts = useMemo(
		() => agents.filter((a) => a.id !== "you" && a.id !== "system"),
		[agents],
	);
	const filtered = useMemo(() => {
		const k = q.trim().toLowerCase();
		if (!k) return contacts;
		return contacts.filter(
			(c) =>
				c.name.toLowerCase().includes(k) ||
				(c.setup?.model ?? "").toLowerCase().includes(k) ||
				(c.caps ?? []).some((cap) => cap.toLowerCase().includes(k)),
		);
	}, [contacts, q]);
	const groups = useMemo(
		() =>
			ENGINES.map((e) => ({
				engine: e,
				items: filtered.filter((c) => engineOf(c) === e),
			})).filter((g) => g.items.length),
		[filtered],
	);

	return (
		<>
			<LargeHeader title={t.contactsTitle} count={contacts.length} showAdd />
			<div style={scrollStyle}>
				<SearchInput value={q} onChange={setQ} placeholder={t.searchAgent} />
				<Divider pal={pal} indent={16} margin />
				<ActionRow icon={<Users size={22} color="#fff" strokeWidth={2} />} label={t.newContact} onClick={onNewContact} />
				{groups.length === 0 && <Empty text={t.noResult} />}
				{groups.map((g) => (
					<div key={g.engine}>
						<div
							style={{
								padding: "14px 16px 6px",
								fontSize: 12,
								fontWeight: 600,
								color: pal.ink3,
								letterSpacing: 0.4,
								display: "flex",
								alignItems: "center",
								gap: 7,
							}}
						>
							<Cpu size={14} color={pal.ink3} />
							<span>{g.engine}</span>
						</div>
						{g.items.map((c, i) => (
							<div key={c.id}>
								<button
									type="button"
									onClick={() => onSelectConv(`dm-${c.id}`, [c.id, "you"], c.name)}
									style={{ ...rowBtn, padding: "11px 16px" }}
								>
									<div style={{ position: "relative", flexShrink: 0 }}>
										<Avatar initials={c.initials} color={c.color} size={48} radius={15} />
										<OnlineDot pal={pal} color={c.online ? ONLINE : pal.ink3} />
									</div>
									<div style={{ flex: 1, minWidth: 0 }}>
										<div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
											<span style={{ fontSize: 16, fontWeight: 600, color: pal.ink }}>{c.name}</span>
											{c.setup?.model && (
												<span style={{ fontSize: 11, color: pal.ink3, fontFamily: "ui-monospace, Menlo, monospace" }}>
													{c.setup.model}
												</span>
											)}
											<span style={{ marginLeft: "auto", fontSize: 11.5, color: c.online ? "#5FA572" : pal.ink3 }}>
												{c.online ? t.online : t.offline}
											</span>
										</div>
										{c.caps && c.caps.length > 0 && (
											<div style={{ display: "flex", gap: 5, marginTop: 6, flexWrap: "wrap" }}>
												{c.caps.slice(0, 4).map((tag) => (
													<span
														key={tag}
														style={{
															fontSize: 11,
															color: pal.chipInk,
															background: pal.chip,
															padding: "2px 7px",
															borderRadius: 6,
														}}
													>
														{tag}
													</span>
												))}
											</div>
										)}
									</div>
								</button>
								{i < g.items.length - 1 && <Divider pal={pal} indent={77} />}
							</div>
						))}
					</div>
				))}
				<div style={{ height: 16 }} />
			</div>
		</>
	);
}

/* ─────────────────── 项目 ─────────────────── */

function ProjectsScreen({ onSelectConv }: Props) {
	const { pal, t } = useApp();
	const workspaces = useStore((st) => st.workspaces);
	const servers = useStore((st) => st.servers);
	const setActiveWorkspace = useStore((st) => st.setActiveWorkspace);
	const [opened, setOpened] = useState<Workspace | null>(null);
	const [q, setQ] = useState("");

	const shown = useMemo(() => {
		const k = q.trim().toLowerCase();
		if (!k) return workspaces;
		return workspaces.filter((w) => w.name.toLowerCase().includes(k));
	}, [workspaces, q]);

	if (opened) {
		return (
			<ProjectConvsScreen ws={opened} onBack={() => setOpened(null)} onSelectConv={onSelectConv} />
		);
	}

	return (
		<>
			<LargeHeader title={t.projectsTitle} count={workspaces.length} />
			<div style={scrollStyle}>
				<SearchInput value={q} onChange={setQ} placeholder={t.searchProject} />
				<Divider pal={pal} indent={16} margin />
				<div
					style={{
						padding: "14px 16px 6px",
						fontSize: 12,
						fontWeight: 600,
						color: pal.ink3,
						letterSpacing: 0.4,
					}}
				>
					{t.allProjects(workspaces.length)}
				</div>
				{shown.length === 0 && <Empty text={t.noResult} />}
				{shown.map((w, i) => {
					const srv = servers.find((sv) => sv.id === w.server_id);
					return (
						<div key={w.id}>
							<button
								type="button"
								onClick={() => {
									setActiveWorkspace(w.id);
									setOpened(w);
								}}
								style={{ ...rowBtn, padding: "12px 16px" }}
							>
								<div
									style={{
										width: 44,
										height: 44,
										borderRadius: 13,
										flexShrink: 0,
										background: `${w.color}22`,
										display: "flex",
										alignItems: "center",
										justifyContent: "center",
									}}
								>
									<div style={{ width: 18, height: 18, borderRadius: 6, background: w.color }} />
								</div>
								<div style={{ flex: 1, minWidth: 0 }}>
									<div style={{ fontSize: 16, fontWeight: 600, color: pal.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
										{w.name}
									</div>
									<div style={{ fontSize: 12.5, color: pal.ink3, marginTop: 2, display: "flex", alignItems: "center", gap: 6 }}>
										<span>{w.role ?? t.owner}</span>
										<Dot pal={pal} />
										<span>{srv?.name ?? t.local}</span>
										<Dot pal={pal} />
										<span>{t.agents(w.members?.length ?? 0)}</span>
									</div>
								</div>
								<ChevronRight size={17} color={pal.ink3} style={{ flexShrink: 0 }} />
							</button>
							{i < shown.length - 1 && <Divider pal={pal} indent={73} />}
						</div>
					);
				})}
				<div style={{ height: 16 }} />
			</div>
		</>
	);
}

function ProjectConvsScreen({
	ws,
	onBack,
	onSelectConv,
}: {
	ws: Workspace;
	onBack: () => void;
	onSelectConv: Props["onSelectConv"];
}) {
	const { pal, t } = useApp();
	const agents = useStore((st) => st.agents);
	const [convs, setConvs] = useState<ConversationSummary[]>([]);

	useEffect(() => {
		api
			.conversations({ workspaceId: ws.id })
			.then((list) => setConvs(list.filter((c) => !c.archived)))
			.catch(() => setConvs([]));
	}, [ws.id]);

	return (
		<>
			<div style={{ flexShrink: 0, background: pal.bgTop, borderBottom: `0.5px solid ${pal.line}` }}>
				<div style={{ display: "flex", alignItems: "center", padding: "8px 8px 10px" }}>
					<button
						type="button"
						onClick={onBack}
						style={{ border: "none", background: "none", padding: 6, cursor: "pointer", display: "flex" }}
						aria-label="返回项目列表"
					>
						<ChevronLeft size={22} color={pal.ink2} />
					</button>
					<span style={{ width: 8, height: 8, borderRadius: 99, background: ws.color, flexShrink: 0 }} />
					<span
						style={{
							marginLeft: 8,
							fontFamily: 'Georgia, "Songti SC", serif',
							fontSize: 18,
							fontWeight: 600,
							color: pal.ink,
							overflow: "hidden",
							textOverflow: "ellipsis",
							whiteSpace: "nowrap",
						}}
					>
						{ws.name}
					</span>
				</div>
			</div>
			<div style={scrollStyle}>
				{convs.length === 0 && <Empty text={t.noProjectConvs} />}
				{convs.map((c, i) => (
					<div key={c.id}>
						<button
							type="button"
							onClick={() => onSelectConv(c.id, c.members, c.title || "对话")}
							style={rowBtn}
						>
							<div
								style={{
									width: 44,
									height: 44,
									borderRadius: 13,
									flexShrink: 0,
									background: `${ws.color}22`,
									display: "flex",
									alignItems: "center",
									justifyContent: "center",
								}}
							>
								<MessageSquare size={18} color={ws.color} />
							</div>
							<div style={{ flex: 1, minWidth: 0 }}>
								<div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
									<span style={nameStyle(pal)}>{c.title || "对话"}</span>
									<span style={{ marginLeft: "auto", fontSize: 11.5, color: pal.ink3, whiteSpace: "nowrap" }}>
										{fmtTime(c.last_message_at)}
									</span>
								</div>
								<div style={{ ...subStyle(pal), marginTop: 3 }}>
									{c.members
										.filter((m) => m !== "you")
										.map((m) => agents.find((a) => a.id === m)?.name ?? m)
										.join("、") || "—"}
								</div>
							</div>
						</button>
						{i < convs.length - 1 && <Divider pal={pal} indent={73} />}
					</div>
				))}
				<div style={{ height: 16 }} />
			</div>
		</>
	);
}

/* ─────────────────── 我 ─────────────────── */

function MeScreen() {
	const { pal, t, lang, setLang, dark, setDark, adapter, setAdapter, adapters } =
		useApp();
	return (
		<>
			<div style={{ flexShrink: 0, background: pal.bgTop, borderBottom: `0.5px solid ${pal.line}` }}>
				<div style={{ padding: "8px 16px 14px" }}>
					<span style={{ fontSize: 18, fontWeight: 600, color: pal.ink }}>{t.me}</span>
				</div>
			</div>
			<div style={{ ...scrollStyle, paddingTop: 16 }}>
				<Card>
					<div style={{ display: "flex", alignItems: "center", gap: 15, padding: "18px 16px" }}>
						<Avatar initials="P" color="#4E7C8B" size={60} radius={18} />
						<div style={{ flex: 1, minWidth: 0 }}>
							<div style={{ fontSize: 20, fontWeight: 600, color: pal.ink }}>Polynoia</div>
						</div>
						<ChevronRight size={17} color={pal.ink3} />
					</div>
				</Card>

				<Card title={t.secAdapter}>
					<div style={{ padding: "13px 16px 6px" }}>
						<div style={{ fontSize: 15.5, color: pal.ink, fontWeight: 500 }}>{t.adapter}</div>
						<div style={{ fontSize: 12, color: pal.ink3, marginTop: 1 }}>{t.adapterHint}</div>
					</div>
					{adapters.length === 0 && (
						<div
							style={{
								padding: "10px 16px 14px",
								fontSize: 13,
								color: pal.ink3,
								borderTop: `0.5px solid ${pal.line}`,
							}}
						>
							{t.adapterEmpty}
						</div>
					)}
					{adapters.map((a) => {
						const on = a.id === adapter;
						return (
							<button
								key={a.id}
								type="button"
								onClick={() => setAdapter(a.id)}
								style={{
									width: "100%",
									textAlign: "left",
									display: "flex",
									alignItems: "center",
									gap: 12,
									padding: "12px 16px",
									cursor: "pointer",
									border: "none",
									borderTop: `0.5px solid ${pal.line}`,
									background: on ? pal.accentSoft : "transparent",
								}}
							>
								<div
									style={{
										width: 30,
										height: 30,
										borderRadius: 9,
										flexShrink: 0,
										background: on ? pal.accent : pal.chip,
										display: "flex",
										alignItems: "center",
										justifyContent: "center",
									}}
								>
									<Cpu size={18} color={on ? "#fff" : pal.ink2} />
								</div>
								<div style={{ flex: 1, minWidth: 0 }}>
									<div style={{ fontSize: 15, color: pal.ink, fontWeight: on ? 600 : 500 }}>
										{friendlyAdapter(a.id)}
									</div>
									{a.default_model && (
										<div style={{ fontSize: 12, color: pal.ink3, marginTop: 1, fontFamily: "ui-monospace, Menlo, monospace" }}>
											{a.default_model}
										</div>
									)}
								</div>
								{on && <Check size={20} color={pal.accent} />}
							</button>
						);
					})}
				</Card>

				<Card title={t.secGeneral}>
					<SettingRow
						icon={dark ? <Moon size={18} color={pal.ink2} /> : <Sun size={18} color={pal.ink2} />}
						label={t.appearance}
						control={
							<Segmented
								value={dark ? "dark" : "light"}
								onChange={(v) => setDark(v === "dark")}
								options={[
									{ value: "light", label: t.light, icon: "sun" },
									{ value: "dark", label: t.dark, icon: "moon" },
								]}
							/>
						}
					/>
					<SettingRow
						icon={<Globe size={18} color={pal.ink2} />}
						label={t.language}
						last
						control={
							<Segmented
								value={lang}
								onChange={(v) => setLang(v as Lang)}
								options={[
									{ value: "zh", label: "中文" },
									{ value: "en", label: "English" },
								]}
							/>
						}
					/>
				</Card>
				<div style={{ height: 16 }} />
			</div>
		</>
	);
}

function Card({ title, children }: { title?: string; children: React.ReactNode }) {
	const { pal } = useApp();
	return (
		<div style={{ margin: "0 12px 16px" }}>
			{title && (
				<div style={{ fontSize: 11.5, fontWeight: 600, color: pal.ink3, letterSpacing: 1, padding: "0 8px 7px" }}>
					{title}
				</div>
			)}
			<div style={{ background: pal.card, borderRadius: 16, overflow: "hidden", border: `0.5px solid ${pal.line}` }}>
				{children}
			</div>
		</div>
	);
}

function SettingRow({
	icon,
	label,
	control,
	last,
}: {
	icon: React.ReactNode;
	label: string;
	control: React.ReactNode;
	last?: boolean;
}) {
	const { pal } = useApp();
	return (
		<div style={{ padding: "0 16px", borderBottom: last ? "none" : `0.5px solid ${pal.line}` }}>
			<div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 0" }}>
				<div
					style={{
						width: 30,
						height: 30,
						borderRadius: 9,
						flexShrink: 0,
						background: pal.chip,
						display: "flex",
						alignItems: "center",
						justifyContent: "center",
					}}
				>
					{icon}
				</div>
				<div style={{ flex: 1, minWidth: 0 }}>
					<div style={{ fontSize: 15.5, color: pal.ink, fontWeight: 500 }}>{label}</div>
				</div>
				<div style={{ flexShrink: 0 }}>{control}</div>
			</div>
		</div>
	);
}

function Segmented<T extends string>({
	options,
	value,
	onChange,
}: {
	options: { value: T; label: string; icon?: "sun" | "moon" }[];
	value: T;
	onChange: (v: T) => void;
}) {
	const { pal } = useApp();
	return (
		<div style={{ display: "flex", gap: 3, background: pal.segBg, borderRadius: 10, padding: 3 }}>
			{options.map((o) => {
				const on = o.value === value;
				return (
					<button
						key={o.value}
						type="button"
						onClick={() => onChange(o.value)}
						style={{
							border: "none",
							cursor: "pointer",
							borderRadius: 8,
							padding: "6px 12px",
							display: "flex",
							alignItems: "center",
							gap: 5,
							background: on ? pal.segOn : "transparent",
							boxShadow: on ? "0 1px 2.5px rgba(0,0,0,0.18)" : "none",
							color: on ? pal.accent : pal.ink3,
							fontSize: 13,
							fontWeight: on ? 600 : 500,
						}}
					>
						{o.icon === "sun" && <Sun size={15} color={on ? pal.accent : pal.ink3} />}
						{o.icon === "moon" && <Moon size={15} color={on ? pal.accent : pal.ink3} />}
						<span>{o.label}</span>
					</button>
				);
			})}
		</div>
	);
}

/* ─────────────────── 底部 tab bar ─────────────────── */

function TabBar({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
	const { pal, t } = useApp();
	const tabs: { id: Tab; label: string; Icon: typeof MessageSquare }[] = [
		{ id: "chat", label: t.tabChat, Icon: MessageSquare },
		{ id: "contacts", label: t.tabContacts, Icon: Users },
		{ id: "folder", label: t.tabFolder, Icon: FolderTree },
		{ id: "me", label: t.tabMe, Icon: User },
	];
	return (
		<div
			style={{
				flexShrink: 0,
				display: "flex",
				borderTop: `0.5px solid ${pal.line}`,
				background: pal.bgTop,
				paddingTop: 8,
				paddingBottom: "calc(env(safe-area-inset-bottom) + 10px)",
			}}
		>
			{tabs.map(({ id, label, Icon }) => {
				const on = tab === id;
				const col = on ? pal.accent : pal.ink3;
				return (
					<button
						key={id}
						type="button"
						onClick={() => setTab(id)}
						style={{
							flex: 1,
							border: "none",
							background: "none",
							cursor: "pointer",
							display: "flex",
							flexDirection: "column",
							alignItems: "center",
							gap: 4,
							padding: 0,
						}}
					>
						<Icon size={25} color={col} strokeWidth={on ? 2 : 1.7} />
						<span style={{ fontSize: 10.5, color: col, fontWeight: on ? 600 : 500 }}>{label}</span>
					</button>
				);
			})}
		</div>
	);
}

/* ─────────────────── 小零件 ─────────────────── */

const rowBtn: React.CSSProperties = {
	width: "100%",
	textAlign: "left",
	display: "flex",
	alignItems: "center",
	gap: 13,
	padding: "13px 18px",
	cursor: "pointer",
	border: "none",
	background: "transparent",
};

function nameStyle(pal: Pal): React.CSSProperties {
	return {
		fontSize: 16.5,
		fontWeight: 600,
		color: pal.ink,
		overflow: "hidden",
		textOverflow: "ellipsis",
		whiteSpace: "nowrap",
	};
}
function subStyle(pal: Pal): React.CSSProperties {
	return {
		flex: 1,
		minWidth: 0,
		fontSize: 13.5,
		color: pal.ink2,
		overflow: "hidden",
		textOverflow: "ellipsis",
		whiteSpace: "nowrap",
	};
}

function OnlineDot({ pal, color = ONLINE }: { pal: Pal; color?: string }) {
	return (
		<div
			style={{
				position: "absolute",
				top: -2,
				right: -2,
				width: 13,
				height: 13,
				borderRadius: 99,
				background: color,
				border: `2.5px solid ${pal.bg}`,
			}}
		/>
	);
}
function UnreadBadge({ pal, n }: { pal: Pal; n: number }) {
	return (
		<span
			style={{
				flexShrink: 0,
				minWidth: 18,
				height: 18,
				padding: "0 5px",
				borderRadius: 99,
				background: pal.accent,
				color: "#fff",
				fontSize: 11.5,
				fontWeight: 600,
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
			}}
		>
			{n}
		</span>
	);
}
function EngineChip({ pal, text }: { pal: Pal; text: string }) {
	return (
		<span
			style={{
				fontSize: 10.5,
				color: pal.chipInk,
				fontFamily: "ui-monospace, Menlo, monospace",
				background: pal.chip,
				padding: "1px 6px",
				borderRadius: 5,
				whiteSpace: "nowrap",
			}}
		>
			{text}
		</span>
	);
}
function Divider({ pal, indent, margin }: { pal: Pal; indent: number; margin?: boolean }) {
	return (
		<div
			style={{
				height: 0.5,
				background: pal.line,
				marginLeft: indent,
				marginRight: margin ? 16 : 0,
				marginTop: margin ? 6 : 0,
			}}
		/>
	);
}
function Dot({ pal }: { pal: Pal }) {
	return <span style={{ width: 3, height: 3, borderRadius: 99, background: pal.ink3, display: "inline-block" }} />;
}
function ActionRow({
	icon,
	label,
	onClick,
}: {
	icon: React.ReactNode;
	label: string;
	onClick: () => void;
}) {
	const { pal } = useApp();
	return (
		<div style={{ padding: "0 16px" }}>
			<button
				type="button"
				onClick={onClick}
				style={{
					width: "100%",
					textAlign: "left",
					display: "flex",
					alignItems: "center",
					gap: 13,
					padding: "12px 0",
					border: "none",
					background: "transparent",
					cursor: "pointer",
				}}
			>
				<div
					style={{
						width: 44,
						height: 44,
						borderRadius: 13,
						flexShrink: 0,
						background: pal.accent,
						display: "flex",
						alignItems: "center",
						justifyContent: "center",
					}}
				>
					{icon}
				</div>
				<span style={{ fontSize: 16, fontWeight: 500, color: pal.ink, flex: 1 }}>{label}</span>
				<ChevronRight size={18} color={pal.ink3} />
			</button>
		</div>
	);
}

function fmtTime(iso: string | null): string {
	if (!iso) return "";
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return "";
	const now = new Date();
	if (d.toDateString() === now.toDateString())
		return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
	const yest = new Date(now);
	yest.setDate(now.getDate() - 1);
	if (d.toDateString() === yest.toDateString()) return "昨天";
	return d.toLocaleDateString([], { month: "numeric", day: "numeric" });
}
