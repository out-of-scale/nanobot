# 08 — 技能、子 Agent 与 MCP（agent/skills.py · subagent.py · tools/mcp.py）

---

## 一、Skills — 技能加载器（agent/skills.py，229行）

### 技能是什么？

技能 = 一个 `SKILL.md` 文件，里面写给 LLM 看的操作说明书。

```
nanobot/skills/         ← 内置技能
├── github/SKILL.md     ← 教 Agent 怎么用 GitHub CLI
├── weather/SKILL.md    ← 教 Agent 查天气
├── cron/SKILL.md       ← 教 Agent 创建定时任务
├── memory/SKILL.md     ← 教 Agent 管理记忆
└── tmux/SKILL.md       ← 教 Agent 用 tmux

workspace/skills/       ← 用户自定义技能（优先级更高）
└── my-skill/SKILL.md
```

### SKILL.md 文件格式

```markdown
---
name: weather
description: Get current weather information
metadata: {"nanobot": {"requires": {"bins": ["curl"], "env": ["WEATHER_API_KEY"]}}}
---

# Weather Skill
To check weather, run: curl ...
```

- `requires.bins`：需要的命令行工具（缺失则标记 `available=false`）
- `requires.env`：需要的环境变量

### 两种加载策略（节省 Token）

| 类型 | frontmatter 中 | 行为 |
|------|---------------|------|
| 常驻技能 | `always: true` | 全文塞进系统提示 |
| 普通技能 | 默认 | 只列摘要，Agent 按需 `read_file` 读详情 |

### 技能摘要用 XML 格式

```xml
<skills>
  <skill available="true">
    <name>github</name>
    <description>GitHub CLI operations</description>
    <location>/path/to/SKILL.md</location>
  </skill>
  <skill available="false">
    <name>weather</name>
    <requires>ENV: WEATHER_API_KEY</requires>
  </skill>
</skills>
```

---

## 二、SubagentManager — 子 Agent（agent/subagent.py，247行）

### 主 Agent "外包"任务给子 Agent

```
主 Agent 调 spawn 工具（"去帮我搜这个话题写报告"）
    ↓
SubagentManager.spawn() → asyncio.create_task()
    ↓ 立即返回 "已启动子 Agent，完成后通知你"

[后台异步]
_run_subagent()
  ├─ 独立的 ToolRegistry（无 message、无 spawn 工具，防递归）
  ├─ 最多 15 轮工具调用（主 Agent 是 40 轮）
  └─ 完成后 → _announce_result()
         ↓
    bus.publish_inbound(channel="system", ...)  ← 结果注入为系统消息
         ↓
    主 Agent 收到 → 总结后发给用户
```

### 两个关键设计

1. **无 `spawn` 工具**：子 Agent 不能再派子子 Agent，防止无限递归
2. **结果走总线**：子 Agent 完成后把结果打包成 `channel="system"` 的入站消息，主 Agent 正常处理并输出

---

## 三、MCP — 外部工具协议（agent/tools/mcp.py，4KB）

### MCP 是什么？

MCP（Model Context Protocol）是 Anthropic 提出的开放标准，允许 Agent 连接第三方工具服务器（数据库、IDE 插件、API 服务等）。

### 使用方式

```yaml
# config.yml 里配置 MCP 服务器
mcp_servers:
  my-db:
    url: http://localhost:8080
```

### 连接时机：懒加载

```python
# 第一条消息到来时才连接（不是启动时）
async def _connect_mcp(self) -> None:
    if self._mcp_connected or self._mcp_connecting:
        return
    # 用 AsyncExitStack 管理多个 MCP 连接生命周期
    self._mcp_stack = AsyncExitStack()
    await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
```

连接后，MCP 服务器暴露的工具自动注册进 `ToolRegistry`，对 LLM 完全透明，和内置工具一样使用。

---

## 四、工具注册表（agent/tools/registry.py）

```python
class ToolRegistry:
    def register(self, tool: BaseTool) -> None   # 注册工具
    def execute(self, name, arguments) -> str    # 执行工具
    def get_definitions(self) -> list[dict]      # 生成 LLM 可读的工具描述列表
    def get(self, name) -> BaseTool | None       # 按名称获取工具
```

内置工具清单：
| 工具 | 功能 |
|------|------|
| `read_file` / `write_file` / `edit_file` / `list_dir` | 文件操作 |
| `exec` | 执行 shell 命令 |
| `web_search` / `web_fetch` | 网络搜索/抓取 |
| `message` | 向指定渠道发消息（流式进度） |
| `spawn` | 启动子 Agent |
| `cron` | 管理定时任务 |

---

*笔记日期：2026-03-05*
