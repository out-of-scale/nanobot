# 06 — 记忆管理（nanobot/agent/memory.py）

> 文件路径：`nanobot/agent/memory.py`（151 行）  
> 核心类：`MemoryStore`

---

## 1. 两层记忆结构

```
workspace/memory/
├── MEMORY.md    ← 长期记忆（重要事实、用户偏好、关键信息）
└── HISTORY.md   ← 历史日志（每段对话的2-5句摘要，可 grep 搜索）
```

---

## 2. 核心方法一览

```python
class MemoryStore:
    def read_long_term(self) -> str:      # 读取 MEMORY.md
    def write_long_term(self, content):   # 覆写 MEMORY.md
    def append_history(self, entry):      # 追加到 HISTORY.md
    def get_memory_context(self) -> str:  # 给 ContextBuilder 用（返回格式化内容）
    async def consolidate(self, session, provider, model, ...) -> bool:  # 压缩旧消息
```

---

## 3. 记忆压缩流程：`consolidate()`

**触发时机**：`loop.py` 中，未压缩消息数超过 `memory_window`（默认100）时后台触发。

```python
# 普通压缩（保留最近半窗口消息）
keep_count   = memory_window // 2      # 保留 50 条最新消息
old_messages = session.messages[session.last_consolidated:-keep_count]  # 待压缩部分
```

压缩流程：
```
旧消息格式化为文本
    ↓
调用 LLM（专用的"记忆压缩 Agent"）
    ↓
LLM 调用 save_memory 工具，返回：
  ├─ history_entry：这段对话的摘要 → 追加到 HISTORY.md
  └─ memory_update：更新后的完整长期记忆 → 覆写 MEMORY.md
```

---

## 4. 最妙的设计：用工具调用约束 LLM 输出格式

```python
_SAVE_MEMORY_TOOL = [{
    "type": "function",
    "function": {
        "name": "save_memory",
        "parameters": {
            "required": ["history_entry", "memory_update"],  # 必须返回这两个字段
        }
    }
}]
```

这不是真正的"工具"，而是一个技巧：**用工具调用 schema 强迫 LLM 返回结构化数据**，比解析自由文本可靠得多。

`heartbeat/service.py` 里的 `_HEARTBEAT_TOOL` 也是同样的技巧（强迫 LLM 返回 `skip` 或 `run`）。

---

## 5. `/new` 命令触发全量压缩

用户发 `/new` 时，`loop.py` 调用 `consolidate(archive_all=True)`：

```python
# archive_all=True 时压缩所有消息，然后清空 session
old_messages = session.messages  # 全部消息
keep_count = 0                   # 不保留任何消息
```

---

*笔记日期：2026-03-05*
