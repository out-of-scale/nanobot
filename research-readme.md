# Nanobot Research Mode 使用文档

## 概述

Research Mode 是 nanobot 的专用操作模式，聚焦于**研究前半段工作**：文献搜索 → 问题定框 → Gap 提取 → 创新点生成 → 轻量短名单决策。

它**不是**全自动研究 OS，也不做实验执行或论文撰写。它的定位是「互动式创新点发现助手」。

---

## 快速开始

### 初始化工作区

```bash
# 用已有 workspace 初始化（默认 workspace）
nanobot research-init

# 指定路径和研究主题
nanobot research-init --workspace ./my-research --topic "graph neural networks on heterogeneous hardware"
```

初始化会创建以下目录结构：

```
<workspace>/
├── RESEARCH.md           ← 研究指南（系统提示词注入源）
├── memory/
│   ├── MEMORY.md         ← 会话级记忆
│   ├── papers/           ← Paper Cards
│   ├── gaps/             ← Gap Cards
│   ├── ideas/            ← Idea Cards
│   └── decisions/        ← Decision Cards
└── artifacts/
    ├── framing_report/
    ├── literature_map/
    ├── gap_report/
    ├── idea_candidates/
    ├── idea_shortlist/
    ├── idea_brief/
    └── novelty_audit/
```

### 进入交互模式

```bash
# 普通进入
nanobot research

# 指定主题（首次进入）
nanobot research --topic "efficient GNN training"

# 恢复已有进度（显示当前阶段状态再进入）
nanobot research --resume

# 单次发消息（非交互式）
nanobot research --message "start gap extraction"

# 指定自定义 workspace
nanobot research --workspace ./my-research
```

### 查看当前进度

```bash
nanobot research-status
# 输出：阶段、问题、paper/gap/idea 数量、卡数统计
```

---

## 五阶段研究流程

系统会根据 workspace 状态**自动推断当前阶段**，无需手动切换。

```
scout-lite → gap-finder → idea-miner → idea-critic → decision-lite
```

### 阶段推进规则

| 当前状态 | 推断阶段 |
|---------|---------|
| 无 paper cards | `scout-lite` |
| paper < 3 篇 | `scout-lite`（回滚） |
| paper ≥ 3，无 gap | `gap-finder` |
| gap 无 paper 证据 | `gap-finder`（回滚） |
| 有 evidence gap，无 ideas | `idea-miner` |
| ideas < 2 个 | `idea-miner`（回滚） |
| ideas ≥ 2，无 shortlisted | `idea-critic` |
| 有 shortlisted idea | `decision-lite` |

### 阶段一：scout-lite — 文献侦察

**目标：** 稳定问题框架，建立文献邻域

四层框架结构：
1. **Task-Definition** — 具体任务是什么？成功标准？
2. **Evaluation-Contract** — 哪个数据集/指标/benchmark？
3. **Literature Neighborhood** — 最近 5-10 篇参考文献
4. **Baseline Direction** — 当前最佳方法的优劣势

**完成条件：** 至少 3-5 篇 paper cards + 明确的 next anchor

### 阶段二：gap-finder — Gap 提取

**目标：** 将 paper 证据转化为结构化研究缺口

Gap 类型分类（`gap_type` 五选一）：

| 类型 | 含义 |
|------|------|
| `failure_mode` | 已知失败模式或不可靠行为 |
| `evaluation_blind_spot` | 评估协议遗漏了重要方面 |
| `assumption_break` | 在真实场景中会破裂的假设 |
| `missing_capability` | 邻近方法完全缺乏的能力 |
| `efficiency_bottleneck` | 阻碍实际采用的资源开销 |

**完成条件：** 至少 1 个带 paper 证据的 gap card，推荐 3-5 个强 gap

### 阶段三：idea-miner — 创新点生成

**目标：** 从 gap 生成有意义差异化的候选方向

