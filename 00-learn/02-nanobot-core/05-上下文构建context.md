# 05 — 上下文构建器（nanobot/agent/context.py）

> 文件路径：`nanobot/agent/context.py`（174 行）  
> 核心类：`ContextBuilder`

---

## 1. 这个文件做什么？

把"系统提示 + 历史记录 + 当前消息"拼成完整的消息列表，交给 LLM。

---

## 2. 发给 LLM 的消息结构

```python
def build_messages(self, history, current_message, media, channel, chat_id):
    return [
        {"role": "system",  "content": build_system_prompt()},  # ① 系统提示
        *history,                                                # ② 历史对话（展开）
        {"role": "user",    "content": runtime_ctx + 用户消息},  # ③ 当前消息
    ]
```

每次调 LLM 永远是这三层结构。

---

## 3. 系统提示的组成：`build_system_prompt()`

```
_get_identity()            ← 角色定义 + workspace 路径 + 行为准则（硬编码）
  +
_load_bootstrap_files()   ← 读取 AGENTS.md、SOUL.md、USER.md 等（用户自定义）
  +
memory.get_memory_context() ← MEMORY.md 里的长期记忆
  +
skills（always=true）     ← 常驻技能全文
  +
skills 摘要目录            ← 其他技能名称+描述（Agent 按需 read_file 读详情）
```

**设计亮点**：用户在 workspace 里放 `SOUL.md` 就能改 Agent 性格，放 `USER.md` 就能写自我介绍——系统提示是**动态构建**的。

---

## 4. 运行时上下文（Runtime Context）

每条用户消息前都悄悄插入当前时间和渠道信息：

```python
_RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

# 插入的内容：
"""
[Runtime Context — metadata only, not instructions]
Current Time: 2026-03-05 11:34 (Thursday)
Channel: telegram
Chat ID: 123456
"""
```

> 这就是 nanobot 知道"今天星期几"的原因——每条消息时注入，不是 LLM 猜的。

**重要**：保存到 session 时，`_save_turn()` 会把这段元数据**剥离**，不污染历史记录。

---

## 5. 图片处理：`_build_user_content()`

消息带图片时，把图片转为 base64 内嵌：

```python
b64 = base64.b64encode(p.read_bytes()).decode()
# 发给 LLM 的格式：
{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
```

---

## 6. 工具结果追加：`add_tool_result()` / `add_assistant_message()`

```python
# 工具结果追加（在 loop.py 的工具调用循环里调用）
def add_tool_result(self, messages, tool_call_id, tool_name, result):
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": result
    })
    return messages
```

---

## 7. 关键常量

```python
BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
# 按顺序查找，存在就读入，不存在跳过
```

---

*笔记日期：2026-03-05*
