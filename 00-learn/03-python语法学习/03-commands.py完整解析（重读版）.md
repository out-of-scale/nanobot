# 03 cli/commands.py 完整解析（重读版）

> 对应代码：`nanobot/cli/commands.py`（1003 行，程序入口大本营）
>
> 前置知识：先读 `00-整体架构概览.md`，再读本文。

---

## 文件结构一览

```
commands.py
│
├── [导入区]                       第 1-22 行    标准库 + 第三方库 + 本项目模块
│
├── [全局变量]                     第 25-42 行   app / console / EXIT_COMMANDS / 模块变量
│
├── [终端辅助函数]                 第 46-151 行
│   ├── _flush_pending_tty_input()      丢弃 AI 思考期间的垃圾键盘输入
│   ├── _restore_terminal()             退出时还原终端状态
│   ├── _init_prompt_session()          初始化交互式输入会话（懒初始化）
│   ├── _print_agent_response()         打印 AI 回复（Markdown / 纯文本两种）
│   └── _is_exit_command()             判断是否为退出指令
│   └── _read_interactive_input_async() 异步读取用户输入（prompt_toolkit）
│
├── [CLI 主回调]                   第 156-171 行
│   └── main()                          处理全局 --version 选项
│
├── [onboard 命令]                 第 179-224 行   首次配置初始化
│
├── [LLM Provider 工厂]            第 231-269 行
│   └── _make_provider()               读配置 → 选对应的 Provider 实例
│
├── [gateway 命令]                 第 278-465 行   完整服务器模式
│   ├── 初始化各个服务（bus/agent/channels/cron/heartbeat）
│   ├── on_cron_job()                  定时任务回调
│   ├── on_heartbeat_execute/notify()  心跳回调
│   └── run()                          asyncio 并发主循环
│
├── [agent 命令]                   第 476-660 行   直接与 AI 对话
│   ├── 单条消息模式（-m 参数）
│   └── 交互式会话模式（无 -m 参数）
│       ├── run_interactive()           并发主循环
│       └── _consume_outbound()        消费出站消息队列
│
├── [channels 子命令组]            第 668-858 行
│   ├── channels_status()              显示所有渠道状态
│   ├── _get_bridge_dir()             WhatsApp bridge 安装/定位逻辑
│   └── channels_login()              扫码登录 WhatsApp
│
├── [status 命令]                  第 867-903 行   显示整体配置状态
│
└── [OAuth 登录]                   第 910-998 行
    ├── _register_login()              装饰器工厂，注册登录处理器
    ├── provider_login()               分发到对应 provider 的登录函数
    ├── _login_openai_codex()          OpenAI Codex OAuth 登录
    └── _login_github_copilot()        GitHub Copilot 设备流登录
```

---

## 一、导入区（第 1-22 行）

```python
import asyncio
import os
import select
import signal
import sys
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from nanobot import __logo__, __version__
from nanobot.config.schema import Config
from nanobot.utils.helpers import sync_workspace_templates
```

### 功能/架构

这个文件是**整个程序的入口**，需要操控几乎所有核心能力，所以导入的库特别多：

| 类别 | 库 | 用途 |
|------|----|------|
| 标准库 | `asyncio` | Python 内置的异步并发框架，整个程序靠它跑多个服务 |
| 标准库 | `os`, `sys` | 操作系统接口（文件描述符、进程退出等） |
| 标准库 | `select` | 检查文件描述符是否有数据可读（跨平台 I/O 复用） |
| 标准库 | `signal` | 捕获 Ctrl+C（SIGINT）等操作系统信号 |
| 标准库 | `pathlib.Path` | 用面向对象方式操作文件路径，比字符串更安全 |
| 第三方 | `typer` | 把普通函数变成 CLI 命令行工具 |
| 第三方 | `prompt_toolkit` | 提供有历史记录、颜色提示符的交互式输入框 |
| 第三方 | `rich` | 终端里输出带颜色、表格、Markdown 的漂亮文字 |
| 项目内 | `nanobot.*` | 导入配置、工具函数等项目内部模块 |

