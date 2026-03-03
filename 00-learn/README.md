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
    └── 02-CLI命令系统.md
```

## 学习进度

### ✅ 已完成
- Git & GitHub 基础（命令、工作流、commit 规范）
- Git 分支、merge、Fork、PR、Sync fork
- nanobot 项目入口（pyproject.toml / __init__.py / __main__.py）
- CLI 命令系统（typer / commands.py / gateway 组装）

### 🔲 接下来
- `nanobot/bus/` — 消息总线
- `nanobot/agent/loop.py` — Agent 主循环（最核心）
- `nanobot/agent/context.py` — 上下文构建
- `nanobot/agent/memory.py` — 记忆管理
- `nanobot/channels/` — 平台适配器
- `nanobot/providers/` — LLM 提供商
