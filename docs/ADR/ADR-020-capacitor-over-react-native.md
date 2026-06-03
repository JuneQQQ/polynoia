# ADR-020 — 移动端(iOS/Android):Capacitor 6 复用 web 构建,不走 React Native 重写

- **状态**:accepted
- **日期**:2026-06-03
- **相关**:`apps/mobile/`(Capacitor 6 脚手架)、`apps/web/src/lib/platform.ts:isMobile()`、`apps/desktop/`(Tauri 2,同「壳复用 web」思路)、`mobile-plan.html`(7 阶段落地计划)、CLAUDE.md §6.3、rule.md(移动端范围:轻量 IM)

## 背景

rule.md 要求移动端是 web/桌面端的**子集**:**查看对话、发消息/@、审批(pending-edit / ask-form / 冲突选边)、只读产物预览**;**不做**文件树编辑 / 终端 / 提交历史 / 建项目。即移动端不是新产品,而是把已有 `apps/web` 的一个**安全只读 + 审批**切片搬到手机上。

由此产生一个架构岔路口:**用 React Native 把 UI 重写一遍(原生组件 + `packages/ui-rn`)**,还是**把现成的 `apps/web` React 构建装进原生 WebView 壳里跑**?

这不是凭空的新决定 —— 桌面端(`apps/desktop/`)早已用 **Tauri 2 包 `apps/web/dist`**,零业务代码。移动端面临的是同一道选择题:要不要为第三端再维护一套 UI。

`apps/web` 现状支撑了「复用」这条路:`platform.ts:isMobile()` 已做三级检测(`__POLYNOIA_PLATFORM__` 构建注入 → `window.Capacitor.isNativePlatform()` 运行时 → 屏宽/UA 兜底),`App.tsx` 已有「抽屉 + 单列」的移动分支,`store.ts / lib/{api,ws,types}.ts` ~85% 是纯 TS、无 DOM 假设。`apps/mobile/` 已是 Capacitor 6.2 脚手架(`webDir: "../web/dist"`,插件 keyboard / network / preferences / splash-screen / status-bar 就位)。

## 决策

**移动端 = 同一份 `apps/web` 构建,装进 Capacitor 6 原生壳(iOS/Android)里跑。不重写 UI,不建 `packages/ui-rn`,不引 React Native。**

- 三端(web / 桌面 Tauri / 移动 Capacitor)共用一份 React 代码;差异只靠 `isMobile()` + 一层薄 shim(`runtime-config.ts` 服务器基址 / `storage.ts` 持久化 / `native.ts` Capacitor 插件桥)区分。
- 移动专属的新代码只有**少数表面**:连服务器引导页、审批底部弹层(bottom sheet)、只读产物 modal —— 它们复用底层的 `store` / `api` / `ws` / parts 渲染,不是平行实现。
- 桌面端/移动端编辑、终端、文件树、提交历史等重交互**在移动端 `isMobile()` 处直接关掉**,而不是「移动端没实现」。
- `packages/*` 维持为空:业务逻辑仍住在 `apps/web/src`。落地分 7 阶段,详见 `mobile-plan.html`。

## 为什么

| 维度 | Capacitor 复用 web(选) | React Native 重写(弃) |
|---|---|---|
| UI 代码 | **一份**,三端共用 `apps/web` | 二份:web 一套 + RN 一套(`packages/ui-rn`),永久双写 |
| 与现有组件的距离 | parts 注册表 / BurstCard / 审批栏 / DocPreview **原样跑** | 18 种 part + diff 视图 + markdown 渲染全部 RN 重画 |
| 第三方栈复用 | CodeMirror / `@git-diff-view` / react-markdown / Radix 直接用(只读切片甚至无需) | 这些**没有 RN 版**,要么找替代要么自写 |
| 设计语言一致性 | 同一套 CSS 变量 / Tailwind token,天然一致 | RN 无 CSS,要把 token 翻成 `StyleSheet`,易漂移 |
| 「子集、不偏离」契合度 | 高 —— 关掉重功能即得子集 | 低 —— 重写时很难不顺手「改进」,反而偏离 |
| 原生能力 | Capacitor 插件(keyboard/network/preferences/status-bar/splash + 按需 `@capacitor/app`) | RN 原生模块更全(但本产品用不到相机/蓝牙/后台等) |
| 极致原生手感 | WebView,≈ 95% 原生感(列表滚动/手势用心调即可) | 100% 原生控件 |
| 维护面 | 一套测试、一套类型、一处改 bug | 双倍:每个 part 改一次要改两端 |

