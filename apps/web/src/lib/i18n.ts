/** Minimal i18n — zh / en switch via store + localStorage persistence. */
export type Lang = "zh" | "en";

const STR = {
  newAgent: { zh: "新建联系人", en: "New Contact" },
  manageAdapters: { zh: "管理适配器", en: "Manage Adapters" },
  adapters: { zh: "适配器", en: "adapters" },
  /** "N adapters connected" / "N 个适配器已接入" — count goes in front via interpolation */
  adaptersConnectedSuffix: { zh: "个适配器已接入", en: "adapters connected" },
  noAdaptersShort: { zh: "未接入适配器", en: "No adapters" },
  searchSession: { zh: "搜索会话", en: "Search" },
  projects: { zh: "项目", en: "Projects" },
  contacts: { zh: "联系人", en: "Contacts" },
  newProject: { zh: "新建项目", en: "New Project" },
  newContact: { zh: "新建联系人", en: "New Contact" },
  customContact: { zh: "自定义联系人", en: "Custom contact" },
  editContact: { zh: "编辑人格", en: "Edit persona" },
  noProjectsHint: {
    zh: "还没有项目 · + 新建第一个",
    en: "No projects yet · + Create first",
  },
  noContactsHint: {
    zh: "还没有联系人 · + 新建联系人",
    en: "No contacts yet · + New Contact",
  },
  noAdaptersHint: {
    zh: "还没有接入任何适配器 · 先接入 Claude Code / Codex / OpenCode",
    en: "No adapters connected yet · onboard Claude Code / Codex / OpenCode first",
  },
  /* First-run guide card */
  firstRunStep: { zh: "第一步", en: "Step 01" },
  firstRunTitle: { zh: "接入适配器", en: "Connect Adapters" },
  firstRunBody: {
    zh: "连接你已经登录过的 Claude Code / Codex / OpenCode,Polynoia 自动复用主机凭证。",
    en: "Connect your already-logged-in Claude Code / Codex / OpenCode CLI. Polynoia reuses host credentials automatically.",
  },
  firstRunCta: { zh: "立刻接入", en: "Connect now" },
  /* Step 2 guide — visible after first adapter enabled but zero custom contacts */
  secondRunStep: { zh: "第二步", en: "Step 02" },
  secondRunTitle: { zh: "新建第一个联系人", en: "Create First Contact" },
  secondRunBody: {
    zh: "适配器已就绪。基于它创建一个或多个联系人(不同模型 / 角色 / 颜色),开始对话。",
    en: "Adapter ready. Create one or more contacts on top of it (different model / role / color) to start chatting.",
  },
  secondRunCta: { zh: "立刻新建", en: "Create now" },
  /* Empty state CTAs */
  newFirstContact: { zh: "新建第一个联系人", en: "Create your first contact" },
  newFirstProject: { zh: "新建第一个项目", en: "Create your first project" },
  offline: { zh: "在线", en: "Online" },
  offlineHint: { zh: "离线 · 重新接入", en: "Offline · re-onboard" },
  agent: { zh: "Agent", en: "Agent" },
  online: { zh: "在线", en: "Online" },
  offlineStatus: { zh: "离线 · CLI 未安装或未登录", en: "Offline · CLI missing or not logged in" },
} as const;

export type TKey = keyof typeof STR;

export function t(key: TKey, lang: Lang): string {
  return STR[key][lang];
}

const STORAGE_KEY = "polynoia.lang";

export function loadLang(): Lang {
  if (typeof window === "undefined") return "zh";
  const v = window.localStorage.getItem(STORAGE_KEY);
  return v === "en" ? "en" : "zh";
}

export function saveLang(lang: Lang) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, lang);
}