### Python 语法：`from X import Y` vs `import X`

```python
import asyncio          # 用时必须写 asyncio.run(...)
from pathlib import Path # 直接写 Path(...)，不用加模块前缀
```

> **为什么大多数用 `from ... import`？** 因为这个文件频繁使用这些功能，省掉模块前缀让代码更简洁。

---

## 二、全局变量（第 25-42 行）

```python
app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None
```

### 功能/架构

- `app`：整个 CLI 程序的根对象，后面所有的 `@app.command()` 都挂在它上面
- `console`：Rich 库的输出对象，凡是带颜色/样式的打印都走它
- `EXIT_COMMANDS`：集合（set），存可以退出程序的命令词
- `_PROMPT_SESSION`、`_SAVED_TERM_ATTRS`：带下划线前缀，Python 约定的"内部变量"（模块私有）

### Python 语法：set（集合）

```python
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
```

- 用 `{}` 包裹，元素间用逗号，但**没有冒号**（有冒号就是字典 `dict`）
- 特点：**无序、不重复、查找极快（O(1)）**
- `"exit" in EXIT_COMMANDS` 比 `"exit" in ["exit", "quit", ...]`（列表）快得多

### Python 语法：类型注解 `|`

```python
_PROMPT_SESSION: PromptSession | None = None
```

- `变量名: 类型` 是 Python 3.6+ 的类型注解
- `PromptSession | None`（Python 3.10+ 语法）= 这个变量可以是 `PromptSession` 对象，也可以是 `None`
- 类型注解**不强制检查**，只给人和工具（如 IDE）看的提示

---

## 三、终端辅助函数（第 46-151 行）

### 3.1 `_flush_pending_tty_input()` — 丢弃垃圾输入

```python
def _flush_pending_tty_input() -> None:
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass  # Windows 无 termios

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return
```

**功能/架构：**

AI 在思考的时候用户可能会乱打键盘，这些输入会残留在系统缓冲区。下次提问时，如果不清除，这些"垃圾"就会粘在下一条消息前面。这个函数就是在每次提问前把缓冲区清空。

**运行逻辑（三段保险）：**

1. 先检查是否是真正的终端（TTY），管道/重定向时跳过
2. 优先用 `termios`（Linux/macOS 专用，一次清空，高效）
3. 如果没有 `termios`（Windows），用 `select` 轮询逐块读丢（兼容方案）

**Python 语法：多重 `try/except`**

```python
try:
    import termios          # 尝试导入，Windows 上会报 ImportError
    termios.tcflush(...)    # 执行操作
    return                  # 成功就直接返回，后面不执行
except Exception:
    pass                    # 失败了什么都不做，继续往下走
```

- `except Exception: pass` 是吞掉异常的写法，适合"用备用方案"的场景
- `pass` 是空语句，表示"什么也不做"

**Python 语法：多返回值解包**

```python
ready, _, _ = select.select([fd], [], [], 0)
```

- `select.select()` 返回一个三元组 `(可读列表, 可写列表, 异常列表)`
- 用三个变量同时接收，`_` 是惯用名，表示"这个值我不用，先忽略"

---

### 3.2 `_restore_terminal()` — 还原终端

```python
def _restore_terminal() -> None:
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass
```

**功能/架构：**

`prompt_toolkit` 接管终端时会修改终端属性（如关闭"输入回显"），正常退出它会自动还原。但如果程序被强制杀死（如 `os._exit()`），就得手动还原，否则退出后终端可能变成"打字不显示"的奇怪状态。

---

### 3.3 `_init_prompt_session()` — 初始化输入框

```python
def _init_prompt_session() -> None:
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".nanobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
    )
```

**功能/架构：**