三步协议：
1. **Raw Slate** — 每个 gap 生成 2-3 个机制不同的方向
2. **Serious Frontier** — 删掉平凡变体、无机制差异、无可行验证路径的想法
3. **Why-Now Check** — 每个候选方向必须回答：「为什么现在比 2 年前更可行？」

每个 IdeaCard 必填：`why_now`、`closest_prior_work`、`difference_from_prior_work`、`core_mechanism`

**完成条件：** ≥ 2 个 candidate ideas，每个都填了 `why_now`

### 阶段四：idea-critic — 轻量验证与短名单

**目标：** 从候选前沿筛选出真正值得推进的方向

五维评估：

| 维度 | 评级 |
|------|------|
| Novelty（相对最近工作） | high / moderate / low |
| Value（即使不绝对新颖也值得） | high / moderate / low |
| Feasibility（当前条件下能验证吗） | high / moderate / low |
| Risk（最大单点失败模式） | high / moderate / low |
| Validation Hint（最小可信首实验） | — |

产出：比较表 + shortlisted/parked 状态 + why-not 记录

### 阶段五：decision-lite — 方向决策

**目标：** 产出明确的推荐 next move

每个 decision card 必须回答五问：
1. `question` — 具体在决策什么？
2. `winner` — 推荐哪个方向？
3. `why_winner` — 涵盖 novelty + feasibility 的 ≥2 句理由
4. `why_not_others` — 每个落选方向的具体弱点
5. `next_action` — 最小的具体下一步验证行动

---

## 研究卡系统（Cards）

所有研究对象都以持久化 Markdown + YAML frontmatter 存储，跨会话保留。

### 五种卡片类型

#### ProblemCard（问题卡）
```yaml
card_type: problem
card_id: problem
title: "研究主题标题"
topic: ""
objective: ""
constraints: []
baselines: []
evaluation_targets: []
preferred_idea_ids: []    # 用户偏好的方向（交互收敛用）
excluded_directions: []   # 排除的方向
checkpoint_reached: ""
```

#### PaperCard（文献卡）
```yaml
card_type: paper
card_id: "paper-xxx"
title: ""
authors: []
year: null
venue: ""
url: ""
task: ""
method_family: ""
core_mechanism: ""
contributions: []
limitations: []           # Gap 提取的主要来源
keywords: []
```

#### GapCard（缺口卡）
```yaml
card_type: gap
card_id: "gap-xxx"
title: ""
description: ""
evidence_paper_ids: []    # 必填：至少 1 个 paper card_id
gap_type: ""              # 必填：五类之一
evidence_confidence: ""   # strong(≥3 papers) / moderate(2) / weak(1)
research_value: ""
main_risk: ""
```

#### IdeaCard（创意卡）
```yaml
card_type: idea
card_id: "idea-xxx"
title: ""
target_gap_ids: []         # 必填：至少 1 个 gap card_id
one_sentence_pitch: ""
core_mechanism: ""
difference_from_baseline: ""
difference_from_prior_work: ""
why_now: ""               # 必填：非空，引用具体近期进展
closest_prior_work: ""    # 必填：具体方法/论文名
novelty: ""               # high / moderate / low
feasibility: ""
main_risk: ""
validation_hint: ""
status: candidate          # candidate / shortlisted / rejected / parked
```

#### DecisionCard（决策卡）
```yaml
card_type: decision
card_id: "decision-xxx"
title: ""
outcome: shortlist          # shortlist / reject / park / next-step
question: ""               # 必填
winner: "idea-xxx"         # 必填
why_winner: ""             # 必填：≥2 句，涵盖 novelty 和 feasibility
why_not_others: []         # 必填：每个落选方向一条
next_action: ""            # 必填
```

---

## Artifact 系统（阶段产物）

每个研究阶段产出 1-2 个 Markdown 合成文档，是阶段的「真实记录」。

