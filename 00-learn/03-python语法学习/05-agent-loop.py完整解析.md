# 05 agent/loop.py 完整解析

> 对应代码：`nanobot/agent/loop.py`（510 行，整个项目的"大脑核心"）
>
> 这是最核心的文件，理解它就理解了 AI Agent 的本质。

---

## 文件结构一览

```
loop.py
│
├── [导入区]                       第 1-32 行
│
├── class AgentLoop                第 35-509 行   核心类
│   │
│   ├── __init__()                 第 49-113 行   初始化所有组件
│   ├── _register_default_tools()  第 115-131 行  注册内置工具
│   ├── _connect_mcp()             第 133-153 行  连接外部 MCP 工具服务器（懒加载）
│   ├── _set_tool_context()        第 155-160 行  给工具设置当前会话的路由信息
│   ├── _strip_think()             第 162-167 行  去掉模型回复中的 <think> 块
│   ├── _tool_hint()               第 169-178 行  格式化"正在调用工具"的提示文字
│   │
│   ├── _run_agent_loop()          第 180-257 行  ★ 核心：AI 推理+工具调用循环
│   │
│   ├── run()                      第 259-276 行  从 bus 持续接收消息，分发处理
│   ├── _handle_stop()             第 278-292 行  处理 /stop 命令，取消进行中的任务
│   ├── _dispatch()                第 294-314 行  拿到消息后获取锁、调用处理函数
│   │
│   ├── _process_message()         第 330-453 行  ★ 单条消息完整处理流程
│   ├── _save_turn()               第 455-488 行  把本轮对话保存进 session
│   ├── _consolidate_memory()      第 490-495 行  触发长期记忆压缩（委托给 MemoryStore）
│   │
│   ├── process_direct()           第 497-509 行  CLI 直接调用（跳过 bus）
│   ├── close_mcp()                第 316-323 行  关闭 MCP 连接
│   └── stop()                     第 325-328 行  停止 agent 主循环
```

---

## 一、docstring 自述（直接讲清楚了自己是什么）

```python
class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """
```

五步就是 Agent 的全部工作，后面所有代码都在实现这五步。

---

## 二、`__init__()` — 初始化（第 49-113 行）

```python
def __init__(
    self,
    bus: MessageBus,
    provider: LLMProvider,
    workspace: Path,
    model: str | None = None,
    max_iterations: int = 40,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    memory_window: int = 100,
    reasoning_effort: str | None = None,
    brave_api_key: str | None = None,
    exec_config: ExecToolConfig | None = None,
    cron_service: CronService | None = None,
    session_manager: SessionManager | None = None,
    mcp_servers: dict | None = None,
    channels_config: ChannelsConfig | None = None,
):
    self.bus = bus
    self.provider = provider
    self.model = model or provider.get_default_model()
    ...
    self.context = ContextBuilder(workspace)
    self.sessions = session_manager or SessionManager(workspace)
    self.tools = ToolRegistry()
    self.subagents = SubagentManager(...)
    
    self._running = False
    self._processing_lock = asyncio.Lock()
    self._register_default_tools()
```

### 功能/架构

`__init__` 把所有零件装配在一起：

| 属性 | 类型 | 作用 |
|------|------|------|
| `bus` | `MessageBus` | 消息通道（收发消息的快递系统）|
| `provider` | `LLMProvider` | AI 模型接口（调用 GPT/Claude 等）|
| `context` | `ContextBuilder` | 组装发给 LLM 的消息列表（历史+当前）|
| `sessions` | `SessionManager` | 管理每个用户的对话历史 |
| `tools` | `ToolRegistry` | 工具注册表（知道有哪些工具、怎么调用）|
| `subagents` | `SubagentManager` | 管理派生的子 Agent |
| `_processing_lock` | `asyncio.Lock` | 互斥锁，防止并发处理同一个会话 |

### Python 语法：`__init__` 方法