创建 prompt_toolkit 的交互式输入会话。特点：
- 输入历史**持久化存到文件**，重启程序后还能用方向键翻历史
- **懒初始化**：只在交互式模式才调用，单条消息模式（`-m`）不需要

**Python 语法：`global` 关键字**

```python
global _PROMPT_SESSION, _SAVED_TERM_ATTRS
```

- Python 函数内赋值默认创建**局部变量**，不影响外部
- 加 `global` 声明后，赋值操作才会修改**模块级变量**
- 规则：**读可以不写 global，写必须写 global**

**Python 语法：`Path` 对象的路径拼接**

```python
history_file = Path.home() / ".nanobot" / "history" / "cli_history"
```

- `Path.home()` 返回当前用户主目录（Windows 上是 `C:\Users\你的用户名`）
- `/` 运算符在 `Path` 对象上重载为路径拼接，比字符串 `os.path.join()` 更直观
- 等价于：`C:\Users\你的用户名\.nanobot\history\cli_history`

**Python 语法：`mkdir(parents=True, exist_ok=True)`**

```python
history_file.parent.mkdir(parents=True, exist_ok=True)
```

- `parents=True`：如果父目录也不存在，一并创建（递归创建）
- `exist_ok=True`：目录已存在时不报错，静默跳过

---

### 3.4 `_print_agent_response()` — 打印 AI 回复

```python
def _print_agent_response(response: str, render_markdown: bool) -> None:
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
    console.print(body)
    console.print()
```

**功能/架构：**

统一的 AI 回复打印函数，根据参数选择渲染方式，确保所有场景输出格式一致。

**Python 语法：三元表达式**

```python
body = Markdown(content) if render_markdown else Text(content)
```

- 格式：`值A if 条件 else 值B`
- 相当于其他语言的 `condition ? A : B`
- `render_markdown` 为 True → 创建 `Markdown` 对象（富文本渲染）
- `render_markdown` 为 False → 创建 `Text` 对象（纯文本）

**Python 语法：`or` 短路求值**

```python
content = response or ""
```

- `A or B`：如果 A 是"真值"就返回 A，否则返回 B
- Python 的"假值"包括：`None`、`False`、`0`、`""`（空字符串）、`[]`（空列表）
- 这里的效果：如果 response 是 None，就用空字符串代替，防止后面代码出错

---

### 3.5 `_is_exit_command()` — 退出指令检测

```python
def _is_exit_command(command: str) -> bool:
    return command.lower() in EXIT_COMMANDS
```

**Python 语法：函数返回类型注解 `-> bool`**

```python
def _is_exit_command(command: str) -> bool:
```

- `-> bool` 注解这个函数返回一个布尔值（True/False）
- `参数名: 类型` 注解参数类型
- `command.lower()` 先转小写再查 set，这样 "EXIT"、"Exit" 都能识别

---

### 3.6 `_read_interactive_input_async()` — 异步读用户输入

```python
async def _read_interactive_input_async() -> str:
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc
```

**功能/架构：**

等待用户在 prompt_toolkit 输入框里打字并按 Enter，返回用户输入的字符串。`patch_stdout` 让 AI 的异步输出和用户输入行互不干扰。

**Python 语法：`async def` 和 `await`**

```python
async def _read_interactive_input_async() -> str:
    return await _PROMPT_SESSION.prompt_async(...)
```

- `async def`：定义**协程函数**，调用时不立即执行，返回一个协程对象
- `await`：挂起当前协程，把控制权交还给事件循环，等被等待的操作完成再继续
- 类比："我去等用户打字了，你（事件循环）可以先去做别的事，用户按 Enter 再来找我"

**Python 语法：异常链 `raise X from Y`**

```python
except EOFError as exc:
    raise KeyboardInterrupt from exc
```

- `from exc` 把原始异常附加到新异常上，调试时能追溯根因
- 效果：把 Ctrl+D（EOFError）统一转换成 Ctrl+C（KeyboardInterrupt）处理，让上层代码只需处理一种退出场景

