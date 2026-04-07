"""Agent loop: the core processing engine."""
# Agent 循环：nanobot 的核心消息处理引擎。
# 负责从消息总线（MessageBus）获取入站消息，驱动 LLM 与工具之间的迭代，
# 并将最终回复写回消息总线。

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.research import (
    LiteratureSearchTool,
    PaperDigestTool,
    ResearchArtifactListTool,
    ResearchArtifactReadTool,
    ResearchMemoryAuditTool,
    ResearchMemoryListRecentTool,
    ResearchMemoryReadTool,
    ResearchMemorySearchTool,
    SaveResearchArtifactTool,
    SaveResearchCardTool,
)
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager

# TYPE_CHECKING 块：仅在静态类型检查时导入，避免运行时循环依赖
if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    完整处理流程（每条消息）：
      1. 从消息总线获取入站消息（InboundMessage）
      2. 加载会话历史，构建包含系统提示词、记忆和上下文的消息列表
      3. 调用 LLM provider.chat()，获取响应
      4. 若响应包含工具调用，则依次执行工具并将结果追加到消息列表，继续迭代
      5. 若响应为纯文本，则结束迭代，将最终回复发布为出站消息（OutboundMessage）
    """

    # 工具返回结果在持久化到 session 时的最大字符数；超出部分截断，防止 context 爆炸
    _TOOL_RESULT_MAX_CHARS = 500

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
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        tool_profile: str = "auto",
    ):
        # 延迟导入避免循环依赖；ExecToolConfig 提供 shell 工具的默认配置
        from nanobot.config.schema import ExecToolConfig

        # 消息总线：负责入站/出站消息的异步队列
        self.bus = bus
        # 消息渠道配置（WhatsApp、Telegram 等）
        self.channels_config = channels_config
        # LLM provider：封装了与 AI 模型的通信（OpenAI、DeepSeek 等）
        self.provider = provider
        # 工作空间根目录
        self.workspace = workspace
        # 使用的模型名称；若未指定则取 provider 默认值
        self.model = model or provider.get_default_model()
        # 单次对话最多迭代（工具调用）次数，防止无限循环
        self.max_iterations = max_iterations
        # LLM 采样温度：0.1 接近确定性输出，适合任务执行场景
        self.temperature = temperature
        # 单次 LLM 调用最大 token 数
        self.max_tokens = max_tokens
        # 历史消息滑动窗口大小：超出则触发记忆整合
        self.memory_window = memory_window
        # 推理强度（部分模型支持，如 OpenAI o-series）
        self.reasoning_effort = reasoning_effort
        # Brave Search API 密钥（用于 WebSearchTool）
        self.brave_api_key = brave_api_key
        # HTTP 代理地址（用于 WebSearchTool 和 WebFetchTool）
        self.web_proxy = web_proxy
        # Shell 执行工具配置（超时、路径追加、是否限制在工作空间内）
        self.exec_config = exec_config or ExecToolConfig()
        # 定时任务服务（可选）；为 None 时不注册 CronTool
        self.cron_service = cron_service
        # 是否将文件系统操作限制在工作空间目录内
        self.restrict_to_workspace = restrict_to_workspace
        self.tool_profile = tool_profile
        self.research_mode = tool_profile == "research" or (
            tool_profile == "auto" and (workspace / "RESEARCH.md").exists()
        )

        # 上下文构建器：负责组装每次 LLM 调用的消息列表
        self.context = ContextBuilder(workspace)
        # 会话管理器：负责加载、保存和缓存每个 session 的历史消息
        self.sessions = session_manager or SessionManager(workspace)
        # 工具注册表：所有可供 LLM 调用的工具的集合
        self.tools = ToolRegistry()
        # 子 Agent 管理器：用于 spawn 工具创建并管理子任务
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        # Agent 循环运行状态标志
        self._running = False
        # MCP（Model Context Protocol）服务器配置字典
        self._mcp_servers = mcp_servers or {}
        # AsyncExitStack 用于管理 MCP 连接的生命周期
        self._mcp_stack: AsyncExitStack | None = None
        # MCP 连接状态标志，避免重复连接
        self._mcp_connected = False
        self._mcp_connecting = False

        # 正在进行记忆整合的 session key 集合（用于去重，防止并发整合同一会话）
        self._consolidating: set[str] = set()
        # 强引用集合，防止整合任务被垃圾回收
        self._consolidation_tasks: set[asyncio.Task] = set()
        # 每个 session 的整合锁（弱引用字典，session 消亡时自动清理）
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        # 每个 session 当前活跃的异步任务列表，用于 /stop 命令取消
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        # 全局消息处理锁：确保同一时间只处理一条消息，避免会话状态竞争
        self._processing_lock = asyncio.Lock()

        # 注册所有默认工具
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认工具集。

        工具集包括：
          - 文件系统工具：ReadFile、WriteFile、EditFile、ListDir
          - Shell 执行工具：ExecTool
          - 网络工具：WebSearch（Brave API）、WebFetch
          - 消息工具：MessageTool（向指定渠道发送消息）
          - 子任务工具：SpawnTool（创建子 Agent）
          - 定时任务工具：CronTool（若 cron_service 存在）
        """
        # 若开启工作空间限制，则文件操作只能在 workspace 目录下进行
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        file_tools = (ReadFileTool, ListDirTool) if self.research_mode else (
            ReadFileTool,
            WriteFileTool,
            EditFileTool,
            ListDirTool,
        )
        for cls in file_tools:
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))

        if not self.research_mode:
            self.tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))

        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))

        if self.research_mode:
            self.tools.register(LiteratureSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
            self.tools.register(PaperDigestTool(proxy=self.web_proxy))
            self.tools.register(ResearchMemoryListRecentTool(self.workspace))
            self.tools.register(ResearchMemorySearchTool(self.workspace))
            self.tools.register(ResearchMemoryReadTool(self.workspace))
            self.tools.register(ResearchArtifactListTool(self.workspace))
            self.tools.register(ResearchArtifactReadTool(self.workspace))
            self.tools.register(SaveResearchCardTool(self.workspace))
            self.tools.register(SaveResearchArtifactTool(self.workspace))
            self.tools.register(ResearchMemoryAuditTool(self.workspace))

        # MessageTool 的发送回调直接指向消息总线的出站发布方法
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        if not self.research_mode:
            self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service and not self.research_mode:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """懒加载式连接 MCP 服务器（仅在首次消息到达时执行一次）。

        MCP（Model Context Protocol）允许 Agent 通过标准协议连接外部工具服务。
        若连接失败，会记录错误日志并重置状态，下次消息到达时重试。
        """
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            # AsyncExitStack 统一管理所有 MCP 连接的上下文，便于一次性关闭
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """更新需要知道路由信息的工具的上下文（channel、chat_id、message_id）。

        MessageTool 需要知道回复到哪个频道/聊天；
        SpawnTool 和 CronTool 也需要此信息以便子任务正确路由回复。
        """
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    # message 工具还需要 message_id（用于回复引用等功能）
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """移除 LLM 输出中的 <think>…</think> 推理块。

        部分模型（如 DeepSeek-R1）会在正文前嵌入思维链推理块，
        对用户展示时需要将其去除，仅保留最终回复内容。
        [\\s\\S]*? 使用非贪婪匹配，避免跨越多个 think 块。
        """
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """将工具调用格式化为简洁的人类可读提示，用于进度推送。

        示例输出："web_search("python asyncio")", "read_file("README.md")"
        仅提取第一个参数的值（通常是最具代表性的），超过 40 字符的值截断显示。
        """
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """执行 Agent 的核心迭代循环，直至 LLM 给出最终文本回复或达到最大迭代次数。

        每次迭代：
          1. 调用 LLM，传入当前消息列表和工具定义
          2. 若 LLM 返回工具调用：执行工具，将结果追加到消息列表，继续迭代
          3. 若 LLM 返回纯文本：将其作为最终回复，退出循环
          4. 若迭代次数达到上限，生成超限提示并退出

        参数：
          initial_messages: 已组装好的完整消息列表（system + history + user）
          on_progress: 进度回调，用于在工具调用期间推送中间结果

        返回：
          (final_content, tools_used, messages)
            - final_content : 最终回复文本（可能为 None，但类型标注允许）
            - tools_used    : 本次循环中所有被调用的工具名称列表
            - messages      : 包含完整对话轮次（含工具结果）的消息列表
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            # 调用 LLM，传入当前消息历史和可用工具定义
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                # LLM 请求调用工具 ─────────────────────────────────────────────
                if on_progress:
                    # 若 LLM 在工具调用前输出了思考文本，先推送给用户
                    clean = self._strip_think(response.content)
                    if clean:
                        await on_progress(clean)
                    # 推送当前工具调用提示（如 'web_search("asyncio")'）
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                # 将 LLM 的工具调用意图转换为 OpenAI 标准 dict 格式，追加到消息列表
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                # 依次执行每个工具调用
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    # 通过工具注册表调度执行，返回字符串类型的结果
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    # 将工具结果追加到消息列表，LLM 下次迭代时可见
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # LLM 返回最终文本回复（无工具调用），结束循环 ─────────────────
                clean = self._strip_think(response.content)
                # 若 LLM 返回错误（如 API 限流、内容过滤），不将其持久化到 session，
                # 避免污染上下文造成后续 400 请求错误循环（issue #1303）
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break  # 正常退出循环

        # 迭代次数耗尽，生成友好的超限提示
        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """启动 Agent 主循环，持续从消息总线获取并分发消息。

        每条消息作为独立的异步任务执行（非阻塞），确保 /stop 命令
        可以第一时间被响应和处理，不被正在执行的任务阻塞。
        使用 1 秒超时轮询消息总线，保持对 self._running 标志的响应能力。
        """
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                # 以 1 秒超时等待消息；若无消息则继续循环检查 _running 标志
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                # /stop 命令：同步处理，立即取消当前会话所有活跃任务
                await self._handle_stop(msg)
            else:
                # 普通消息：创建独立 Task 异步处理，避免阻塞主循环
                task = asyncio.create_task(self._dispatch(msg))
                # 将任务加入该 session 的活跃任务列表，以便 /stop 时取消
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                # 任务完成后的回调：从活跃列表中移除自身（防止内存泄漏）
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """处理 /stop 命令：取消指定 session 的所有活跃任务和子 Agent。

        流程：
          1. 弹出该 session 的所有活跃任务并逐一取消
          2. 等待每个任务完成（捕获 CancelledError）
          3. 通知 SubagentManager 取消该 session 的子任务
          4. 向用户发送停止确认消息
        """
        tasks = self._active_tasks.pop(msg.session_key, [])
        # 对未完成的任务发送取消信号，返回值为成功取消的数量
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t  # 等待任务真正退出（CancelledError 在任务内部处理）
            except (asyncio.CancelledError, Exception):
                pass
        # 取消该 session 相关的子 Agent 任务
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """在全局处理锁下处理单条消息，并将回复发布到消息总线。

        使用 _processing_lock 确保同一时刻只有一条消息在被处理，
        避免并发修改 session 状态导致数据竞争。

        异常处理：
          - CancelledError：任务被 /stop 取消，重新抛出以正常退出
          - 其他异常：记录错误日志，向用户返回友好的错误提示
        """
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    # CLI 渠道需要一个空回复作为"完成"信号，供 CLI 层检测
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """关闭所有 MCP 服务器连接，释放相关资源。

        MCP SDK 的取消作用域清理可能产生噪音异常，此处静默处理。
        """
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """将 _running 标志设为 False，使主循环在下一次超时后退出。"""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        skill_names: list[str] | None = None,
    ) -> OutboundMessage | None:
        """处理单条入站消息，完成 Agent 的完整推理-响应周期。

        特殊处理逻辑：
          - system 频道消息：从 chat_id 中解析原始频道和 chat_id（格式 "channel:chat_id"）
          - /new 命令：归档当前会话记忆，清空会话历史，开始新会话
          - /help 命令：返回帮助文本
          - 普通消息：触发记忆整合检查 → 构建上下文 → 运行 Agent 循环 → 保存会话

        返回：
          OutboundMessage：包含最终回复内容，将由调用者发布到消息总线；
          None：当本轮 MessageTool 已直接发送了消息时，避免重复发送。
        """
        # ── system 频道消息处理 ────────────────────────────────────────────────
        # system 消息用于内部触发（如 Cron 任务），其 chat_id 格式为 "channel:chat_id"
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                skill_names=skill_names,
                channel=channel,
                chat_id=chat_id,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        # 日志预览：截取前 80 字符，避免长消息刷屏
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        resolved_skill_names = skill_names or session.metadata.get("skill_names")

        # ── 斜杠命令处理 ───────────────────────────────────────────────────────
        cmd = msg.content.strip().lower()

        if cmd == "/new":
            # /new 命令：先将当前会话记忆归档，再清空会话
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    # 只归档自上次整合以来的新消息
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        # 创建临时 session 对象以避免在归档过程中修改原始 session
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                # 无论成功与否，都要从整合集合中移除，防止死锁
                self._consolidating.discard(session.key)

            # 清空内存中的 session 并持久化，使缓存失效
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")

        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        # ── 记忆整合触发检查 ───────────────────────────────────────────────────
        # 当自上次整合的消息数超过滑动窗口大小，且当前 session 没有正在进行的整合任务时，
        # 异步启动记忆整合，不阻塞当前消息处理
        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                """异步整合记忆的内部任务函数，完成后释放整合锁和任务引用。"""
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        # 从强引用集合移除，允许任务被垃圾回收
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            # 加入强引用集合，防止任务在完成前被垃圾回收
            self._consolidation_tasks.add(_task)

        # ── 普通消息处理 ───────────────────────────────────────────────────────
        # 更新工具的路由上下文（channel、chat_id、message_id）
        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        # 通知 MessageTool 开始新的对话轮次（重置"本轮已发送"标志）
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # 从 session 获取最近 memory_window 条历史消息，构建完整消息列表
        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            skill_names=resolved_skill_names,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            """进度推送回调：将工具调用提示或中间结果发布为带 _progress 标记的出站消息。

            _progress=True 标记让 CLI/渠道层知道这是中间状态，而非最终回复。
            _tool_hint=True 标记进一步区分工具提示和实质性内容。
            """
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        # 运行 Agent 迭代循环，获得最终回复
        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # 保存本轮新增的消息到 session（跳过 system prompt 和已有历史）
        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        # 若 MessageTool 在本轮已经主动发送过消息，则不重复发送最终回复
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """将本轮新增消息持久化到 session，同时执行数据清洗：

          - 跳过空 assistant 消息（无内容且无工具调用），防止污染上下文
          - 截断过长的工具结果（超过 _TOOL_RESULT_MAX_CHARS 的部分替换为省略号）
          - 剥离 user 消息中的运行时上下文前缀（时间戳等临时信息不宜持久化）
          - 将 user 多模态消息中的 base64 图片替换为 [image] 占位符（节省存储）
          - 为每条消息附加 timestamp 字段（若尚未存在）

        参数：
          skip: 已有消息数（system prompt 占 1 个位置 + 原有历史长度），
                只保存 messages[skip:] 中的新增消息。
        """
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)  # 浅拷贝，避免修改原始消息列表中的对象
            role, content = entry.get("role"), entry.get("content")

            # 跳过内容和工具调用均为空的 assistant 消息（可能是空占位）
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue

            # 截断过长的工具结果文本，防止 session 文件过大
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"

            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # 纯文本 user 消息：剥离运行时上下文前缀，只保留用户实际输入
                    # 格式："[Runtime Context...]\n时间戳等\n\n用户实际内容"
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue  # 跳过仅含运行时上下文、无实际用户内容的消息

                if isinstance(content, list):
                    # 多模态 user 消息：过滤掉运行时上下文块和 base64 图片
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # 剥离运行时上下文文本块
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            # 将 base64 内联图片替换为 [image] 文本占位符，大幅减小存储体积
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue  # 过滤后内容为空，跳过整条消息
                    entry["content"] = filtered

            # 为消息打上时间戳（若尚未存在）
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)

        # 更新 session 的最后修改时间
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """委托 MemoryStore 执行记忆整合，将旧消息压缩写入 MEMORY.md 和 HISTORY.md。

        每次调用都创建新的 MemoryStore 实例（轻量级，仅含路径引用），
        保证并发整合时不共享状态。

        返回：True 表示成功，False 表示整合失败。
        """
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        skill_names: list[str] | None = None,
    ) -> str:
        """直接处理消息，绕过消息总线（供 CLI 命令行和 Cron 任务使用）。

        构造一个 InboundMessage 并直接调用 _process_message，
        省去消息发布/订阅的中间环节，适合同步式的单次调用场景。

        参数：
          content     : 用户输入的消息文本
          session_key : 会话键，默认为 "cli:direct"
          channel     : 频道名称，默认为 "cli"
          chat_id     : 聊天 ID，默认为 "direct"
          on_progress : 可选进度回调（CLI 可用于打印中间状态）

        返回：
          最终回复文本；若 MessageTool 已主动发送（response 为 None），则返回空字符串。
        """
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        if skill_names:
            session = self.sessions.get_or_create(session_key)
            session.metadata["skill_names"] = list(skill_names)
            self.sessions.save(session)
        response = await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            skill_names=skill_names,
        )
        return response.content if response else ""