```python
class AgentLoop:
    def __init__(self, bus, provider, ...):
        self.bus = bus       # 把参数存为实例属性
        self.provider = provider
```

- `__init__` 是类的**构造函数**，创建实例时自动调用
- `self` 指向正在被创建的那个实例本身
- `self.xxx = yyy` 把值存为实例属性，后续 `实例.xxx` 可以访问

### Python 语法：`asyncio.Lock` 互斥锁

```python
self._processing_lock = asyncio.Lock()
```

场景：如果两条消息同时到达，两个协程可能同时修改同一个 session，造成数据混乱。

`Lock` 保证一次只有一个协程能进入"临界区"：

```python
async with self._processing_lock:
    # 只有拿到锁的协程才能执行这里
    response = await self._process_message(msg)
    # 执行完后锁自动释放，下一个协程才能进来
```

类比：厕所只有一个坑，门锁保证一次只有一个人进去。

---

## 三、`_register_default_tools()` — 注册工具（第 115-131 行）

```python
def _register_default_tools(self) -> None:
    for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
        self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
    self.tools.register(ExecTool(...))          # 执行 shell 命令
    self.tools.register(WebSearchTool(...))     # 搜索网页
    self.tools.register(WebFetchTool(...))      # 抓取网页内容
    self.tools.register(MessageTool(...))       # 发消息给用户
    self.tools.register(SpawnTool(...))         # 创建子 Agent
    if self.cron_service:
        self.tools.register(CronTool(...))      # 管理定时任务
```

### 功能/架构

这里注册了 Agent 可以使用的全部**内置工具**。工具就是 AI 可以调用来"做事"的函数。

注册完后，这些工具的描述（名称、参数、功能说明）会在每次调用 LLM 时一起发过去，LLM 收到后知道"我可以用这些工具"。

### Python 语法：`for cls in (类1, 类2, ...)` 遍历类

```python
for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
    self.tools.register(cls(workspace=self.workspace))
```

- 圆括号里的是一个**元组**，存放的是**类本身**（不是实例）
- 循环每次拿到一个类，调用 `cls(workspace=...)` 创建实例，再注册
- 等价于手动写四遍 `self.tools.register(ReadFileTool(...))` 等，但更简洁

---

## 四、`_run_agent_loop()` ★ 核心循环（第 180-257 行）

**这是整个项目最重要的函数。**

```python
async def _run_agent_loop(
    self,
    initial_messages: list[dict],
    on_progress: Callable[..., Awaitable[None]] | None = None,
) -> tuple[str | None, list[str], list[dict]]:
    messages = initial_messages
    iteration = 0
    final_content = None
    tools_used: list[str] = []

    while iteration < self.max_iterations:    # 最多循环 40 次
        iteration += 1

        # ① 调用 LLM
        response = await self.provider.chat(
            messages=messages,
            tools=self.tools.get_definitions(),   # 把所有工具描述发给 LLM
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        if response.has_tool_calls:
            # ② LLM 决定调用工具
            if on_progress:
                await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

            # 把 LLM 的"想法"（含工具调用意图）加进对话历史
            messages = self.context.add_assistant_message(messages, ...)

            # ③ 逐个执行工具
            for tool_call in response.tool_calls:
                tools_used.append(tool_call.name)
                result = await self.tools.execute(tool_call.name, tool_call.arguments)
                # ④ 把工具结果加回对话历史
                messages = self.context.add_tool_result(messages, tool_call.id, ...)
            
            # ⑤ 带工具结果再次调用 LLM → 回到 while 顶部
        else:
            # LLM 直接给出最终答案
            final_content = self._strip_think(response.content)
            break   # 退出循环

    return final_content, tools_used, messages
```

### 功能/架构：这个 while 循环就是 AI Agent 的本质

