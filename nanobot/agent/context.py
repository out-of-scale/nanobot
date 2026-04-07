"""Context builder for assembling agent prompts."""
# 上下文构建器：负责将系统提示词、记忆、技能、历史对话和用户消息
# 组装成 LLM 可直接使用的消息列表（messages）。

import base64
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.research.prompt_templates import RESEARCH_SYSTEM_PROMPT
from nanobot.research.workflow import ResearchWorkflowResolver


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent.

    ContextBuilder 的职责：
      1. 构建系统提示词（system prompt）：包含身份定义、引导文件、长期记忆和技能说明。
      2. 构建完整的消息列表（messages list）：供 LLM provider 直接调用。
      3. 管理消息的追加：工具结果消息、助手回复消息。
    """

    # 引导文件列表：按顺序从工作空间根目录加载，内容会被追加进系统提示词。
    # - AGENTS.md  : 项目级别的开发规范与命令提示
    # - SOUL.md    : Agent 的性格与行为准则
    # - USER.md    : 用户自定义偏好设置
    # - TOOLS.md   : 工具使用说明补充
    # - IDENTITY.md: 可覆盖身份描述
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md", "RESEARCH.md"]

    # 运行时上下文标签：注入到每条用户消息最前面的元数据块前缀。
    # _save_turn 中会识别此标签并将其从持久化历史中剥离，
    # 避免时间戳等瞬时信息污染长期记忆。
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        # MemoryStore 负责读写 MEMORY.md 和 HISTORY.md
        self.memory = MemoryStore(workspace)
        # SkillsLoader 负责发现并加载工作空间内的自定义技能
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """构建完整的系统提示词，由以下几部分拼接而成（以 '---' 分隔）：
          1. 核心身份描述（_get_identity）
          2. 引导文件内容（AGENTS.md 等）
          3. 长期记忆（MEMORY.md）
          4. 始终激活的技能（always=true 的 skills）
          5. 可按需加载的技能目录（技能摘要）
        """
        parts = [self._get_identity()]

        # 加载引导文件，若存在则追加
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # 从 MEMORY.md 读取长期记忆，格式化后追加
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        if (self.workspace / "RESEARCH.md").exists():
            parts.append(RESEARCH_SYSTEM_PROMPT)
            resolver = ResearchWorkflowResolver(self.workspace)
            parts.append(resolver.render_context_block())
            hint = self._research_recovery_hint()
            if hint:
                parts.append(hint)

        # 加载所有标记为"始终激活"的技能内容，直接注入系统提示词
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        requested_skills = [name for name in (skill_names or []) if name not in always_skills]
        if requested_skills:
            requested_content = self.skills.load_skills_for_context(requested_skills)
            if requested_content:
                parts.append(f"# Requested Skills\n\n{requested_content}")

        # 仅列出可选技能的摘要（Agent 可调用 read_file 来加载具体内容）
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        # 各部分之间用水平分隔线拼接，提高可读性并减少 LLM 混淆
        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """生成 Agent 的核心身份描述块，包含：
          - 工作空间路径（绝对路径，expanduser 展开 ~）
          - 当前操作系统和 Python 版本
          - 记忆文件和历史日志的位置
          - 行为准则（Guidelines）
        """
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        # 将 Darwin 映射为 macOS，其他系统直接使用 platform.system() 返回值
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        is_research = (self.workspace / "RESEARCH.md").exists()
        role_line = (
            "You are nanobot, an interactive research and idea discovery assistant."
            if is_research
            else "You are nanobot, a helpful AI assistant."
        )
        research_guidelines = """
- In research mode, prioritize active literature search before proposing novelty claims.
- Use `research_memory_list_recent` / `research_memory_search` / `research_memory_read`
  to recover durable research state before repeating work.
- Use `literature_search` to expand the neighborhood, `paper_digest` to structure sources,
  and `save_research_card` / `save_research_artifact` to persist useful outputs.
