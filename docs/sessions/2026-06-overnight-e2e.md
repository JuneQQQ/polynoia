# 自主通宵 E2E:20 个 seed 用例逐个测试 + 边测边修

> **日期**:2026-06-10 凌晨(用户授权全自主推进)
> **方法**:`scripts/testkit/reset.sh` 灌入的 20 个 seed 用例,逐个经「UI 发草稿 → 监视收敛 → `check_case.py` 4 项硬不变量 → 亲查产物/亲跑」三重验证;终端 + Playwright 双通道;**发现 BUG 当场在系统侧修,再回归**。
> **完整逐案流水**见仓库本地工作日志(`.tmp/testkit/OVERNIGHT_TEST_LOG.md`,gitignore);本文是沉淀到仓库的证据摘要。

## 结果总览:20 / 20 PASS

覆盖矩阵:web-game ×2、fullstack ×5、doc(md/docx/xlsx/pptx)、data-report、multimodal ×2、static-page ×2、时序接力、@ 边界、错误恢复、**冲突闭环压轴**。

| 维度 | 证据(亲验) |
|---|---|
| web 游戏 | `2048.html` 浏览器实测可玩(键盘/计分/重开) |
| 表格 | `budget.xlsx` openpyxl 实测:47 笔明细 + 26 个 SUMIF + 饼图 |
| 文档 | 租赁合同 .docx(98 段 + 13 表)、路演 .pptx(10 页页序对契约) |
| 权限 API | 我在 main 区**亲跑 pytest:18/18** |
| 全栈 | FastAPI 上传后端、Express 库存后端**亲起实测**;React 飞机大战**亲跑 npm build 392ms** |
| 多人 burst | 5 成员全栈缺陷追踪:Playwright 端到端建 issue → 后端持久化(id 17→18)→ 列表刷新 |
| 冲突闭环 | 故意同文件冲突:检测→开卡→resolve→合并入 main、**no MERGE_HEAD、无 `<<<<<<<` 残留** |

## 自主修复记录(系统侧 BUG,边测边修)

通宵在被测系统里定位并修复 ~11 类 BUG,横跨终端/思考/派发/进程/路由/沙箱各层:

1. **终端卡乱序竞态卡死「运行中」** → 单调 `seq` 服务端守卫 + 终态快照重试 + 回合末重广播。
2. **后台卡谎报 `exit -1`** → 监视器只在真退出时发终态,CancelledError 不再撒谎。
3. **write 卡停「准备写入」** → 回合末把 pending/running 工具卡 coerce 后**补广播**(原来只改 DB 不广播)。
4. **子进程死后卡片永久卡** → 后端权威 **liveness sweeper**(30s 探活 pgid、死进程闭卡+重广播)。[→ ADR-023](../ADR/ADR-023-terminal-card-lifecycle-backend-owned.md)
5. **沙箱产物服务泄漏(互抢 5173/8000)** → `reset.sh` 新增 reaper(本次清 13 个泄漏 dev-server)。
6. **幽灵未读 +1** → `ChatPane` 在 `finish` 帧补 markRead。
7. **空 reasoning 落库(blank 思考块)** → routes.py 增量持久化路径 + ws_conv 回合末双重拦截(双 body 形态);DB 清掉 7 条历史脏数据。
8. **FileTree 首开 404** → workspace 主目录未建时根路径返回空树。
9. **codex `apply_patch` 被沙箱拒** → identity 提示词告知 codex 系直接用平台 `write`(进 diff/审计管线)。
10. **协调器收尾不彻底(BUG-11/12)** → `orchestrator.py` 验收轮协议加硬约束:**验收轮必须产生真实推进动作**(dispatch 或本轮 present),且 **present 前必须清掉所有 open 冲突卡**。Cases 17/20 复现 → 18/19 修复后连续产出 present 卡验证。
11. **os.getpgid 竞态、monitor 弱引用 GC、datetime 漏导入** 等小洞一并堵上。

## 已知 / 非阻断(记录,未在本夜动)

- **MCP `Transport closed`**(发现 #10,P1):长回合中 CLI 重启 MCP stdio server,present/report 偶发失败后 agent 重试成功;不阻断产物。建议:MCP 工具客户端对幂等的 present/report 自动重试一次。
- gpt-5.5 / glm5.1 长考可 >5min 静默;监视收敛阈值需 ≥300s,收尾型用例以「present 卡 + 60s 静默」或 7min 硬静默兜底。

## 回归测试基线

后端 pytest 全绿(唯一 `test_refresh_credentials` 失败为 **pre-existing**:macOS direct-creds 凭据拷贝,clean HEAD 即失败,与本夜改动无关);前端 tsc exit 0。
