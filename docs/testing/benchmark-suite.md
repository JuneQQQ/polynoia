# 测评集(Benchmark Suite)— 平台 harness 对弱模型的提升,要可测量

> 论点:Agent 的交付质量来自「模型 × Harness」。本测评集让第二个因子可量化:
> 同一用例 × 不同模型(尤其 opencode 免费弱模型)反复跑,分数沉淀进
> `benchmark_runs` 表,质量面板与联系人综合分随之更新。

## 资产清单(全部入库,可回归)

| 文件 | 职责 |
|---|---|
| `scripts/testkit/benchmarks.py` | **验收脚本**:20 用例任务文本从 reset.sh 的 CASES 单源解析(ast.literal_eval);每用例 = 通用底线检查(4 项)+ 专项语义检查;`verify(case, ws_dir)` → score/checks。可独立运行验收任意工作区 |
| `scripts/testkit/run_benchmark.py` | **驱动器**:复用/铸造基准联系人 → 独立工作区+会话 → WS user_message 真实轮次(与 UI 同链路)→ 双重沉降判定(running_agents 清空 + 事件流 60s 静默)→ 验收 → PATCH 入库 |
| `scripts/testkit/soak.py` | **浸泡(aging)**:同一会话连续 N 轮,每 K 轮核 5 类不变量(I1 API 一致/I2 无卡死/I3 git 干净/I4 交付无丢轮/I5 事件 seq 单调),报告落 `.tmp/soak-report.json` |
| `POST/GET /api/benchmark/runs` | 结果入库(status/score/checks),喂质量画像(占综合分 45% 权重) |

## 已实测基线(2026-06-13)

| 用例 | 模型 | 结果 | 检查 |
|---|---|---|---|
| game_2048 | opencode/deepseek-v4-flash-free(免费弱模型) | **PASSED 100%** | 9/9(HTML/棋盘/合并/操作/体量/合入 main) |
| game_2048 | opencode-go/glm-5.1 | **PASSED 100%** | 9/9 |
| 浸泡 ×3 轮 | deepseek-v4-flash-free | **PASSED** | 7 不变量全过,89 事件 seq 无空洞 |

## 通用底线(所有用例)

1. 交付了文件(非空工作区) 2. ≥1 笔非 init 提交合入 main
3. 无冲突标记残留 4. 交付物有实质内容(>1KB)

## 已有专项验收(6 个,持续扩)

`game_2048`(棋盘/合并/操作/体量)· `react_plane_war`(工程结构/玩法/README)·
`family_budget_xlsx`(合法 OOXML)· `sales_analysis_report`(口径/结构/数据)·
`single_agent_portfolio`(入口/作品位)· `django_like_api_spec`(路由/权限/文档)。
其余 14 用例自动落到通用底线,补专项时在 `benchmarks.py` 的 `VERIFIERS` 注册即可。

## 用法

```bash
# 单条基准(任意 opencode 模型,免费弱模型见 `opencode models`)
apps/server/.venv/bin/python scripts/testkit/run_benchmark.py \
    --case game_2048 --model opencode/deepseek-v4-flash-free

# 浸泡(默认 30 轮,每 5 轮核不变量)
apps/server/.venv/bin/python scripts/testkit/soak.py \
    --model opencode/deepseek-v4-flash-free --rounds 30
```

结果在质量面板(侧栏 📊)的「基准执行记录」与联系人综合分里可视化。

## 扩展方向(待办)

- 多模型扫描脚本(同用例 × 模型清单,出对比矩阵)
- 失败轨迹取证:benchmark 失败时自动导出该 conv 的 turn_events 切片
- harness 消融:同模型开/关某 harness 组件(共享记忆/工具门禁)对比分数