| artifact_type | 阶段 | 内容 |
|---|---|---|
| `framing_report` | scout-lite | 四层框架报告 |
| `literature_map` | scout-lite | 文献邻域地图 |
| `gap_report` | gap-finder | 结构化 gap 分析 |
| `idea_candidates` | idea-miner | 候选创新方向集合 |
| `novelty_audit` | idea-miner | 新颖性审计表（自动生成） |
| `idea_shortlist` | idea-critic | 比较表 + 短名单（含 why-not） |
| `idea_brief` | decision-lite | 获胜方向的详细 brief |

**novelty_audit** 由系统在 idea-miner 阶段自动生成，格式为 Markdown 表格：

| Idea | Closest Prior Work | Differentiation | Overlap Risk |
|------|--------------------|-----------------|--------------|
| ...  | ...                | ...             | low / high   |

Overlap Risk 判定规则：
- `closest_prior_work` 为空 → `high (prior work unknown)`
- `difference_from_prior_work` 不足 5 个词 → `high (differentiation too vague)`
- 否则 → `low`

---

## LLM 可调用工具（12 个）

### 文献工具

| 工具名 | 用途 |
|--------|------|
| `literature_search` | 主动搜索文献，支持 5 种搜索层级，返回去重+聚类结果 |
| `paper_digest` | 给定 URL 抓取论文页面，返回 PaperCard 模板 + 摘要 |

`literature_search` 的 `searchRound` 参数：

| 值 | 含义 |
|----|------|
| `full` | 完整四层搜索 |
| `topic_expansion` | 主题扩展搜索 |
| `baseline_neighborhood` | 基线邻域搜索 |
| `method_family` | 方法家族搜索 |
| `counter_evidence` | 反证搜索（找驳斥性文献） |

### Card 工具

| 工具名 | 用途 |
|--------|------|
| `save_research_card` | 保存 card（含验证，失败返回 `validation_error`） |
| `research_memory_list_recent` | 列出最近更新的 cards，可按类型过滤 |
| `research_memory_search` | 跨 cards 关键词搜索 |
| `research_memory_read` | 读取指定 card |

### Artifact 工具

| 工具名 | 用途 |
|--------|------|
| `save_research_artifact` | 保存阶段合成文档 |
| `research_artifact_list` | 列出已保存的 artifacts |
| `research_artifact_read` | 读取指定 artifact |

### 会话管理工具

| 工具名 | 用途 |
|--------|------|
| `research_memory_audit` | **恢复会话必调**：返回卡数、artifact 数、当前阶段、推荐下一工具 |

`research_memory_audit` 返回示例：

```json
{
  "current_stage": "idea-miner",
  "memory_md_length": 312,
  "cards": { "paper": 4, "gap": 2, "idea": 0, "decision": 0 },
  "artifacts": { "gap_report": 1, "literature_map": 1 },
  "recommended_next_tool": "research_artifact_read artifactType=gap_report to review gaps before ideation",
  "rollback_reason": null
}
```

---

## 记忆系统（三层）

```
Layer 1 — MEMORY.md（会话级）
  ↳ 会话笔记、用户澄清、风格偏好
  ✗ 不写论文摘要、gap 描述、创意机制

Layer 2 — Research Cards（研究对象层）
  ↳ 每篇 paper / gap / idea / decision 都是一张 card
  ↳ 先调 research_memory_list_recent + research_memory_search，再做新分析

Layer 3 — Artifacts（阶段合成层）
  ↳ 每阶段 1-2 个 Markdown 合成文档
  ↳ 先调 research_artifact_list + research_artifact_read，再重新分析
```

**压缩预警：** 当 `MEMORY.md > 500 字符`，系统提示词自动注入以下警告，引导 LLM 优先读 cards/artifacts 而非扩展 MEMORY.md：

> ⚠️ MEMORY.md is growing long. Prefer `research_memory_list_recent` / `research_artifact_list` to orient before adding more session notes.

---

## 验证机制（Output Contracts）

`save_research_card` 在保存前做前置验证，失败时返回：

