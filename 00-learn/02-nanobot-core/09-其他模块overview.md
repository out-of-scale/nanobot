# 09 — 其他模块概览（cron / heartbeat / templates / config）

---

## 一、cron/ — 定时任务

### 三种调度方式（cron/types.py）

```python
@dataclass
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at_ms: int    # "at"   → 某个时间点执行一次（Unix 毫秒时间戳）
    every_ms: int # "every"→ 每隔 N 毫秒执行
    expr: str     # "cron" → 标准 cron 表达式，如 "0 9 * * *"（每天9点）
    tz: str       # 时区，如 "Asia/Shanghai"
```

### 执行流程

定时器触发 → 构造 `InboundMessage(channel="system")` → `bus.publish_inbound()` → `AgentLoop` 正常处理

与普通用户消息走同一路径，对 Agent 透明。

### CronJob 数据结构

```python
@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule  # 调度配置
    payload: CronPayload    # 执行内容（消息 + 是否推送到渠道）
    state: CronJobState     # 运行时状态（上次执行时间、状态）
    delete_after_run: bool = False  # 一次性任务
```

---

## 二、heartbeat/ — 心跳机制

### 作用

每隔固定时间（默认 30 分钟）唤醒 Agent，检查是否有后台任务要处理。

### 两阶段设计（避免每次都跑完整 Agent）

```
Phase 1 — 决策：
  读取 HEARTBEAT.md
  → 调 LLM（轻量）+ 虚拟工具 _HEARTBEAT_TOOL
  → LLM 返回 {"action": "skip"} 或 {"action": "run", "tasks": "..."}

Phase 2 — 执行（仅 action="run" 时）：
  on_execute(tasks) 回调
  → 走完整 Agent 循环
  → on_notify(result) 推送结果给用户
```

与 `memory.py` 相同的技巧：**用工具调用 schema 强迫 LLM 输出结构化结果**（enum: ["skip", "run"]），而不是解析自由文本。

### HEARTBEAT.md 管理

用户通过告诉 Agent"每天早上给我汇报天气"，Agent 会用 `edit_file` 工具往 `HEARTBEAT.md` 里写任务描述，heartbeat 每次触发时 LLM 读取这个文件决定做什么。

---

## 三、templates/ — Prompt 模板

不是代码，是 Markdown 文件，初始化 workspace 时复制给用户：

| 文件 | 用途 |
|------|------|
| `AGENTS.md` | Agent 行为指令（如何用 cron、heartbeat） |
| `SOUL.md` | Agent 性格定义（语气、风格） |
| `USER.md` | 用户自我介绍模板（背景、偏好） |
| `TOOLS.md` | 工具使用补充说明 |
| `HEARTBEAT.md` | 心跳任务列表初始模板 |
| `memory/` | 初始 MEMORY.md 和 HISTORY.md |

用户可以直接编辑这些文件来个性化 Agent。

---

## 四、config/ — 配置管理

| 文件 | 作用 |
|------|------|
| `schema.py`（16KB）| 用 Pydantic 定义所有配置项（类型安全、自动验证） |
| `loader.py` | 读取 `config.yml`，解析成 schema 对象 |

配置分层结构（来自 schema.py）：

```python
class NanobotConfig(BaseModel):
    providers:   ProvidersConfig    # LLM 提供商配置
    channels:    ChannelsConfig     # 各渠道配置（token、allow_from等）
    agent:       AgentConfig        # Agent 参数（model、max_iterations等）
    mcp_servers: dict               # MCP 服务器配置
    heartbeat:   HeartbeatConfig    # 心跳配置（interval、enabled）
```

---

## 五、整体模块关系图（最终版）

```
用户消息
  ↓
channels/          渠道适配器（Telegram/Discord/飞书...）
  ↓ publish_inbound
bus/               消息队列（解耦渠道与 Agent）
  ↓ consume_inbound
agent/loop.py      大脑（主循环 + 工具调用循环）
  ├─ session/      取历史 → 持久化对话
  ├─ agent/context.py  拼系统提示（templates/ + memory/ + skills/）
  ├─ agent/memory.py   长期记忆压缩
  ├─ agent/skills.py   技能按需加载
  ├─ providers/    调用 LLM
  └─ agent/tools/  执行工具
       ├─ spawn → subagent.py  后台子 Agent
       ├─ cron  → cron/        定时任务
       └─ MCP   → tools/mcp.py 外部工具

[后台并行]
heartbeat/  → 每30分钟 → process_direct() → agent/loop.py
cron/       → 定时触发 → bus.publish_inbound()
config/     → 启动时读取，贯穿全局
```

---

*笔记日期：2026-03-05*
