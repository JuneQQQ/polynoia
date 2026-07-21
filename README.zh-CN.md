<p align="center">
  <img src="assets/brand/logo.svg" alt="Polynoia" width="104" height="104" />
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="assets/readme/community/hero-shared-studio.webp" alt="一位人类和三位各具特色的 AI 协作者回到共享工作台，笔记、决策和项目产物依然保留在原处" width="860" />
</p>

<h1 align="center">Polynoia</h1>

<p align="center">
  <strong>记得工作的 AI 同事。</strong><br />
  Polynoia 是一个本地优先的工作空间，让编码 Agent 拥有自己的身份、实际开展工作的空间，
  以及关于决策与成果的持久、范围明确的工作记忆。
</p>

[快速开始](#快速开始) · [下载 macOS Apple Silicon](https://github.com/JuneQQQ/polynoia/releases/latest) · [文档](#文档与社区) · [参与贡献](CONTRIBUTING.md)

[![Latest release](https://img.shields.io/github/v/release/JuneQQQ/polynoia?display_name=tag&sort=semver&label=release)](https://github.com/JuneQQQ/polynoia/releases/latest)
[![Apache-2.0 license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/JuneQQQ/polynoia?style=flat&label=stars)](https://github.com/JuneQQQ/polynoia)

<p align="center">
  <video src="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4" controls muted playsinline width="860"></video>
</p>
<p align="center">
  <sub>▶︎ <a href="https://github.com/JuneQQQ/polynoia/raw/main/assets/readme/demo.mp4">观看产品演示</a> —— 如果内联播放器没有加载，可直接打开视频。</sub>
</p>

## 是同事，不是又一个标签页

大多数 AI 编码会话都是用完即弃的：提问、回答、关闭，下一次对话又从零开始。模型也许很强，
但它与工作的关系总在重置。

Polynoia 从一个不同的信念出发：**AI 应该像同事一样工作，而不是每次打开对话都重新认识你。**
Agent 再回来时，应该带着可以辨认的角色、恰当的历史信息和真实的工作空间。它的记忆只应在
连续性有用且符合预期的范围内延续；它产出的工作则始终可供人检查。

这个信念形成了三项产品原则。

## 同事有自己的身份

每个 Polynoia Agent 都是一个持久的身份，而不是一次性的模型会话。它的名称、人设、角色、
已配置的工具、技能和模型都与 Agent 记录相绑定。这些配置可以逐步调整，却不会让每次对话
都变成一个全新的匿名助手。

身份也让工作有了归属。每个 Agent 通过已配置的编码适配器运行，并在项目工作中获得一个
Git worktree，因此每份贡献都有明确的位置与历史记录。

## 对话会结束，工作会留下

Polynoia 保留**持久、范围明确的工作记忆**。同一个 Agent 可以把相关的决策和成果带到自己的
后续对话中。在一次对话内，置顶上下文以及契约、决策、报告和产物等记录，会为参与者提供
当前工作的共同背景。

消息、工具活动、diff 和流程结果都会作为工作记录保留下来。这些边界很重要：连续性只沿
明确的范围延续，不会变成所有 Agent 共用的上下文。

## 同事会留下可审查的工作成果

能真正帮上忙的同事，留下的不只是一段精心润色的回复。Polynoia 会保留项目文件、产物、消息、
工具轨迹、diff 和流程结果，让人们既能检查最终成果，也能了解它是如何产生的。

每轮开始时，Polynoia 都会基于项目的集成分支（默认为 main），重置每个 Agent 在每个对话中
的 Git worktree。一轮成功结束后，Polynoia 会先把无冲突的提交自动集成进该分支；随后，
负责协调的 Agent 和人便可检查由此产生的提交与轨迹。合并冲突会被明确呈现，以供解决。

## 看看 Polynoia 如何工作

| 群聊与编排 | 内联产物预览 |
|---|---|
| <img src="assets/readme/群聊与编排.png" alt="Polynoia 群聊中的并行 Agent 工作泳道与归属明确的结果" width="420" /> | <img src="assets/readme/预览.png" alt="Polynoia 对话旁打开的内联产物预览" width="420" /> |
| **可审查的 diff 与提交历史** | **持久的 Agent 身份** |
| <img src="assets/readme/diff.png" alt="Polynoia 提交历史中的并排代码 diff" width="420" /> | <img src="assets/readme/联系人.png" alt="Polynoia 联系人页面中的持久 Agent 身份与配置详情" width="420" /> |
| **Agent 质量面板** | **角色专长库** |
| <img src="assets/readme/质量面板.jpg" alt="Polynoia Agent 质量面板中的逐 Agent 可靠性与基准证据" width="420" /> | <img src="assets/readme/角色库.jpg" alt="Polynoia 用于把角色预设添加为 AI 同事的专长库" width="420" /> |

## Polynoia 会记住什么

假设你在单聊中告诉 **Frontend Agent**，下个版本的代号是 “Aurora”。之后，同一个 Frontend
Agent 再次参与项目工作时，它的个人工作记忆可以把这个代号延续下来。**QA Agent 不会继承这段
私有记忆。** 要让项目参与者都能使用这项决定，请将它置顶到项目对话中，或将它记录为共享
决策或产物。

| 范围 | 谁可以使用 | 会保留什么 |
|---|---|---|
| **个人工作记忆** | 同一个 Agent，跨越它自己的多次对话 | 有助于保持连续性的 Agent 范围内的决策与成果 |
| **共享项目记忆** | 记录所在对话中的参与者 | 置顶上下文，以及对话范围内的契约、决策、报告和产物记录 |
| **持久项目产物** | 适用的对话、项目或 worktree 范围内的人与 Agent | 消息、工具轨迹、diff 和流程结果等对话记录，以及文件和提交等 Git 产物；每一项都保留在各自的范围内 |

## 快速开始

### macOS 桌面版（Apple Silicon）

官方可下载版本目前仅支持搭载 **Apple Silicon**、运行 **macOS 11 或更高版本**的 Mac。

1. 安装至少一个受支持的编码 Agent CLI 并完成身份验证。可选项包括 **Claude Code**、
   **Codex 0.118.0 或更高版本**、**OpenCode**；三者不必全部安装。
2. 下载[最新版本](https://github.com/JuneQQQ/polynoia/releases/latest)，打开 DMG，
   然后将 Polynoia 拖入 **Applications**。
3. 当前构建采用 ad-hoc 签名，且尚未经过公证（notarization）。首次启动时，请按住 Control 键点击
   Polynoia，然后选择**打开**。
4. 首次启动时请保持网络连接。Polynoia 会准备它的私有后端，这可能需要几分钟。

桌面应用会直接使用已在 Mac 上安装并完成身份验证的编码 Agent CLI。

### 从源码运行

前置要求：**Git**、**Make**、**Python 3.12+**、**Node.js 22+**、**uv**，以及
**pnpm 9**（首选）。开始让 Agent 工作之前，请至少安装 Claude Code、Codex 0.118.0+ 或 OpenCode
中的一个，并完成身份验证。

```bash
git clone https://github.com/JuneQQQ/polynoia.git
cd polynoia
make install
make dev
```

在 [http://127.0.0.1:7788](http://127.0.0.1:7788) 打开 Web 应用。开发 API 位于
[http://127.0.0.1:7780](http://127.0.0.1:7780)。

## 已实现的能力

| 领域 | 已实现内容 |
|---|---|
| **编码 Agent 适配器** | 将 Claude Code、Codex 和 OpenCode 接入同一个以对话为中心的工作空间，并使用它们已安装且完成身份验证的 CLI |
| **身份与记忆** | 持久的 Agent 配置、Agent 范围的跨对话工作记忆，以及对话范围的共享记录和置顶上下文 |
| **工作空间** | 每轮开始时，基于已配置的集成分支（默认为 main）重置每个 Agent 在每个对话中的 Git worktree |
| **产物与轨迹** | 持久保存消息、工具活动、diff、流程结果、文件和信息丰富的产物记录 |
| **编排** | 支持单聊和群聊；负责协调的 Agent 可以委派工作，并收集归属明确的结果 |
| **应用外壳** | 提供 Web、Tauri 桌面端和 Capacitor 移动端的源码外壳；官方可下载版本目前仅支持 macOS Apple Silicon |

## 信任与边界

**本地优先存储。** 默认情况下，后端状态和项目工作都保留在你控制的机器上。桌面版本会在
首次启动时准备一个私有的本地后端。

**Git 隔离。** Worktree 可以隔离分支和并发 Git 工作，但它不是操作系统级沙箱。能够使用
shell 的 Agent 会以本地用户身份执行操作，因此请保护好凭据与宿主机数据，并审查每个 Agent
可用的工具和访问权限。

**可审查的执行过程。** 持久保存的消息、工具轨迹、diff 和流程结果让 Agent 活动可以被检查。
带回执的消息投递使客户端能确认系统已持久接收用户消息，并支持重放与恢复；但它不保证
模型执行具备 exactly-once（恰好一次）语义。

开发 API 仅供可信的本地开发使用。在没有适当的生产级身份验证和网络控制时，请勿将它暴露
到不可信网络中。

## 项目状态与局限

Polynoia 正在积极开发中。随着项目逐渐成熟，接口、数据格式、Agent 工作流和打包方式都可能变化。

- 持久、范围明确的工作记忆并不意味着无限、永久或完整回忆。Polynoia 目前不提供语义检索
  或向量检索，也不会自主学习。
- 个人工作记忆并不是全局记忆。Agent 不会继承另一个 Agent 的私有上下文，Polynoia 也不提供
  基于云端的跨设备记忆。
- 每轮开始时，worktree 会基于已配置的集成分支（默认为 main）重置；它不会暴露另一个 Agent
  尚未合并的工作。
- 本地优先运行并不代表端到端加密。
- 投递回执并不保证模型执行具备 exactly-once（恰好一次）语义。
- 仓库包含 Web、桌面端和移动端的源码外壳，但官方可下载版本目前仅支持运行 macOS 11 或
  更高版本的 Apple Silicon Mac。

## 文档与社区

- 阅读[设计规范](docs/superpowers/specs/2026-05-23-polynoia-design.md)、
  [上下文系统概览](docs/design/context-system.md)和[架构决策记录](docs/ADR/)。
- 在[贡献指南](CONTRIBUTING.md)中了解如何构建、验证和提交更改，并遵守
  [行为准则](CODE_OF_CONDUCT.md)。
- 如发现疑似安全漏洞，请按照[安全政策](SECURITY.md)中的流程私下报告。
- 使用 [GitHub Issues](https://github.com/JuneQQQ/polynoia/issues) 报告错误或提出建议，
  通过 [Releases](https://github.com/JuneQQQ/polynoia/releases) 获取已发布的构建。
- Polynoia 采用 [Apache-2.0](LICENSE) 许可证。
