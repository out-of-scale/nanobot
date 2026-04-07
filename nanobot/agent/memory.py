"""Memory system for persistent agent memory."""
# 持久化记忆系统：负责将对话历史压缩并写入磁盘，供后续对话读取。

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session


# ──────────────────────────────────────────────────────────────────────────────
# save_memory 工具定义
# 该工具定义以 OpenAI Function Calling 格式描述了一个虚拟工具。
# 在记忆整合时，代理（LLM）会被要求调用此工具，将压缩后的
# 历史摘要（history_entry）和更新后的长期记忆（memory_update）
# 作为参数返回，从而以结构化方式提取 LLM 输出的两个关键字段。
# ──────────────────────────────────────────────────────────────────────────────
_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        # 2-5 句话的段落，概括本次对话的关键事件/决策/话题，
                        # 以 [YYYY-MM-DD HH:MM] 时间戳开头，便于 grep 搜索。
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        # 完整的长期记忆 markdown 文本，包含所有已知事实加上本次新增内容。
                        # 若无新内容则原样返回。
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log).

    双层记忆设计：
      - MEMORY.md：长期事实库，以 markdown 格式持续积累关键知识。
      - HISTORY.md：可 grep 检索的历史日志，按时间戳追加写入。
    """

    def __init__(self, workspace: Path):
        # 确保 memory/ 目录存在（不存在则自动创建）
        self.memory_dir = ensure_dir(workspace / "memory")
        # 长期记忆文件路径
        self.memory_file = self.memory_dir / "MEMORY.md"
        # 历史日志文件路径
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        """读取 MEMORY.md 的全部内容；文件不存在则返回空字符串。"""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        """将新内容覆盖写入 MEMORY.md（整体替换，而非追加）。"""
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """向 HISTORY.md 追加一条历史记录，末尾保留空行以分隔条目。"""
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        """返回格式化后的长期记忆块，供注入系统提示词；若为空则返回空字符串。"""
        long_term = self.read_long_term()
        if not long_term:
            return ""
        parts = [f"## Long-term Memory\n{long_term}"]
        if is_research_workspace(self.memory_dir.parent):
            parts.append(
                "## Research Memory Note\n"
                "This workspace is in research mode. Durable research state (papers, gaps, ideas, "
                "decisions) is stored in research cards and artifacts, NOT in MEMORY.md.\n"
                "Use `research_memory_list_recent`, `research_memory_search`, `research_memory_read`, "
                "`research_artifact_list`, and `research_artifact_read` to recover research state.\n"
                "MEMORY.md is for conversation-level short notes only."
            )
        return "\n\n".join(parts)

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        model: str,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
    ) -> bool:
        """将旧对话消息整合写入 MEMORY.md 和 HISTORY.md，通过 LLM 工具调用实现。

        整合流程：
          1. 根据模式（archive_all 或滑动窗口）确定需要整合的消息范围。
          2. 将这些消息格式化为纯文本，连同当前长期记忆一起构造 prompt。
          3. 调用 LLM，强制其调用 save_memory 工具，以结构化方式返回摘要。
          4. 将摘要写入 HISTORY.md，将更新后的记忆写入 MEMORY.md。
          5. 更新 session.last_consolidated 指针，标记已处理的边界。

        参数：
          archive_all: 若为 True，归档全部消息（用于 /new 命令清空会话前保存）。
          memory_window: 滑动保留窗口大小，超出部分才会被整合。

        返回：
          True 表示成功（含无需操作的情况），False 表示整合失败。
        """
        if archive_all:
            # 归档模式：将会话中的所有消息都纳入整合范围，不保留任何消息
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} messages", len(session.messages))
        else:
            # 滑动窗口模式：保留最近 keep_count 条消息，整合更早的消息
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                # 消息总数未超过保留窗口，无需整合
                return True
            if len(session.messages) - session.last_consolidated <= 0:
                # 自上次整合后没有新消息，跳过
                return True
            # 提取上次整合位置到保留窗口起点之间的消息
            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return True
            logger.info("Memory consolidation: {} to consolidate, {} keep", len(old_messages), keep_count)

        # 将待整合消息格式化为 "[时间戳] ROLE [tools: ...]: 内容" 的文本行
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue  # 跳过无正文内容的消息（如纯工具调用消息）
            # 若消息携带工具使用记录，附加成 [tools: tool1, tool2] 格式
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")

        # 读取现有长期记忆，作为整合 prompt 的上下文基础
        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

        try:
            # 使用专用的系统提示调用 LLM，并且只提供 save_memory 工具，
            # 强制 LLM 以工具调用的方式返回结构化摘要
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation."},
                    {"role": "user", "content": prompt},
                ],
                tools=_SAVE_MEMORY_TOOL,
                model=model,
            )

            if not response.has_tool_calls:
                # LLM 未按预期调用工具，整合失败
                logger.warning("Memory consolidation: LLM did not call save_memory, skipping")
                return False

            # 取第一个工具调用的参数（save_memory 只会被调用一次）
            args = response.tool_calls[0].arguments
            # 部分 provider 将 arguments 作为 JSON 字符串而非 dict 返回，需要反序列化
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                logger.warning("Memory consolidation: unexpected arguments type {}", type(args).__name__)
                return False

            # 将历史摘要追加写入 HISTORY.md
            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self.append_history(entry)

            # 若 LLM 返回的长期记忆与现有内容不同，才更新 MEMORY.md（避免无谓写盘）
            if update := args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    self.write_long_term(update)

            # 更新整合边界指针：archive_all 时归零（全部归档），否则指向保留区起点
            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            logger.info("Memory consolidation done: {} messages, last_consolidated={}", len(session.messages), session.last_consolidated)
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return False


def is_research_workspace(workspace: Path) -> bool:
    """Return True if the workspace has a RESEARCH.md marker file."""
    return (workspace / "RESEARCH.md").exists()