- **本产品是「轻量 IM + 审批 + 只读预览」,不是性能敏感的原生 App**:没有 60fps 游戏式交互、没有重原生硬件诉求。WebView 的性能上限对「看消息 + 点审批 + 读产物」完全够用,RN 那 5% 的原生手感增益,换不回双写一整套 UI 的代价。
- **与桌面端同构**:桌面已是 Tauri 包 web,移动再用 Capacitor 包 web,三端「壳复用 web」心智统一,onboard 一次讲清。
- **「子集、不偏离」是 rule.md 硬要求**:复用同一份代码 + `isMobile()` 关功能,天然保证移动端是 web 的真子集;RN 重写则每个组件都是一次「重新决定长什么样」的机会,偏离风险高。
- **脚手架已落地**:`apps/mobile/` 的 Capacitor 6 + `webDir: "../web/dist"` + `isMobile()` 三级检测都在,沿这条路是顺水,改道 RN 是推倒。

## 否则会怎样(若走 React Native)

- **永久双写**:18 种 MessagePart、diff 视图、markdown + mention 渲染、审批流全部在 RN 重画;此后每改一处 UI 都要改两端,bug 也要修两遍。
- **第三方栈断供**:CodeMirror / `@git-diff-view/react` / react-markdown / Radix / cmdk 均无 RN 版,只读预览要么降级要么自写渲染器。
- **设计语言漂移**:CSS 变量 / Tailwind token 体系在 RN 失效,要重映射到 `StyleSheet`,长期必然与 web 不一致 —— 直接违背「不要和目前的出入太大」。
- **多养一个 `packages/ui-rn`**:CLAUDE.md 明确 `packages/*` 为空、逻辑在 `apps/web`;引 RN 等于推翻这条跨平台架构基线。
- 投入产出倒挂:为 ≤5% 的原生手感,付 2× UI 工程量 + 第三方替代成本 + 长期双写税。

## 代价

- **WebView 性能天花板**:长列表滚动、键盘避让、手势返回需逐项调优(已在 `mobile-plan.html` Phase 4 列为原生集成项);极端场景手感弱于原生控件。
- **连服务器是设备级前置**:WebView 拿不到相对 URL / `window.location` 后端基址,必须先有 `runtime-config.ts` + 连服务器引导页(LAN IP → 测 `/api/health` → 持久化),否则移动端连不上后端。这是复用方案独有的一道坎(Phase 0/1)。
- **CORS**:后端 `cors_origins` 要加 `capacitor://localhost` / `https://localhost`(`allow_credentials=True` 禁用 `*`,需显式列举,见 Phase 5)。
- **离线/恢复**:WebView 不像原生那样自动管生命周期,WS 重连要在 `resume`/`online` 事件里手动触发。
- 这些都是**有界的薄 shim 工作**(总计一层 `lib/{runtime-config,storage,native}.ts` + 几个移动表面),不波及核心逻辑 —— 相较 RN 的整套重写,代价小一个量级。

## 何时反悔(切回 React Native 的触发条件)

- 移动端范围**突破 rule.md 子集**,变成需要重原生交互的一等产品功能(如设备端实时音视频、相机深度集成、需 60fps 的复杂手势画布)。
- WebView 在真机上的**列表/手势性能**经实测无法接受,且穷尽优化(虚拟列表 / 原生过渡)后仍不达标。
- 出现**离线优先 / 后台常驻**等 WebView 壳难以胜任、而 RN 原生模块明显更合适的核心诉求。
- 届时也优先评估**局部原生**(Capacitor 自定义插件 / 原生子视图嵌入)而非整体改道 RN —— 全量 RN 重写应是最后手段。