```json
{
  "status": "validation_error",
  "card_type": "gap",
  "errors": ["evidence_paper_ids must contain at least 1 paper card_id"],
  "hint": "Fix the listed fields and call save_research_card again."
}
```

各卡类型的强制字段：

| 卡类型 | 必须有值的字段 |
|--------|--------------|
| `gap` | `evidence_paper_ids`（非空列表）、`gap_type`（五类之一） |
| `idea` | `target_gap_ids`（非空列表）、`why_now`（非空字符串） |
| `decision` | `winner`（非空）、`why_winner`（非空） |

LLM 收到 `validation_error` 后应修正字段并重新调用，不得跳过。

---

## 新颖性审计门控

在进入 idea-critic 之前，系统自动检测 `closest_prior_work` 填写情况：

| 情况 | 系统行为 |
|------|---------|
| **所有** candidate ideas 都缺少 `closest_prior_work` | 设置 `rollback_reason`，阻止推进到 idea-critic |
| **部分** ideas 缺少 `closest_prior_work` | 添加到 `missing_prerequisites` 警告，允许继续但显示提示 |

---

## 交互收敛

两种情况下系统会在 Context Block 的 `## Self-Check` 中设置 `needs_user_input` 提示：

| 触发条件 | 用户需要做什么 |
|---------|--------------|
| `idea-miner` 阶段，所有 gap 置信度均为 `weak` | 告知是否继续，或要求回 scout-lite 补充文献 |
| `decision-lite` 阶段，`ProblemCard.preferred_idea_ids` 为空 | 告知偏好哪个方向，系统更新到 ProblemCard 持久化 |

---

## CLI 快速参考

```bash
# 初始化 workspace
nanobot research-init --topic "your topic"
nanobot research-init --workspace ./my-research --topic "your topic"

# 查看当前研究状态
nanobot research-status

# 进入交互研究对话（主入口）
nanobot research
nanobot research --resume          # 恢复模式，先显示进度摘要
nanobot research --topic "xxx"     # 带主题启动（首次）
nanobot research --message "xxx"   # 非交互式单次调用

# 批处理模式（确定性模板，无 LLM，is_llm_generated=false）
nanobot research-scout --topic "xxx"         # scout 阶段
nanobot research-gaps                         # gap 合成
nanobot research-ideas                        # idea 生成
nanobot research-shortlist                    # shortlist
```

> **注意：** 批处理命令（`research-scout/gaps/ideas/shortlist`）为确定性模板回退，产出质量不及交互模式。真正的分析质量来自 `nanobot research` 交互模式下的 LLM 调用路径。

---

## 推荐工作流

```bash
# 第一次启动
nanobot research-init --topic "你的研究主题"
nanobot research

# 会话内流程（LLM 自动驱动）
# 1. LLM 调用 literature_search 建立文献邻域
# 2. 确认 3-5 篇关键 paper 后，LLM 进入 gap-finder
# 3. LLM 提取 gap，你确认或补充证据
# 4. 进入 idea-miner，LLM 生成候选方向
# 5. 如提示 needs_user_input，告知偏好方向或是否继续
# 6. idea-critic 产出比较表，decision-lite 给出推荐

# 随时查看进度
nanobot research-status

# 下次继续
nanobot research --resume
```

---

## 知识图谱与证据链

系统自动维护 card 间的关联图，并在每次对话的 Context Block 中注入 `## Evidence Chains` 摘要：

```
## Evidence Chains
- gap-assumption_break ← paper-hetero-gnn
- gap-efficiency_bottleneck ← paper-cluster-gcn, paper-gas
- idea-selective-compute → gap-efficiency_bottleneck
```

通过工具验证链路完整性：

- `validate_links(card)` — 验证单张 card 的所有 ID 引用是否解析到磁盘上的文件
- `check_graph_integrity()` — 扫描全部 gap/idea/decision cards，返回所有断链警告
