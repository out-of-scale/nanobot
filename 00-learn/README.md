# 🐈 nanobot 学习笔记目录

## 文件夹结构

```
00-learn/
├── README.md                          ← 本文件（导航）
│
├── 01-git-github/                     ← Git & GitHub 学习
│   ├── 01-基础概念与常用命令.md
│   └── 02-分支与协作流程.md
│
└── 02-nanobot-core/                   ← nanobot 项目核心代码
    ├── 01-项目入口与打包配置.md
    ├── 02-CLI命令系统.md
    ├── 03-消息总线bus.md
    ├── 04-agent主循环loop.md
    ├── 05-上下文构建context.md
    ├── 06-记忆管理memory.md
    ├── 07-会话管理session.md
    ├── 08-技能与子Agent与MCP.md
    └── 09-其他模块overview.md
```

## 学习进度

### ✅ 已完成
- Git & GitHub 基础（命令、工作流、commit 规范）
- Git 分支、merge、Fork、PR、Sync fork
- nanobot 项目入口（pyproject.toml / __init__.py / __main__.py）
- CLI 命令系统（typer / commands.py / gateway 组装）
- `nanobot/bus/` — 消息总线（InboundMessage / OutboundMessage / asyncio.Queue）
- `nanobot/agent/loop.py` — Agent 主循环（ReAct 模式、create_task 并发、工具调用循环）
- `nanobot/agent/context.py` — 上下文构建（系统提示拼装、运行时注入、图片处理）
- `nanobot/agent/memory.py` — 记忆管理（两层记忆、工具调用约束 LLM 输出格式）
- `nanobot/session/` — 会话持久化（JSONL 格式、内存缓存、last_consolidated 游标）
- `nanobot/agent/skills.py` — 技能加载（SKILL.md、按需加载、available 检查）
- `nanobot/agent/subagent.py` — 子 Agent（后台异步、防递归、结果走总线）
- `nanobot/agent/tools/mcp.py` — MCP 外部工具协议（懒加载、AsyncExitStack）
- `nanobot/cron/` — 定时任务（at/every/cron 三种调度）
- `nanobot/heartbeat/` — 心跳机制（两阶段 skip/run、工具约束输出）
- `nanobot/templates/` — Prompt 模板（SOUL/AGENTS/USER/TOOLS.md）
- `nanobot/config/` — Pydantic 配置管理

### 🔲 接下来（可按需深入）
- `nanobot/channels/base.py` + `telegram.py` — 平台适配器具体实现
- `nanobot/providers/registry.py` — LLM 提供商注册（2步添加新模型）
- `nanobot/agent/tools/` 各工具实现细节