---

## 四、CLI 主回调（第 156-171 行）

```python
def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()

@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    pass
```

**功能/架构：**

`@app.callback()` 把 `main` 函数变成全局选项处理器（非子命令）。`is_eager=True` 让 `--version` 在解析其他参数之前就执行，所以 `nanobot --version` 永远优先生效。

**Python 语法：`@decorator`装饰器**

```python
@app.callback()
def main(...):
    pass
```

- `@` 是**装饰器语法**，等价于：`main = app.callback()(main)`
- 装饰器"包裹"了函数，给它添加额外行为，这里是把 `main` 注册为 typer 的全局回调
- `pass` 是合法的空函数体，函数不做任何事但语法完整

---

## 五、`onboard` 命令（第 179-224 行）

```python
@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
        else:
            config = load_config()
            save_config(config)
    else:
        save_config(Config())

    workspace = get_workspace_path()
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(workspace)
```

**功能/架构：**

用户第一次使用 nanobot 时运行 `nanobot onboard`，这个函数：
1. 检查配置文件是否已存在
2. 已存在：询问覆盖（重置）还是刷新（保留旧值，补新字段）
3. 不存在：直接用默认值创建
4. 最后创建工作目录、同步内置模板文件

**Python 语法：函数内 import（延迟导入）**

```python
def onboard():
    from nanobot.config.loader import get_config_path, load_config, save_config
```

- import 写在函数内部，**只在调用这个函数时才加载**
- 好处：其他命令（如 `nanobot status`）启动时不加载 `config.loader`，启动更快
- 这种模式在这个文件里大量使用，是刻意的性能优化

**Python 语法：`typer.confirm()`**

```python
if typer.confirm("Overwrite?"):
```

- 在终端打印 "Overwrite? [y/N]"，等待用户输入
- 用户输入 y/Y 返回 True，输入 n/N 或直接回车返回 False

---

## 六、`_make_provider()` — LLM 工厂函数（第 231-269 行）

```python
def _make_provider(config: Config):
    from nanobot.providers.custom_provider import CustomProvider
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    from nanobot.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )
```

**功能/架构：**

这是一个**工厂函数**（Factory Pattern），读取配置里的模型名，决定创建哪种 Provider 实例：

```
model = "openai-codex/xxx"  →  OpenAICodexProvider（OAuth 认证）
model = "custom/xxx"        →  CustomProvider（自定义 API 地址）
其他所有模型              →  LiteLLMProvider（通用路由层）
```

对应架构图里的 `providers/` 模块：屏蔽了不同 AI 接口的差异，调用者只管用，不管是哪家 AI。

**Python 语法：条件表达式 `A if 条件 else B`（三元）**

```python
api_key=p.api_key if p else "no-key"
```

- `p` 如果不是 None（真值）→ 用 `p.api_key`
- `p` 如果是 None（假值）→ 用 `"no-key"` 占位

**Python 语法：`str.startswith()`**

```python
model.startswith("openai-codex/")
```

- 检查字符串是否以指定前缀开头，返回 True/False

---

## 七、`gateway` 命令（第 278-465 行）

```python
@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    ...
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    cron = CronService(cron_store_path)
    agent = AgentLoop(bus=bus, provider=provider, ...)
    channels = ChannelManager(config, bus)
    heartbeat = HeartbeatService(...)

    async def run():
        await cron.start()
        await heartbeat.start()
        await asyncio.gather(
            agent.run(),
            channels.start_all(),
        )
    asyncio.run(run())
```

**功能/架构：**

`nanobot gateway` 是**完整的服务器模式**，一次性启动所有服务：

```
bus（消息总线）
  ↑↓
channels（Telegram/WeChat/…）── 接收外部消息 → 放入 bus → agent 处理 → 回复 → bus → 发回
agent（AI 大脑）
cron（定时任务）────────────── 定时触发 → agent 直接执行
heartbeat（心跳）───────────── 周期唤醒 → agent 执行巡检
```

