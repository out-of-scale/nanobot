# 04 — Agent 主循环（nanobot/agent/loop.py）

> 文件路径：`nanobot/agent/loop.py`（510 行，项目最核心文件）  
> 核心类：`AgentLoop`

---

## 1. 这个文件做什么？

`AgentLoop` 是整个系统的"大脑"，负责：

1. 从消息总线取消息
2. 组装发给 LLM 的上下文
3. 调用 LLM
4. 执行工具（循环）
5. 把结果发回总线

---

## 2. 构造函数 `__init__`

```python
class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,          # 消息总线（解耦渠道与 Agent）
        provider: LLMProvider,    # LLM 提供商（OpenAI/Claude/DeepSeek...）
        workspace: Path,          # 工作目录（文件读写的根目录）
        max_iterations: int = 40, # 工具调用最大轮数，防死循环
        temperature: float = 0.1, # LLM 温度（越低越确定）
        ...
    ):
        self.context  = ContextBuilder(workspace)   # 上下文构建器
        self.sessions = SessionManager(workspace)   # 会话管理（持久化历史）
        self.tools    = ToolRegistry()              # 工具注册表
        self.subagents = SubagentManager(...)       # 子 Agent 管理器
        self._register_default_tools()              # 注册默认工具
```

---

## 3. 主监听循环 `run()`

```python
async def run(self) -> None:
    self._running = True
    while self._running:
        try:
            # 每1秒超时，让循环可以检查 _running 状态
            msg = await asyncio.wait_for(
                self.bus.consume_inbound(), timeout=1.0
            )
        except asyncio.TimeoutError:
            continue   # 没消息就继续等

        if msg.content.strip().lower() == "/stop":
            await self._handle_stop(msg)
        else:
            # 关键：create_task 不阻塞，主循环立即继续监听
            task = asyncio.create_task(self._dispatch(msg))
```

**核心设计**：`create_task` vs `await` 的区别：
- `await _dispatch(msg)`：等这条消息处理完才接下一条（单线程阻塞）
- `create_task(_dispatch(msg))`：立即返回，后台异步处理（并发响应多用户）

---

## 4. 工具调用循环 `_run_agent_loop()`（最核心）

ReAct 模式：**Re**asoning + **Act**ing

```python
async def _run_agent_loop(self, initial_messages):
    messages = initial_messages
    while iteration < self.max_iterations:
        # 1. 调 LLM
        response = await self.provider.chat(
            messages=messages,
            tools=self.tools.get_definitions(),  # 告诉 LLM 有哪些工具
        )

        if response.has_tool_calls:
            # 2a. LLM 要用工具 → 执行 → 把结果追加到 messages → 再次调 LLM
            for tool_call in response.tool_calls:
                result = await self.tools.execute(
                    tool_call.name, tool_call.arguments
                )
                messages = self.context.add_tool_result(messages, ...)
        else:
            # 2b. LLM 直接回答 → 结束循环
            final_content = response.content
            break

    return final_content, tools_used, messages
```

流程图：
```
LLM 回复
  ├─ 有工具调用 → 执行工具 → 追加结果 → 再调 LLM ← 循环（最多40次）
  └─ 直接回答  → 结束，返回
```

---

## 5. 单条消息处理 `_process_message()`

```python
async def _process_message(self, msg: InboundMessage):
    key = msg.session_key                        # "telegram:123456"
    session = self.sessions.get_or_create(key)  # 取出或新建会话
    history = session.get_history(max_messages=self.memory_window)

    # 构建完整消息列表
    initial_messages = self.context.build_messages(
        history=history,
        current_message=msg.content,
        channel=msg.channel, chat_id=msg.chat_id,
    )

    # 进入工具调用循环
    final_content, _, all_msgs = await self._run_agent_loop(initial_messages)

    # 保存本轮到 session
    self._save_turn(session, all_msgs, ...)
    self.sessions.save(session)

    return OutboundMessage(channel=..., content=final_content)
```

---

## 6. 几个巧妙的设计细节

### ① 自动记忆压缩（后台异步）
```python
if unconsolidated >= self.memory_window:
    # 后台异步触发，不阻塞当前消息处理
    asyncio.create_task(_consolidate_and_unlock())
```

### ② 工具结果截断（防上下文爆炸）
```python
_TOOL_RESULT_MAX_CHARS = 500
# 超过 500 字符的工具结果，保存到 session 时截断
```

### ③ CLI 直接处理（绕过总线）
```python
async def process_direct(self, content: str, ...) -> str:
    """CLI 和 cron 使用。直接调 _process_message，不经过队列。"""
    msg = InboundMessage(channel="cli", ...)
    response = await self._process_message(msg, ...)
    return response.content if response else ""
```

### ④ `/stop` 命令取消 asyncio Task
```python
async def _handle_stop(self, msg):
    tasks = self._active_tasks.pop(msg.session_key, [])
    for t in tasks:
        t.cancel()   # asyncio Task 可以被取消
```

---

## 7. Python 语法要点

| 语法 | 用法 |
|------|------|
| `asyncio.wait_for(coro, timeout=1.0)` | 带超时的协程等待 |
| `asyncio.create_task(coro)` | 创建后台并发任务，立即返回 |
| `task.cancel()` | 取消正在运行的异步任务 |
| `weakref.WeakValueDictionary` | 值被 GC 时自动删除，防 Lock 对象内存泄漏 |
| `TYPE_CHECKING` 块 | 仅类型检查时导入，避免循环导入 |
| `@staticmethod` | 不需要 `self` 的工具函数 |
| 列表推导式 | 构建 `tool_call_dicts` |

---

## 8. 完整调用链（一次消息的旅程）

```
bus.consume_inbound()
  → AgentLoop.run()
    → asyncio.create_task(_dispatch(msg))
      → _dispatch() ─→ _process_message()
          ├─ sessions.get_or_create()        # 取历史
          ├─ context.build_messages()        # 拼上下文
          └─ _run_agent_loop()              # LLM + 工具循环
              ├─ provider.chat()            # 调 LLM
              ├─ tools.execute()            # 执行工具
              └─ 循环 → final_content
          ├─ _save_turn()                   # 保存到 session
          └─ bus.publish_outbound(response) # 发回总线
```

---

*笔记日期：2026-03-05*
