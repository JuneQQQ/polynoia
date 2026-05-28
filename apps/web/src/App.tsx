import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { ChatPane } from "./components/ChatPane";
import { ChatSearchOverlay } from "./components/ChatSearchOverlay";
import { PreviewPane } from "./components/preview/PreviewPane";
import { RightDrawer } from "./components/RightDrawer";
import { Sidebar } from "./components/Sidebar";
import { ArchiveView } from "./components/views/ArchiveView";
import { CreateHubView } from "./components/views/CreateHubView";
import { InboxView } from "./components/views/InboxView";
import { api } from "./lib/api";
import { isMobile } from "./lib/platform";
import { useStore } from "./store";

export function App() {
  const setSeed = useStore((s) => s.setSeed);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const activeWorkspaceId = useStore((s) => s.activeWorkspaceId);
  const workspaces = useStore((s) => s.workspaces);
  const previewOpen = useStore((s) => s.preview.open);
  const [activeConv, setActiveConv] = useState<{
    id: string;
    members: string[];
    title: string;
  } | null>(null);
  // Mobile: sidebar is a drawer, hidden by default. Desktop/browser: sidebar
  // is a permanent left column.
  const mobile = isMobile();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    Promise.all([api.providers(), api.agents(), api.servers(), api.workspaces()])
      .then(([providers, agents, servers, workspaces]) =>
        setSeed({ providers, agents, servers, workspaces }),
      )
      .catch((e) => console.error("seed fetch failed", e));
  }, [setSeed]);

  // 进 workspace 自动选默认 conv
  useEffect(() => {
    if (activeWorkspaceId) {
      const ws = workspaces.find((w) => w.id === activeWorkspaceId);
      if (ws) {
        setActiveConv({
          id: `conv-${ws.id}`,
          members: ws.members ?? [],
          title: `${ws.name} · 主对话`,
        });
        setView("chat");
      }
    }
  }, [activeWorkspaceId, workspaces, setView]);

  const openConvAndSwitchToChat = (id: string, members: string[], title: string) => {
    setActiveConv({ id, members, title });
    setView("chat");
  };

  const renderMain = () => {
    if (view === "chat" && activeConv) {
      return (
        <ChatPane
          convId={activeConv.id}
          members={activeConv.members}
          title={activeConv.title}
        />
      );
    }
    if (view === "inbox") {
      return <InboxView onOpenConv={openConvAndSwitchToChat} />;
    }
    if (view === "marketplace") {
      return <CreateHubView onOpenConv={openConvAndSwitchToChat} />;
    }
    if (view === "archive") {
      return <ArchiveView onOpenConv={openConvAndSwitchToChat} />;
    }
    return (
      <main className="flex-1 grid place-items-center text-[var(--color-fg-3)]">
        <div className="text-center">
          <div className="text-[18px] font-semibold text-[var(--color-fg)] mb-2">
            欢迎使用 Polynoia
          </div>
          <div className="text-[12.5px]">从左侧选一个联系人或项目开始</div>
        </div>
      </main>
    );
  };

  // ── Mobile layout (Capacitor iOS/Android or narrow viewport) ─────
  if (mobile) {
    return (
      <div className="h-screen flex flex-col overflow-hidden bg-[var(--color-bg)]">
        {/* Drawer overlay */}
        {drawerOpen && (
          <>
            <div
              className="fixed inset-0 bg-black/40 z-30"
              onClick={() => setDrawerOpen(false)}
              role="button"
              tabIndex={0}
              aria-label="close drawer"
            />
            <div className="fixed left-0 top-0 bottom-0 w-72 z-40 shadow-xl bg-[var(--color-surface)]">
              <Sidebar
                activeConvId={activeConv?.id ?? null}
                onSelectConv={(id, members, title) => {
                  setActiveConv({ id, members, title });
                  setView("chat");
                  setDrawerOpen(false);
                }}
              />
            </div>
          </>
        )}
        {/* Top bar with hamburger */}
        {view === "chat" && activeConv ? (
          <>
            <div className="flex items-center px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="p-1.5 rounded hover:bg-[var(--color-line)]"
                aria-label="open sidebar"
              >
                <Menu size={18} />
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <ChatPane
                convId={activeConv.id}
                members={activeConv.members}
                title={activeConv.title}
              />
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col">
            <div className="flex items-center px-3 py-2 border-b border-[var(--color-line)] bg-[var(--color-surface)]">
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="p-1.5 rounded hover:bg-[var(--color-line)]"
                aria-label="open sidebar"
              >
                <Menu size={18} />
              </button>
              <span className="ml-3 text-[14px] font-semibold">Polynoia</span>
            </div>
            <main className="flex-1 grid place-items-center text-[var(--color-fg-3)]">
              <div className="text-center px-6">
                <div className="text-[16px] font-semibold text-[var(--color-fg)] mb-2">
                  欢迎使用 Polynoia
                </div>
                <div className="text-[12.5px]">
                  点左上角菜单 · 选一个联系人或项目开始
                </div>
              </div>
            </main>
          </div>
        )}
      </div>
    );
  }

  // ── Desktop / browser layout (Tauri or normal browser) ───────────
  return (
    <div className="h-screen flex overflow-hidden">
      <Sidebar
        activeConvId={activeConv?.id ?? null}
        onSelectConv={(id, members, title) => {
          setActiveConv({ id, members, title });
          setView("chat");
        }}
      />
      {renderMain()}
      {previewOpen && view === "chat" && activeConv && <PreviewPane />}
      {/* Right-side info drawer (agent detail / members list). Globally
          mounted so it can be opened from anywhere — sidebar, chat header,
          message bubble, roles modal. */}
      <RightDrawer />
      {/* Search overlay — Cmd+K global hotkey + header 🔍 button */}
      <ChatSearchOverlay />
    </div>
  );
}