```
┌─────────────────────────────────────────────────────┐
│                    while 循环                        │
│                                                     │
│  messages = [系统提示 + 历史对话 + 用户消息]         │
│       ↓                                             │
│  provider.chat(messages, tools)  ← 调用 AI          │
│       ↓                                             │
│  AI 回复有工具调用？                                 │
│    ├─ YES → 执行工具 → 把结果加进 messages           │
│    │        → 继续 while（带工具结果再问 AI）←─────┐│
│    │                                              ││
│    └─ NO  → final_content = AI 的文字回答          ││
│             break 退出循环                          ││
│                                                   ││
│  超过 40 次 → 强制退出，返回"达到上限"提示          ││
└─────────────────────────────────────────────────────┘
```

**举个例子**，你问 "今天北京天气怎么样"：

```
第1轮：AI 说"我需要搜索天气" → 调用 web_search("北京天气")
第2轮：工具返回天气数据 → AI 说"北京今天晴，25°C" → break
```

总共循环了 2 次，用了 1 个工具。

### Python 语法：`Callable[..., Awaitable[None]]` 类型注解

```python
on_progress: Callable[..., Awaitable[None]] | None = None
```

- `Callable`：可以被调用的对象（函数、方法等）
- `[..., Awaitable[None]]`：接受任意参数，返回一个可以被 await 的对象
- 简单理解：`on_progress` 是一个**异步函数**，可以传入也可以不传（`| None`）
- 作用：进度回调，每次调用工具时通知外部"正在做什么"

### Python 语法：`list[dict]` 消息格式

LLM 的对话历史是一个字典列表，每条消息长这样：

```python
messages = [
    {"role": "system", "content": "你是一个 AI 助手..."},
    {"role": "user",   "content": "今天北京天气怎么样"},
    {"role": "assistant", "content": None, "tool_calls": [...]},  # AI 说要调工具
    {"role": "tool",   "content": "晴，25°C", "tool_call_id": "xxx"},  # 工具结果
    {"role": "assistant", "content": "今天北京晴，25°C"},  # AI 最终回答
]
```

每次循环都往这个列表里追加新内容，所以 LLM 每次都能看到完整的对话历史。

---

## 五、`run()` — 主循环监听 bus（第 259-276 行）

```python
async def run(self) -> None:
    self._running = True
    await self._connect_mcp()    # 连接外部工具服务器

    while self._running:
        try:
            msg = await asyncio.wait_for(
                self.bus.consume_inbound(),
                timeout=1.0    # 1 秒超时，避免永久阻塞
            )
        except asyncio.TimeoutError:
            continue           # 超时了没消息，继续等

        if msg.content.strip().lower() == "/stop":
            await self._handle_stop(msg)
        else:
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
```

### 功能/架构

这个函数是 Agent 的**主监听循环**，在 `gateway` 模式时一直运行：

```
while 一直循环:
    等待 bus 里有新消息（最多等 1 秒）
    ├─ 超时了 → 继续等
    ├─ 收到 /stop → 取消正在进行的任务
    └─ 收到普通消息 → 创建异步 Task 去处理（不阻塞主循环）
```

**为什么 dispatch 要用 `asyncio.create_task()`？**

因为处理一条消息可能需要几十秒（调多个工具，等 LLM 响应），如果同步等待，在这期间收到的新消息就会全部堆积等待。用 `create_task` 把处理任务扔到后台，主循环立刻返回继续等下一条消息。

### Python 语法：`asyncio.wait_for(协程, timeout=秒数)`

```python
msg = await asyncio.wait_for(
    self.bus.consume_inbound(),
    timeout=1.0
)
```

- 给协程设置超时时间
- 超时未完成则抛出 `asyncio.TimeoutError`
- 这里用来让 `while` 循环每秒检查一次 `self._running` 状态，如果 `stop()` 被调用就能退出

### Python 语法：`dict.setdefault(key, default)`

```python
self._active_tasks.setdefault(msg.session_key, []).append(task)
```

- `setdefault(key, default)`：如果 `key` 不存在，先设置为 `default` 再返回；如果存在，直接返回现有值
- 等价于：

