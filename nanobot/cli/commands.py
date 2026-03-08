"""CLI commands for nanobot."""

import asyncio    #异步并发框架
import os         #操作系统接口
import select     #I/O多路复用
import signal     #信号处理
import sys        #系统相关参数和函数
from pathlib import Path  #路径操作

import typer   # 把普通函数变成 CLI 命令行工具
# 提供有历史记录、颜色提示符的交互式输入框
from prompt_toolkit import PromptSession    
# 格式化文本
from prompt_toolkit.formatted_text import HTML
# 文件历史记录
from prompt_toolkit.history import FileHistory
# 补丁标准输出
from prompt_toolkit.patch_stdout import patch_stdout
# 终端里输出带颜色、表格、Markdown 的漂亮文字
from rich.console import Console
# Markdown 渲染
from rich.markdown import Markdown
# 表格
from rich.table import Table
# 文本
from rich.text import Text

# 内部模块
from nanobot import __logo__, __version__   # 导入 logo 和版本号 
from nanobot.config.schema import Config  # 导入配置
from nanobot.utils.helpers import sync_workspace_templates  # 导入同步工作区模板

# 创建 CLI 应用主入口，无参数时显示帮助
app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,  # 无子命令时自动打印帮助
)

# Rich 库的输出对象，凡是带颜色/样式的打印都走它
console = Console()
# 用 set 存储退出指令，O(1) 查找比 list 更快   存可以退出程序的命令词
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

# 模块级变量，懒初始化，避免启动时副作用
_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # 保存终端原始属性，退出时还原


# AI 思考期间用户的乱按输入，在下次提问前丢弃，防止污染输入
def _flush_pending_tty_input() -> None:
    """当 AI 在思考输出时，用户可能乱按了一堆键，这个函数的作用是把那些"垃圾键盘输入"丢掉，防止污染下一次提问。"""
    try:
        fd = sys.stdin.fileno()  # 获取标准输入文件描述符
        if not os.isatty(fd):   # 非 TTY（如管道）则跳过
            return
    except Exception:
        return

    try:
        # 优先用 termios 一次性清空输入缓冲区（Linux/macOS）
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass  # Windows 无 termios，走下面备用方案

    # 备用：用 select 轮询逐块读丢（跨平台）
    try:
        while True:
            # select 返回三元组 (可读, 可写, 异常)，只关心可读
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:           # 无数据可读，缓冲区已空
                break
            if not os.read(fd, 4096):  # 读到空字节表示 EOF
                break
    except Exception:
        return


# 退出前还原终端状态（回显、行缓冲等），防止终端被 prompt_toolkit 改乱
def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:  # 从未保存则无需还原
        return
    try:
        # TCSADRAIN：等待当前输出写完再修改属性，比 TCSANOW 更安全
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


# 初始化交互式输入会话，只调用一次（懒初始化模式）
def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    # global 声明：修改模块级变量而非创建局部变量
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # 在 prompt_toolkit 接管终端前先保存原始属性
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    # 历史文件持久化到 ~/.nanobot/history/，跨会话保留
    history_file = Path.home() / ".nanobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)  # 目录不存在则自动创建

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),  # 持久化历史记录
        enable_open_in_editor=False,             # 禁用 Ctrl+X Ctrl+E 编辑器
        multiline=False,   # 单行模式：Enter 直接提交
    )


# 打印 AI 回复，支持 Markdown 渲染或纯文本两种模式
def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""  # 防止 response 为 None 时出错
    # 根据参数选择渲染方式：Markdown 对象 or 纯文本对象
    body = Markdown(content) if render_markdown else Text(content)
    console.print()                                  # 空行分隔
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")  # 带颜色的前缀
    console.print(body)
    console.print()                                  # 空行收尾


# 判断用户输入是否为退出指令
def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS  # 先转小写再查 set，兼容大小写


# 异步读取用户输入，async 使其可与 asyncio 事件循环协同
async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        # patch_stdout 防止 AI 异步输出与用户输入行互相覆盖
        with patch_stdout():
            # await 挂起当前协程，等待用户按 Enter
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),  # 蓝色加粗提示符
            )
    except EOFError as exc:
        # Ctrl+D 发出 EOFError，统一转为 KeyboardInterrupt 处理
        raise KeyboardInterrupt from exc



# --version 回调：打印版本后立即退出，不继续执行子命令
def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()  # 正常退出，不报错


