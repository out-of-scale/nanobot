# 03 — 消息总线（nanobot/bus/）

> 模块路径：`nanobot/bus/`  
> 文件三件套：`__init__.py` · `events.py` · `queue.py`  
> 核心作用：**解耦** 各聊天渠道（Telegram、Discord…）与 Agent 处理核心

---

## 1. 为什么需要消息总线？

没有消息总线时，渠道需要直接调用 Agent，Agent 也需要直接回调渠道——双方紧密耦合，添加新渠道时改动巨大。

```
【没有总线】                   【有了总线】
Channel ──→ AgentLoop          Channel ──→ [Bus] ←── AgentLoop
AgentLoop ──→ Channel                           ↕
(互相依赖，难扩展)              Channel ←── [Bus] ──→ AgentLoop
                               (各自只认识 Bus，互不依赖)
```

**设计模式**：生产者-消费者（Producer-Consumer），通过异步队列传递消息，双方完全解耦。

---

## 2. 文件结构一览

```
bus/
├── __init__.py   ← 公开导出 MessageBus、InboundMessage、OutboundMessage
├── events.py     ← 定义两种消息数据类（dataclass）
└── queue.py      ← 实现 MessageBus 队列类
```

---

## 3. events.py — 消息数据结构

### 3.1 InboundMessage（入站消息）

渠道 → Agent，来自用户的消息。

```python
@dataclass
class InboundMessage:
    channel: str          # 渠道名，如 "telegram"、"discord"
    sender_id: str        # 发送者 ID（用户标识）
    chat_id: str          # 聊天/频道 ID
    content: str          # 消息正文
    timestamp: datetime = field(default_factory=datetime.now)  # 自动填入当前时间
    media: list[str] = field(default_factory=list)             # 媒体 URL 列表
    metadata: dict[str, Any] = field(default_factory=dict)     # 渠道私有附加数据
    session_key_override: str | None = None                    # 线程级会话 key 覆盖

    @property
    def session_key(self) -> str:
        """会话唯一标识，默认格式 'channel:chat_id'，可被线程覆盖。"""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"
```

**关键设计**：
- `session_key` 是 `@property`（只读计算属性），不需要手动维护，自动由 `channel+chat_id` 生成
- `session_key_override` 让 Slack Thread、Discord Thread 等线程机制可以创建独立会话

### 3.2 OutboundMessage（出站消息）

Agent → 渠道，发给用户的回复。

```python
@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None        # 可回复某条消息
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

---

## 4. queue.py — MessageBus 队列

```python
class MessageBus:
    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()   # 入站队列
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue() # 出站队列

    # 渠道调用：把用户消息放入队列
    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    # Agent 调用：阻塞等待并取出消息
    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    # Agent 调用：把回复放入队列
    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    # 渠道调用：阻塞等待并取出回复
    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:   # 查看入站队列积压量
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:  # 查看出站队列积压量
        return self.outbound.qsize()
```

**核心**：`asyncio.Queue` 是 Python 标准库的异步队列，`put()` 和 `get()` 都是协程，天然支持并发。

---

## 5. 完整数据流（结合 loop.py 和 base.py）

```
用户发消息（Telegram等）
       ↓
BaseChannel._handle_message()    ← 权限检查（is_allowed）
       ↓ 构造 InboundMessage
bus.publish_inbound(msg)          ← 放入 inbound 队列
       ↓
AgentLoop.run() 主循环
  msg = await bus.consume_inbound()   ← 阻塞取出（超时1秒重试）
       ↓
asyncio.create_task(_dispatch(msg))   ← 创建异步任务，不阻塞主循环
       ↓
_process_message(msg)
  → 构建上下文
  → 调用 LLM（可能多轮工具调用）
  → 构造 OutboundMessage
       ↓
bus.publish_outbound(response)    ← 放入 outbound 队列
       ↓
Channel.consume_outbound() 取出
  → channel.send(msg)            ← 发送给用户
```

### 5.1 特殊：进度消息（_bus_progress）

LLM 思考过程中（工具调用之间），也会通过总线推送"正在进行"提示：

```python
async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
    meta = dict(msg.metadata or {})
    meta["_progress"] = True        # 标记为进度消息，渠道可选择特殊展示
    meta["_tool_hint"] = tool_hint  # 标记是否为工具调用提示
    await self.bus.publish_outbound(OutboundMessage(..., metadata=meta))
```

---

## 6. Python 语法要点（本模块涉及）

### 6.1 `@dataclass` 与 `field()`

```python
from dataclasses import dataclass, field

@dataclass
class InboundMessage:
    # 普通字段（必须提供初始值或在构造时传入）
    channel: str

    # 带默认工厂的字段：每个实例独立创建新列表，避免共享同一个列表对象
    media: list[str] = field(default_factory=list)
    #                         ↑ 不能写 media: list[str] = []，这是陷阱！
```

> ⚠️ **经典陷阱**：类属性用可变对象（`[]`、`{}`）做默认值，所有实例共享同一个对象！
> 必须用 `field(default_factory=list)` 让每个实例各自创建新列表。

### 6.2 `asyncio.Queue`

```python
import asyncio

q: asyncio.Queue[int] = asyncio.Queue()
await q.put(42)    # 生产者：放入（如果队列满则等待）
val = await q.get() # 消费者：取出（如果队列空则等待）
q.qsize()           # 查看当前队列长度（非阻塞）
```

### 6.3 `@property`（计算属性）

```python
@property
def session_key(self) -> str:
    return self.session_key_override or f"{self.channel}:{self.chat_id}"

# 使用时像访问普通属性，不需要加括号：
msg.session_key  # ✅ 而不是 msg.session_key()
```

### 6.4 `asyncio.wait_for()` + 超时

```python
# 来自 loop.py
msg = await asyncio.wait_for(
    self.bus.consume_inbound(),
    timeout=1.0   # 超过1秒没消息就抛出 TimeoutError，让循环保持响应
)
```

### 6.5 `asyncio.create_task()` 并发

```python
# AgentLoop.run() 中
task = asyncio.create_task(self._dispatch(msg))
# 立即返回，不等待任务完成 → 主循环可以继续接收新消息
# 多个用户同时发消息也不会互相阻塞
```

---

## 7. 一句话总结

> **MessageBus 是 nanobot 的"快递中转站"**：渠道把用户消息打包（`InboundMessage`）投入入站队列，Agent 从队列取出处理后把回复打包（`OutboundMessage`）投入出站队列，渠道再取出发给用户。双方只和队列对话，彼此完全解耦。

---

## 8. 复习提问

1. `InboundMessage.session_key` 是方法还是属性？怎么调用？
2. 为什么 `media` 字段用 `field(default_factory=list)` 而不是 `= []`？
3. `asyncio.Queue` 的 `get()` 在队列为空时会怎样？
4. `AgentLoop.run()` 中为什么要用 `asyncio.create_task` 而不是直接 `await _dispatch(msg)`？
5. 出站消息中的 `_progress` 元数据有什么用？

---

*笔记日期：2026-03-03*