```python
if msg.session_key not in self._active_tasks:
    self._active_tasks[msg.session_key] = []
self._active_tasks[msg.session_key].append(task)
```

用来追踪每个 session 的活跃任务，`/stop` 命令需要这个列表来取消任务。

---

## 六、`_process_message()` ★ 单条消息处理（第 330-453 行）

```python
async def _process_message(
    self,
    msg: InboundMessage,
    session_key: str | None = None,
    on_progress: Callable | None = None,
) -> OutboundMessage | None:

    key = session_key or msg.session_key
    session = self.sessions.get_or_create(key)   # 找或创建 session

    # 特殊命令处理
    cmd = msg.content.strip().lower()
    if cmd == "/new":    # 开新会话
        session.clear()
        return OutboundMessage(..., content="New session started.")
    if cmd == "/help":   # 显示帮助
        return OutboundMessage(..., content="🐈 nanobot commands:\n/new — ...")

    # 触发记忆压缩（后台异步，不阻塞当前处理）
    if unconsolidated >= self.memory_window and session.key not in self._consolidating:
        asyncio.create_task(_consolidate_and_unlock())

    # 给工具设置当前消息的路由信息
    self._set_tool_context(msg.channel, msg.chat_id, ...)

    # 从 session 取历史，拼装消息列表
    history = session.get_history(max_messages=self.memory_window)
    initial_messages = self.context.build_messages(
        history=history,
        current_message=msg.content,
        channel=msg.channel,
        chat_id=msg.chat_id,
    )

    # ★ 调用核心循环
    final_content, _, all_msgs = await self._run_agent_loop(
        initial_messages, on_progress=on_progress or _bus_progress,
    )

    # 保存本轮对话到 session
    self._save_turn(session, all_msgs, 1 + len(history))
    self.sessions.save(session)

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=final_content,
    )
```

### 功能/架构

这是 `_run_agent_loop` 的"上一层包装"，负责：

```
1. 找到对应的 session（保证对话历史正确）
2. 处理特殊命令（/new /help）
3. 必要时触发记忆压缩（历史太长时）
4. 调用 _run_agent_loop 做真正的 AI 处理
5. 把新的对话内容存回 session
6. 打包成 OutboundMessage 返回
```

### Python 语法：`session_key or msg.session_key`

```python
key = session_key or msg.session_key
```

- 如果外部传入了 `session_key`，就用它
- 如果没传（None），就用消息自带的 `session_key`
- `msg.session_key` 通常是 `"channel:chat_id"` 格式，比如 `"telegram:123456"`

### Python 语法：内嵌异步函数作为闭包

```python
async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
    meta = dict(msg.metadata or {})
    meta["_progress"] = True
    await self.bus.publish_outbound(OutboundMessage(...))
```

- 定义在 `_process_message` 内部，能访问外层的 `msg`、`self.bus` 等变量
- `*` 之后的参数必须用关键字传递：`_bus_progress("内容", tool_hint=True)`
- 每次 AI 调用工具时，`_run_agent_loop` 会调用这个回调，通过 bus 给用户发进度提示

---

## 七、`process_direct()` — CLI 直接调用（第 497-509 行）

```python
async def process_direct(
    self,
    content: str,
    session_key: str = "cli:direct",
    channel: str = "cli",
    chat_id: str = "direct",
    on_progress: Callable | None = None,
) -> str:
    await self._connect_mcp()
    msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
    response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
    return response.content if response else ""
```

### 功能/架构

这是 `nanobot agent -m "xxx"` 走的路径。

直接：
1. 构造一条 `InboundMessage`（跳过 bus 队列）
2. 调用 `_process_message()` 处理
3. 返回字符串（而不是 `OutboundMessage`）

和 `run()` 的区别：  
`run()` 是持续运行、从 bus 读消息；  
`process_direct()` 是一次性调用、直接传字符串进来。

---

