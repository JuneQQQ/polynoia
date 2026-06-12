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
  projects: { zh: "协作项目", en: "Collaborations" },
  contacts: { zh: "联系人", en: "Contacts" },
  newProject: { zh: "新建项目", en: "New Project" },
  newContact: { zh: "新建联系人", en: "New Contact" },
  customContact: { zh: "自定义联系人", en: "Custom contact" },
  editContact: { zh: "编辑联系人", en: "Edit contact" },
  editProject: { zh: "编辑项目", en: "Edit project" },
  deleteProject: { zh: "删除项目", en: "Delete project" },
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
  /* Conversation ⋮ actions menu */
  convPin: { zh: "置顶", en: "Pin" },
  convUnpin: { zh: "取消置顶", en: "Unpin" },
  convRename: { zh: "重命名", en: "Rename" },
  convMembersRoles: { zh: "成员与角色", en: "Members & roles" },
  convArchive: { zh: "归档", en: "Archive" },
  convDelete: { zh: "删除会话", en: "Delete conversation" },
  convActionsLabel: { zh: "会话操作", en: "Conversation actions" },
  renameConvTitle: { zh: "重命名会话", en: "Rename conversation" },
  save: { zh: "保存", en: "Save" },
  saving: { zh: "保存中…", en: "Saving…" },
  cancel: { zh: "取消", en: "Cancel" },
  confirmDeleteConvTitle: { zh: "删除会话?", en: "Delete conversation?" },
  confirmDeleteConvBody: {
    zh: "「{title}」将被永久删除,该操作不可撤销。",
    en: "“{title}” will be permanently deleted. This cannot be undone.",
  },
  confirmArchiveConvTitle: { zh: "归档会话?", en: "Archive conversation?" },
  confirmArchiveConvBody: {
    zh: "「{title}」将移入归档,可随时在归档视图恢复。",
    en: "“{title}” moves to the archive; restore anytime from the archive view.",
  },
  delete: { zh: "删除", en: "Delete" },
  viewArchive: { zh: "查看归档", en: "View archive" },
  deleteContactAction: { zh: "删除联系人", en: "Delete contact" },
  confirmDeleteContactBody: {
    zh: "「{name}」将被删除,该操作不可撤销。历史会话不受影响。",
    en: "“{name}” will be deleted. This cannot be undone. Past conversations are kept.",
  },
  workspaceSettings: { zh: "工作区设置", en: "Workspace settings" },
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
