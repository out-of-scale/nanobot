# 07 — 会话管理（nanobot/session/manager.py）

> 文件路径：`nanobot/session/manager.py`（213 行）  
> 核心类：`Session`、`SessionManager`

---

## 1. 这个文件做什么？

把每次对话的消息**持久化到磁盘**，让 nanobot 重启后历史不丢失。

---

## 2. 存储格式：JSONL

每个用户（session key）对应一个 `.jsonl` 文件，每行一个 JSON：

```
workspace/sessions/
└── telegram_123456.jsonl   ← 文件名 = session_key（冒号换下划线）
```

文件内容（每行一条 JSON）：

```jsonl
{"_type": "metadata", "key": "telegram:123456", "created_at": "...", "last_consolidated": 50}
{"role": "user", "content": "你好", "timestamp": "2026-03-05T..."}
{"role": "assistant", "content": "你好！", "timestamp": "2026-03-05T..."}
```

> 第一行是元数据，后面每行是一条消息。列出所有会话时只需读第一行，极其高效。

---

## 3. Session（数据容器）

```python
@dataclass
class Session:
    key: str                          # "telegram:123456"
    messages: list[dict] = field(default_factory=list)
    last_consolidated: int = 0        # 指针：已压缩到 memory 的消息数

    def get_history(self, max_messages=500) -> list[dict]:
        """取出未压缩的最近 N 条，且保证从 user 消息开头"""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]
        # 跳过开头的非 user 消息（防止孤立的 tool_result 块）
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                return sliced[i:]
```

**重点**：`last_consolidated` 是游标，记录哪些消息已被 `memory.py` 压缩归档，下次压缩从这里开始，避免重复压缩。

---

## 4. SessionManager（管理器）

```python
class SessionManager:
    def __init__(self, workspace: Path):
        self._cache: dict[str, Session] = {}  # 内存缓存

    def get_or_create(self, key: str) -> Session:
        if key in self._cache: return self._cache[key]  # 1. 查缓存
        session = self._load(key)                        # 2. 读磁盘
        if session is None: session = Session(key=key)  # 3. 新建
        self._cache[key] = session
        return session

    def save(self, session: Session) -> None:
        # 第一行写元数据，后续每行写一条消息
        f.write(json.dumps(metadata_line) + "\n")
        for msg in session.messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
```

---

## 5. 自动迁移旧数据

```python
# 旧路径：~/.nanobot/sessions/
# 新路径：workspace/sessions/
# 首次加载时自动 shutil.move() 迁移
```

---

*笔记日期：2026-03-05*