**Python 语法：`asyncio.gather()`**

```python
await asyncio.gather(
    agent.run(),
    channels.start_all(),
)
```

- `gather` 同时启动多个协程，并发运行（不是顺序执行！）
- 等所有协程都完成才返回
- 任意一个抛出未捕获的异常，其他全部取消
- 类比：同时下发多个任务给不同员工，等所有人都完成汇报

**Python 语法：嵌套函数（闭包）**

```python
def gateway(...):
    agent = AgentLoop(...)

    async def on_cron_job(job: CronJob) -> str | None:
        # 可以直接访问外层的 agent 变量
        response = await agent.process_direct(...)
```

- 函数内部定义的函数叫**内嵌函数**
- 内嵌函数可以"捕获"外层函数的变量（`agent`），即使外层函数已返回也能用
- 这种特性叫**闭包（Closure）**

**Python 语法：`try/finally`**

```python
try:
    response = await agent.process_direct(...)
finally:
    if ...:
        cron_tool.reset_cron_context(cron_token)
```

- `finally` 块**无论成功还是异常都会执行**
- 用来确保"一定要做的清理工作"（如重置状态、关闭连接）不会因异常而漏掉

---

## 八、`agent` 命令（第 476-660 行）

```python
@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", ...),
    session_id: str = typer.Option("cli:direct", "--session", "-s", ...),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", ...),
    logs: bool = typer.Option(False, "--logs/--no-logs", ...),
):
```

**功能/架构：**

两种工作模式：

```
nanobot agent -m "你好"      → 单条消息模式：发一条，拿回复，退出
nanobot agent                → 交互式模式：不断循环，直到 exit/Ctrl+C
```

单条消息走 `agent_loop.process_direct()`（直接调用，不经过 bus）  
交互式走 bus（发布 InboundMessage → agent 处理 → 消费 OutboundMessage）

**Python 语法：`asyncio.Event`**

```python
turn_done = asyncio.Event()
turn_done.set()    # 设置为"已完成"状态

# 某处等待：
await turn_done.wait()

# 某处通知完成：
turn_done.set()

# 重置为"未完成"：
turn_done.clear()
```

- `asyncio.Event` 是协程间**同步信号量**，类比"旗语"
- `wait()` 挂起等待旗子竖起；`set()` 竖起旗子唤醒等待者；`clear()` 再次放倒
- 这里用于：用户发消息后，等 agent 处理完时竖旗，主循环接到信号才打印回复

**Python 语法：`asyncio.create_task()`**

```python
bus_task = asyncio.create_task(agent_loop.run())
```

- 把协程包装成 Task，在后台并发运行，不等它完成就继续往下走
- 类比：委派任务给一个"后台线程"，自己继续干别的事

**Python 语法：f-string 调试**

```python
console.print(f"  [dim]↳ {content}[/dim]")
```

- Rich 的标记语言：`[dim]...[/dim]` 使文字变暗（低调灰色）
- `f"..."` 是格式化字符串，`{content}` 运行时替换为变量值

---

## 九、channels 子命令组（第 668-858 行）

```python
channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")

@channels_app.command("status")
def channels_status(): ...

@channels_app.command("login")
def channels_login(): ...
```

**功能/架构：**

Typer 支持**子命令组**（sub-app），用 `add_typer` 把 `channels_app` 挂载到主 `app` 下：

```
nanobot channels status   → 查看各平台启用状态
nanobot channels login    → WhatsApp 扫码登录
```

**Python 语法：字符串切片**

```python
f"app_id: {fs.app_id[:10]}..."
```

- `字符串[开始:结束]` 是切片语法，`[:10]` 取前 10 个字符
- 用于脱敏展示：只显示 ID/token 的前几位，不暴露完整密钥

**Python 语法：`{**os.environ}`**

```python
env = {**os.environ}
```