- Use `research_artifact_list` / `research_artifact_read` to recover reports, maps, and briefs.
- Treat problem framing, paper notes, gap cards, ideas, and decisions as durable research state.
""".strip()
        guidelines_tail = (
            f"- Ask for clarification when the request is ambiguous.\n{research_guidelines}"
            if is_research
            else "- Ask for clarification when the request is ambiguous."
        )

        return f"""# nanobot 🐈

{role_line}

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

## nanobot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
{guidelines_tail}

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """构建运行时元数据块，注入到每条用户消息之前。

        包含：当前时间（含时区）、频道名称、Chat ID。
        这些信息属于"不可信"来源（用户侧），因此使用标签明确标注，
        避免 Agent 将其视为受信任的系统指令。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        # 使用类变量标签作为前缀，便于 _save_turn 识别并剥离该段内容
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _research_recovery_hint(self) -> str:
        """Return a compaction warning if MEMORY.md is large in research mode.

        When MEMORY.md grows long, the LLM should prefer structured research cards
        and artifacts over the raw text content of MEMORY.md.
        """
        memory_md = self.workspace / "memory" / "MEMORY.md"
        if not memory_md.exists():
            return ""
        try:
            length = len(memory_md.read_text(encoding="utf-8"))
        except OSError:
            return ""
        if length > 500:
            return (
                "⚠️ MEMORY.md is growing long. "
                "Prefer `research_memory_list_recent` / `research_artifact_list` "
                "to recover research state rather than relying on MEMORY.md text."
            )
        return ""

    def _load_bootstrap_files(self) -> str:
        """按顺序读取 BOOTSTRAP_FILES 列表中的文件，拼接成 markdown 文本。
        只加载工作空间根目录下实际存在的文件，不存在的文件跳过。
        """
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                # 每个文件作为二级标题的 section 追加
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """组装 LLM 调用所需的完整消息列表，格式为：
          [system, ...history, user]

        最终的 user 消息由两部分合并而成：
          - 运行时上下文元数据（时间、频道等）
          - 实际用户内容（文本 + 可选图片）

        合并为单条消息是为了避免某些 provider 对连续同角色消息的拒绝。
        """
        # 构建运行时上下文前缀
        runtime_ctx = self._build_runtime_context(channel, chat_id)
        # 构建用户内容（纯文本或含图片的多模态列表）
        user_content = self._build_user_content(current_message, media)

        # 将运行时上下文与用户内容合并为单条 user 消息
        if isinstance(user_content, str):
            # 纯文本情况：直接字符串拼接
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            # 多模态情况（含图片）：在内容列表最前插入文本块
            merged = [{"type": "text", "text": runtime_ctx}] + user_content

        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,  # 展开历史对话记录（已从 session 中截取 memory_window 条）
            {"role": "user", "content": merged},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """构建用户消息内容，支持纯文本和含图片的多模态格式。

        若 media 为空或文件无效，直接返回文本字符串。
        若包含合法图片文件，返回 OpenAI vision 格式的内容列表：
          [{type: image_url, ...}, ..., {type: text, text: ...}]
        图片以 base64 内联编码（data URI），无需额外上传。
        """
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            # 推断 MIME 类型，只接受 image/* 类型的文件
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue  # 跳过不存在或非图片文件
            # 读取文件字节并编码为 base64 字符串
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            # 所有 media 路径均无效，降级为纯文本
            return text
        # 图片列表在前，文本在后（符合多数视觉模型的期望顺序）
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """将一次工具调用的结果追加到消息列表中。

        格式遵循 OpenAI tool message 规范：
          role="tool", tool_call_id=..., name=..., content=<结果文本>
        """
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """将助手回复（含可选工具调用和推理内容）追加到消息列表中。

        字段说明：
          content       : 助手的文本回复（可能为 None，当仅有工具调用时）
          tool_calls    : OpenAI 格式的工具调用列表（function calling）
          reasoning_content : 部分 provider（如 DeepSeek-R1）返回的链式推理文本
          thinking_blocks   : Anthropic Claude 扩展的思考块（extended thinking）
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        # reasoning_content 使用 `is not None` 判断，因为空字符串也应被记录
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