## 八、`_save_turn()` — 保存对话（第 455-488 行）

```python
def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
    for m in messages[skip:]:    # 只保存本轮新增的消息
        entry = dict(m)
        role, content = entry.get("role"), entry.get("content")

        # 跳过空的 assistant 消息
        if role == "assistant" and not content and not entry.get("tool_calls"):
            continue

        # 工具结果太长则截断（最多 500 字符）
        if role == "tool" and isinstance(content, str) and len(content) > 500:
            entry["content"] = content[:500] + "\n... (truncated)"

        # 去掉 user 消息里的运行时上下文前缀（只保留用户真正说的话）
        if role == "user" and isinstance(content, str):
            if content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                parts = content.split("\n\n", 1)
                if len(parts) > 1:
                    entry["content"] = parts[1]

        entry.setdefault("timestamp", datetime.now().isoformat())
        session.messages.append(entry)
```

### 功能/架构

每轮对话结束后，把新消息追加进 session。有两个重要的清洗操作：

1. **工具结果截断**：工具执行结果可能很长（如读了一个大文件），存 500 字符够 AI 理解就行，不浪费存储
2. **去掉运行时上下文**：每条 user 消息前面都有一段"当前时间/系统信息"前缀，存档时去掉，只保留用户的真实输入

### Python 语法：`messages[skip:]` 切片

```python
for m in messages[skip:]:
```

- `列表[start:]` 从 `start` 索引到末尾
- `skip` 是原来历史消息的数量，`messages[skip:]` 就是本轮新增的消息
- 只保存新增的，不重复保存历史

### Python 语法：`entry.setdefault("timestamp", ...)`

```python
entry.setdefault("timestamp", datetime.now().isoformat())
```

- `dict.setdefault(key, value)`：如果 `key` 不存在才设置，已存在则不动
- 给每条消息打上时间戳，但不覆盖已有的

---

## 九、整体处理流程图（完整版）

```
用户发消息
    │
    ▼
[bus] 或 [process_direct]
    │
    ▼
AgentLoop.run() + _dispatch()
    │  获取 _processing_lock（同一时间只处理一条）
    ▼
_process_message(msg)
    ├─ 找/建 session（取历史对话）
    ├─ 处理 /new /help 特殊命令
    ├─ ContextBuilder.build_messages()（历史 + 当前消息）
    ▼
_run_agent_loop(messages)
    ├─ while 循环（最多 40 次）：
    │     provider.chat() → LLM 回复
    │       ├─ 有 tool_calls → tools.execute() → 结果加入 messages → 继续
    │       └─ 纯文字 → break
    ▼
返回 final_content
    │
    ▼
_save_turn()（新对话追加到 session，截断过长结果）
sessions.save()（持久化到磁盘）
    │
    ▼
OutboundMessage → bus → channel → 用户
```

---

## 十、Python 语法汇总

| 语法 | 代码示例 | 含义 |
|------|---------|------|
| `__init__` | `def __init__(self, ...)` | 构造函数，创建实例时自动调用 |
| `asyncio.Lock` | `asyncio.Lock()` | 异步互斥锁，防止并发冲突 |
| `async with lock` | `async with self._processing_lock` | 异步上下文管理器，进入时获锁，退出时释锁 |
| `asyncio.wait_for` | `wait_for(协程, timeout=1.0)` | 给协程设超时，超时抛 TimeoutError |
| `asyncio.create_task` | `create_task(协程)` | 把协程扔到后台并发执行 |
| `dict.setdefault` | `d.setdefault(key, default)` | 不存在时才设置 |
| `[skip:]` 切片 | `messages[skip:]` | 从索引 skip 到末尾 |
| `Callable` 类型 | `Callable[..., Awaitable[None]]` | 异步回调函数的类型注解 |
| `*` 关键字参数 | `def f(x, *, y)` | `y` 必须用关键字传递 |
| 内嵌闭包函数 | 函数内定义函数 | 可捕获外层变量 |