# @app.callback 让 main 成为全局选项处理函数（如 --version）
@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
        # is_eager=True：在其他参数处理前优先执行此回调
    ),
):
    """nanobot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


# 初始化配置文件与工作目录，首次使用时运行
@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    # 函数内 import：只在用到此命令时才加载，加快其他命令的启动速度
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        # 配置已存在时询问用户：覆盖还是仅刷新
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            # 用默认值覆盖，丢弃原有配置
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            # 加载已有配置再保存，自动补全新字段，保留旧值
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        # 首次运行：直接创建默认配置
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # 创建工作目录
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)  # 递归创建所有缺失的父目录
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)  # 同步内置模板文件到工作目录

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")





# 根据配置决定使用哪种 LLM Provider，返回对应实例
def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    # 函数内 import：避免未安装对应库时影响其他命令
    from nanobot.providers.custom_provider import CustomProvider
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model       # 配置中指定的模型名
    provider_name = config.get_provider_name(model)  # 从模型名推断 provider
    p = config.get_provider(model)             # 获取 provider 配置对象（可能为 None）

    # OpenAI Codex 使用 OAuth 认证，无需 api_key，单独处理
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # custom provider：直接对接 OpenAI 兼容接口，绕过 LiteLLM
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",  # p 为 None 时给占位值
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    # 其余 provider 统一走 LiteLLM 路由
    from nanobot.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    # bedrock 用 AWS 凭证、OAuth provider 无需 api_key，其余必须检查
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.nanobot/config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,          # p 为 None 时传 None
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


# 启动完整的 gateway 服务：消息总线 + Agent + 所有渠道 + 定时任务 + 心跳
@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    # 函数内 import：gateway 依赖较多，按需加载避免影响其他命令
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)  # 启用 DEBUG 日志

    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")

    config = load_config()
    sync_workspace_templates(config.workspace_path)  # 同步内置模板
    bus = MessageBus()                               # 全局消息总线（发布/订阅）
    provider = _make_provider(config)                # 构建 LLM provider
    session_manager = SessionManager(config.workspace_path)

    # 先创建 cron 服务（回调在 agent 建好后再设置，避免循环依赖）
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    # 设置 cron 回调：定时任务触发时由 agent 执行
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool
        # 构造提醒消息，注入任务名称和指令
        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # 执行期间禁止 agent 再新增 cron 任务，防止递归调度
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)  # 设置上下文标志
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",      # 每个 job 独立会话
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            # 不管是否出错都要重置上下文，防止状态泄漏
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        # agent 本轮已通过 message tool 发送过消息，直接返回不重复投递
        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        # 配置要求投递且有目标时，主动推送到渠道
        if job.payload.deliver and job.payload.to and response:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response
            ))
        return response
    cron.on_job = on_cron_job  # 将回调挂载到 cron 服务

    # 创建渠道管理器（WhatsApp/Telegram/Slack 等）
    channels = ChannelManager(config, bus)

    # 为心跳消息选择一个可路由的渠道和会话目标
    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        # 用 set 方便 O(1) 判断渠道是否已启用
        enabled = set(channels.enabled_channels)
        # 优先选最近活跃的非内部（非 cli/system）会话
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:      # key 格式为 "channel:chat_id"
                continue
            channel, chat_id = key.split(":", 1)  # 只分割一次，chat_id 可含冒号
            if channel in {"cli", "system"}:       # 内部渠道跳过
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # 无外部会话时回退到 cli，保持行为与旧版一致
        return "cli", "direct"

    # 心跳第二阶段：通过 agent 执行心跳任务（异步）
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        # 静默进度回调，心跳执行时不打印中间步骤
        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",  # 心跳使用独立的会话 key
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,      # 传入空回调，抑制进度输出
        )

    # 心跳结果投递：只在有外部渠道时才推送
    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from nanobot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # 无外部渠道时静默，不投递给 CLI
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # 异步主循环：并发运行 agent + 所有渠道，Ctrl+C 触发优雅关闭
    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            # gather 并发运行多个协程，任一抛出异常则全部取消
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            # finally 保证无论正常退出还是异常，都执行清理
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


# 直接与 agent 交互：支持单条消息模式和交互式会话两种模式
@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService

    config = load_config()
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    # CLI 模式下 cron 仅用于工具层，无需设置回调
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # 不需要时屏蔽 nanobot 内部日志，减少干扰
    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    # 日志关闭时才显示 spinner，开启时输出已存在日志不需要
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()  # 空上下文，什么也不做
        # logs 关闭时用动画 spinner 提示用户正在思考
        return console.status("[dim]nanobot is thinking...[/dim]", spinner="dots")

    # 进度回调：根据配置决定是否打印工具提示或进度消息
    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:  # tool 提示已禁用
            return
        if ch and not tool_hint and not ch.send_progress:  # 进度消息已禁用
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # 单条消息模式：直接调用，不经过 bus
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()  # 关闭 MCP 连接

        asyncio.run(run_once())
    else:
        # 交互式模式：通过 bus 消息路由，与其他渠道一致
        from nanobot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        # 解析 session_id："channel:chat_id" 格式
        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        # SIGINT 处理器：矪正终端后用 os._exit 强制退出，
        # 避免 asyncio 循环尚未启动时的 cancel 异常
        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)  # 跳过 Python 正常退出流程，直接杀死进程

        signal.signal(signal.SIGINT, _exit_on_sigint)  # 注册信号处理器

        async def run_interactive():
            # 将 agent 主循环包成 Task 并发运行
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()  # 用于同步等待当前轮回应
            turn_done.set()              # 初化为已完成状态
            turn_response: list[str] = []  # 收集当前轮的回应内容

            # 异步消费出站消息，分拣进度、当前轮、其他消息
            async def _consume_outbound():
                while True:
                    try:
                        # wait_for 设置超时避免无限块塞
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            # 进度消息：按配置决定是否显示
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass   # tool 提示已禁，不输出
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass   # 进度已禁，不输出
                            else:
                                console.print(f"  [dim]↳ {msg.content}[/dim]")
                        elif not turn_done.is_set():
                            # 当前轮未完成，收集回应并设置事件
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()  # 通知主循环可以读取回应
                        elif msg.content:
                            # 轮外消息（如心跳、异步推送）直接打印
                            console.print()
                            _print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue  # 超时继续循环
                    except asyncio.CancelledError:
                        break     # 被取消则退出

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()          # 丢弃升愤期间的垃圾输入
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:   # 空输入跳过
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        # 清空上一轮状态，准备接收新回应
                        turn_done.clear()
                        turn_response.clear()

                        # 将用户消息发布到 bus，由 agent 异步处理
                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        # 显示 spinner 并等待当前轮完成
                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:   # Ctrl+D
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                # 无论怎山退出都停止 agent 并清理任务
                agent_loop.stop()
                outbound_task.cancel()
                # return_exceptions=True 避免 gather 在取消时抛出异常
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


# 展示所有渠道的启用状态和配置信息
@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    # 创建 Rich 表格，每列独立配色
    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",  # 三元表达式：condition ? A : B 的 Python 写法
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu：app_id 不为空时只显示前 10 位，避免泄露完整 ID
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )

    # Telegram：token 只显前 10 位
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack：Socket Mode 需要 app_token + bot_token 两者同时存在
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


# 获取 WhatsApp bridge 目录，首次运行时自动安装和构建
def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # bridge 被复制到用户目录，隔离安装包
    user_bridge = Path.home() / ".nanobot" / "bridge"

    # 已构建则直接返回，避免重复安装
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Windows 上 npm 可执行文件名为 npm.cmd
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_cmd:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # 优先检查已安装包矮内的 bridge，其次检查源码仓库目录
    pkg_bridge = Path(__file__).parent.parent / "bridge"         # nanobot/bridge（已安装）
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge（开发模式）

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # 将 bridge 源码复制到用户目录，排除大文件夹
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)   # 先删除旧版本，再全量复制
    # ignore_patterns 排除 node_modules 和 dist，减少复制体积
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # 安装依赖并构建项目
    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_cmd, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_cmd, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            # 最多显示 500 字符的错误输出，避免屏幕刷屏
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


# 启动 WhatsApp bridge，展示二维码以扫码绑定设备
@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from nanobot.config.loader import load_config

    config = load_config()
    bridge_dir = _get_bridge_dir()  # 自动安装/构建 bridge

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    import shutil

    # {**os.environ} 拷贝当前环境变量，再添加自定义变量
    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

    # Windows 上 npm.cmd 优先，最后备用字符串‘npm’
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm") or "npm"

    try:
        subprocess.run([npm_cmd, "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Status Commands
# ============================================================================


# 显示 nanobot 整体运行状态：配置文件、工作目录、provider API 密鉅
@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    # 文件存在则显示绿色勾，否则红头
    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        # 按需加载 provider 注册表，避免影响其他命令
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # 遍历 registry 中所有 provider，逗一打印 API key 状态
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:  # 配置中没有此 provider 的属性
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # 本地设局展示 api_base 代替 api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)  # 转为 bool：空字符串视为 False
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


# 登录处理器注册表，映射 provider 名 → 对应登录函数
_LOGIN_HANDLERS: dict[str, callable] = {}


# 装饰器工厂：将被装饰的函数注册到全局处理器表
def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn  # 将 handler 绑定到名称
        return fn                   # 返回原函数，不改变其行为
    return decorator


# OAuth provider 登录入口：根据名称分发到对应处理器
@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from nanobot.providers.registry import PROVIDERS

    # CLI 输入用连字符，内部统一用下划线
    key = provider.replace("-", "_")
    # next + 生成器表达式：找到第一个匹配的 OAuth provider
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)  # 查找对应登录处理器
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()  # 调用具体登录逻辑


# OpenAI Codex OAuth 登录处理器
@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            # 先尝试读取本地缓存的 token
            token = get_token()
        except Exception:
            pass
        # token 不存在或已过期则重新登录
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),    # 将输出接入 Rich 控制台
                prompt_fn=lambda s: typer.prompt(s),    # 将输入接入 typer
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        # oauth_cli_kit 未安装时给出安装提示
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


# GitHub Copilot OAuth 登录处理器
@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    # 发一条小消息触发 litellm 配置设备流登录
    async def _trigger():
        from litellm import acompletion
        # max_tokens=1 仅为触发认证，内容无关紧要
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())  # 同步运行异步登录流程
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