- `**` 在字典里叫"字典解包"，把 `os.environ` 的所有键值对展开到新字典 `env`
- 等同于复制一份当前所有环境变量，之后加新键不影响原始环境

---

## 十、OAuth 登录（第 910-998 行）

```python
_LOGIN_HANDLERS: dict[str, callable] = {}

def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator

@_register_login("openai_codex")
def _login_openai_codex() -> None:
    ...

@_register_login("github_copilot")
def _login_github_copilot() -> None:
    ...
```

**功能/架构：**

这是一个**注册表模式 + 装饰器工厂**的组合：
- `_LOGIN_HANDLERS` 字典存放 `provider名 → 登录函数` 的映射
- `_register_login("openai_codex")` 返回一个装饰器，被装饰的函数自动注册进字典
- `provider_login()` 命令收到用户输入的 provider 名后，查表找到对应函数并调用

好处：**添加新 provider 只需加一个 `@_register_login("xxx")` 装饰的函数**，不需要改 `provider_login()` 里的 if-else 分支。

**Python 语法：装饰器工厂（高阶函数）**

```python
def _register_login(name: str):   # 外层函数：接受参数，返回装饰器
    def decorator(fn):             # 内层函数：就是真正的装饰器
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator               # 返回装饰器

@_register_login("openai_codex")  # 等价于：_login_openai_codex = _register_login("openai_codex")(_login_openai_codex)
def _login_openai_codex():
    ...
```

执行顺序：
1. Python 先调用 `_register_login("openai_codex")`，得到 `decorator` 函数
2. 再用 `decorator` 包裹 `_login_openai_codex`
3. `decorator` 把函数注册进字典，然后原样返回函数
4. 结果：函数本身不变，但已悄悄注册进了 `_LOGIN_HANDLERS`

**Python 语法：`next()` + 生成器表达式**

```python
spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
```

- `(s for s in PROVIDERS if ...)` 是**生成器表达式**（懒求值版本的列表推导式）
- `next(可迭代对象, 默认值)` 取第一个元素，找不到时返回默认值（这里是 `None`）
- 组合效果：找 `PROVIDERS` 列表里第一个满足条件的 spec，找不到就是 `None`

**Python 语法：lambda 表达式**

```python
print_fn=lambda s: console.print(s),
prompt_fn=lambda s: typer.prompt(s),
```

- `lambda 参数: 返回值` 是匿名（无名字）函数
- `lambda s: console.print(s)` 等价于：

```python
def _(s):
    return console.print(s)
```

- 适合只用一次的小函数，直接写在参数里

---

## 十一、总结：commands.py 的核心设计思想

| 设计模式 | 代码位置 | 作用 |
|----------|---------|------|
| 工厂模式 | `_make_provider()` | 根据配置创建合适的 Provider，调用者不关心细节 |
| 注册表模式 | `_LOGIN_HANDLERS` | 动态注册登录处理器，扩展时不修改现有代码 |
| 装饰器工厂 | `_register_login()` | 让注册表的注册操作可以用 `@` 语法完成 |
| 懒初始化 | `_init_prompt_session()` | 只在真正需要时才初始化，避免不必要的开销 |
| 延迟导入 | 函数内 `import` | 命令独立，互不影响启动速度 |
| 闭包 | `on_cron_job`, `run_interactive` | 内嵌函数共享外层的 agent/bus 等对象 |
| 异步并发 | `asyncio.gather()` | 让 agent + channels 同时跑，不相互阻塞 |

### 程序命令总图

```
nanobot
├── onboard                    首次配置
├── gateway                    完整服务（多渠道 + agent + 定时任务）
├── agent -m "xxx"             单条消息模式
├── agent                      交互式会话模式
├── status                     查看配置状态
├── channels
│   ├── status                 查看各平台启用状态
│   └── login                  WhatsApp 扫码登录
└── provider
    └── login <name>           OAuth 登录（openai-codex / github-copilot）
```
