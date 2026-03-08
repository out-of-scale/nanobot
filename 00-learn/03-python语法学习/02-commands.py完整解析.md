# 02 `cli/commands.py` 完整解析

> **文件路径**：`nanobot/cli/commands.py`（918 行）  
> **核心职责**：定义所有 `nanobot` 终端命令，是整个项目的"调度总台"

---

## 一、文件整体架构

```
commands.py
├── [1-31]    imports + 全局对象创建
├── [37-131]  私有辅助函数（终端 I/O 处理）
├── [135-148] @app.callback() → 全局钩子（处理 --version）
├── [156-195] onboard()     → nanobot onboard
├── [201-236] _make_provider() → 内部函数，选 LLM 提供商
├── [244-419] gateway()     → nanobot gateway（完整服务）
├── [429-597] agent()       → nanobot agent（直接对话）
├── [605-700] channels_*()  → nanobot channels status/login
├── [795-830] status()      → nanobot status
└── [837-917] provider_*()  → nanobot provider login（OAuth）
```

**架构模式**：所有命令函数都只是"组装入口"——读配置、创建各模块实例、启动事件循环。  
真正的业务逻辑分散在 `agent/`、`bus/`、`channels/` 等子模块里。

---

## 二、全局对象（模块级变量）

```python
app = typer.Typer(name="nanobot", no_args_is_help=True)
console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

_PROMPT_SESSION: PromptSession | None = None   # 交互式输入会话
_SAVED_TERM_ATTRS = None                        # 终端原始状态备份
```

**语法：下划线前缀约定**
- `_xxx`：单下划线 = 模块内部私有变量，不对外暴露
- `__xxx`：双下划线 = Python 特殊变量（如 `__version__`）

**语法：类型注解**
```python
_PROMPT_SESSION: PromptSession | None = None
#                ↑类型           ↑或者None  ↑初始值
```
`A | B` 是 Python 3.10+ 写法，表示"类型 A 或类型 B"，等价于旧写法 `Optional[A]`。

---

## 三、私有辅助函数（终端 I/O）

### `_flush_pending_tty_input()`

**作用**：AI 思考输出期间用户乱按的键，统一丢掉，防止污染下次输入。

```python
def _flush_pending_tty_input() -> None:
    try:
        fd = sys.stdin.fileno()    # 标准输入的文件描述符（整数编号）
        if not os.isatty(fd):      # 如果不是真终端（如管道/文件重定向），不处理
            return
    except Exception:
        return

    try:
        import termios             # Linux/Mac 专用，写在函数内避免 Windows 报错
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass                       # Windows 跳过，用下面的备用方案

    try:
        while True:                # 备用方案：逐批读走缓冲区的字符
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return
```

**语法点：**
| 语法 | 说明 |
|------|------|
| `-> None` | 返回类型注解，表示函数无返回值 |
| `import termios`（在函数内）| 延迟导入 + try/except = 跨平台兼容技巧 |
| `pass` | 占位符，"什么都不做"，保持代码块语法合法 |
| `ready, _, _` | 元组解包，`_` 表示"我不关心这个值" |

---

### `_restore_terminal()`

**作用**：程序退出时恢复终端到最初状态（如重新打开回显、行缓冲）。

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

---

### `_init_prompt_session()`

**作用**：初始化 `prompt_toolkit` 输入会话，支持历史记录、箭头翻历史。

```python
def _init_prompt_session() -> None:
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS   # 声明要修改全局变量

    history_file = Path.home() / ".nanobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        multiline=False,
    )
```

**语法点：`global` 关键字**
```python
global _PROMPT_SESSION
```
- 函数内默认只能**读**全局变量
- 要**修改**全局变量必须先声明 `global 变量名`

**语法点：`Path` 的 `/` 运算符**
```python
Path.home() / ".nanobot" / "history" / "cli_history"
# 等价于（Windows）：C:\Users\你的用户名\.nanobot\history\cli_history
```
- `Path` 类重载了 `/` 运算符（运算符重载）
- 跨平台安全拼接路径，自动处理 `/` 和 `\` 的差异

**语法点：`mkdir` 参数**
```python
history_file.parent.mkdir(parents=True, exist_ok=True)
# parents=True  → 连父目录一起创建（不存在时）
# exist_ok=True → 已存在时不报错
```

---

### `_print_agent_response()` 和 `_is_exit_command()`

```python
def _print_agent_response(response: str, render_markdown: bool) -> None:
    content = response or ""                                     # ①
    body = Markdown(content) if render_markdown else Text(content)  # ②
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
    console.print(body)

def _is_exit_command(command: str) -> bool:
    return command.lower() in EXIT_COMMANDS                      # ③
```

**语法点：**

① **`or` 的短路特性**：`response or ""` → 若 `response` 为 `None`/空，返回 `""`；否则返回 `response` 本身。

② **三元表达式**：`A if 条件 else B`，等同于 if/else 但更紧凑，适合简单二选一赋值。

③ **`in` 运算符 + set**：检查字符串是否在集合里，set 的查找是 O(1)，比 list 快。

---

### `_read_interactive_input_async()`（异步函数）

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

**语法点：`async/await`（异步编程）**
```python
async def 函数名():   # 定义异步函数
    await 某异步操作  # 等待，期间不阻塞其他任务
```
- `async def` = 这个函数是异步的，调用后不立刻执行，返回一个"协程对象"
- `await` = 暂停这里，等异步操作完成后继续，期间 CPU 可以去做别的事
- 场景：等待用户输入、网络请求、文件 I/O 时用异步，避免程序"卡死"

**语法点：`raise XXX from exc`**
```python
except EOFError as exc:
    raise KeyboardInterrupt from exc
```
- 把 `EOFError` 转换成 `KeyboardInterrupt` 继续抛出
- `from exc` 保留原始错误链（traceback 里能看到两个错误）

---

## 四、`@app.callback()` — 全局钩子

```python
def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()    # 打印版本后立即退出程序

@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    pass
```

**`@app.callback()` vs `@app.command()`：**
| | `@app.callback()` | `@app.command()` |
|---|---|---|
| 触发时机 | 执行**任意**子命令前都会先调用 | 只在该命令被选中时调用 |
| 用途 | 全局选项（如 `--version`、`--verbose`）| 具体命令的实现 |

**`is_eager=True`**：告诉 Typer 这个选项要优先处理（不等其他参数），所以 `--version` 不需要指定子命令也能用。

---

## 五、`onboard()` — 初始化配置

**命令**：`nanobot onboard`  
**作用**：第一次使用时，生成 `~/.nanobot/config.json` 并创建工作区目录。

```python
@app.command()
def onboard():
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        if typer.confirm("Overwrite?"):           # 交互式确认
            config = Config()
            save_config(config)
        else:
            config = load_config()
            save_config(config)                   # 保留旧值，补充新字段
    else:
        save_config(Config())                     # 首次：直接生成默认配置

    workspace = get_workspace_path()
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    sync_workspace_templates(workspace)           # 复制内置模板文件
```

**语法点：函数内部的 `import`（延迟导入）**
```python
def onboard():
    from nanobot.config.loader import get_config_path, ...
```
- 仅在命令被调用时才导入，启动更快（不需要的命令的依赖不加载）
- nanobot 所有命令都采用这种模式

**流程图：**
```
nanobot onboard
    ├── config_path 存在？
    │   ├── 是 → 问用户：覆盖还是刷新？
    │   │         ├── 覆盖 → Config() 重置为默认值
    │   │         └── 刷新 → load 旧值 + save（补充新字段）
    │   └── 否 → 直接 save_config(Config())
    ├── 创建 workspace 目录
    └── sync_workspace_templates() 复制模板文件
```

---

## 六、`_make_provider()` — 选择 LLM 提供商

```python
def _make_provider(config: Config):
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

    # 默认：LiteLLM（支持 OpenAI/Claude/DeepSeek 等几乎所有模型）
    return LiteLLMProvider(...)
```

**架构意义**：这是一个工厂函数（Factory Pattern）——根据配置决定创建哪种 Provider 对象，调用方不需要知道具体是哪个类。

**语法点：`X.startswith("前缀")`**
```python
model.startswith("openai-codex/")   # 判断字符串是否以某个前缀开头
```

**语法点：条件表达式 + `or`**
```python
api_key=p.api_key if p else "no-key"
api_base=config.get_api_base(model) or "http://localhost:8000/v1"
# 若 get_api_base() 返回 None 或空，则用默认地址
```

---

## 七、`gateway()` — 完整服务启动

**命令**：`nanobot gateway`  
**作用**：启动完整的 AI 机器人服务，接入 Telegram/飞书/WhatsApp 等平台。

### 组装顺序（重要！）

```python
# 1. 读配置
config = load_config()

# 2. 基础设施层
bus = MessageBus()                          # 消息总线（所有模块的通信枢纽）
provider = _make_provider(config)           # LLM 提供商
session_manager = SessionManager(...)       # 会话持久化

# 3. 业务层
cron = CronService(cron_store_path)         # 定时任务
agent = AgentLoop(bus, provider, ...)       # AI Agent 大脑

# 4. 设置 cron 回调（需要 agent 实例，所以在 agent 之后）
cron.on_job = on_cron_job

# 5. 入口层
channels = ChannelManager(config, bus)      # 平台适配器（Telegram/飞书等）
heartbeat = HeartbeatService(...)           # 心跳（主动触发任务）

# 6. 启动（全部并发运行）
await asyncio.gather(agent.run(), channels.start_all())
```

**架构关系图：**
```
外部平台消息 → ChannelManager → MessageBus → AgentLoop → LLM Provider
                                     ↑
                              HeartbeatService（主动触发）
                              CronService（定时触发）
                                     ↓
外部平台         ← ChannelManager ← MessageBus ← AgentLoop（回复）
```

**语法点：嵌套函数（闭包）**
```python
async def on_cron_job(job: CronJob) -> str | None:
    # 这个函数定义在 gateway() 内部，可以直接访问外层的 agent、bus 变量
    response = await agent.process_direct(reminder_note, ...)
    await bus.publish_outbound(...)
```
- 定义在函数内部的函数叫**嵌套函数**
- 它可以"捕获"外层函数的变量（如 `agent`、`bus`），这叫**闭包（Closure）**
- 这里用闭包避免了把 `agent` 和 `bus` 当参数传来传去

**语法点：`asyncio.gather()`**
```python
await asyncio.gather(agent.run(), channels.start_all())
```
- 同时启动多个异步任务，全部**并发执行**
- 等效于"同时开了两条流水线"，两者都跑起来互不阻塞

**语法点：`try/finally`**
```python
async def run():
    try:
        await asyncio.gather(agent.run(), channels.start_all())
    except KeyboardInterrupt:
        console.print("\nShutting down...")
    finally:                          # 无论正常退出还是报错，finally 都会执行
        await agent.close_mcp()
        heartbeat.stop()
        cron.stop()
```
- `finally` 块保证资源释放，即使程序崩溃也会清理

---

## 八、`agent()` — 直接对话

**命令**：`nanobot agent`（交互）或 `nanobot agent -m "消息"`（单次）  
**作用**：直接用终端和 AI 对话。

### 两种模式

```python
if message:
    # 单次模式：发一条消息，拿到回复，退出
    async def run_once():
        response = await agent_loop.process_direct(message, session_id, ...)
        _print_agent_response(response, render_markdown=markdown)
    asyncio.run(run_once())
else:
    # 交互模式：loop 循环，每次读输入 → 发送 → 等待回复 → 打印
    asyncio.run(run_interactive())
```

### 交互模式的事件循环

```python
async def run_interactive():
    bus_task = asyncio.create_task(agent_loop.run())     # 后台跑 Agent
    outbound_task = asyncio.create_task(_consume_outbound())  # 后台监听输出

    while True:
        user_input = await _read_interactive_input_async()   # 等用户输入

        if _is_exit_command(user_input.strip()):
            break

        await bus.publish_inbound(InboundMessage(...))       # 发给 Agent

        with _thinking_ctx():             # 显示 "nanobot is thinking..." 动画
            await turn_done.wait()        # 等 Agent 回复完成

        _print_agent_response(turn_response[0], ...)         # 打印回复
```

**语法点：`asyncio.create_task()`**
```python
bus_task = asyncio.create_task(agent_loop.run())
```
- 把协程"丢到后台"，立即返回，主流程继续往下走
- Task 会在事件循环空闲时自动推进执行

**语法点：`asyncio.Event`**
```python
turn_done = asyncio.Event()
turn_done.set()    # 设置事件（标记为"完成"）
turn_done.clear()  # 清除事件（标记为"等待中"）
await turn_done.wait()  # 阻塞，直到事件被 set()
```
- `Event` 是异步版的"信号旗"，用来在不同协程之间传递"某件事已完成"的信号

**语法点：`signal.signal()`**
```python
def _exit_on_sigint(signum, frame):
    _restore_terminal()
    os._exit(0)

signal.signal(signal.SIGINT, _exit_on_sigint)
```
- 注册 Ctrl+C（SIGINT 信号）的处理函数
- 按下 Ctrl+C 时，不再走默认的 `KeyboardInterrupt`，而是调用 `_exit_on_sigint`

---

## 九、`channels` 子命令组

```python
channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")    # 注册为 app 的子命令组

@channels_app.command("status")
def channels_status(): ...    # → nanobot channels status

@channels_app.command("login")
def channels_login(): ...     # → nanobot channels login
```

**架构点：`add_typer()` 实现命令嵌套**
- `nanobot channels status` 其实是两层：`nanobot`（根）→ `channels`（子 Typer）→ `status`（命令）
- 这样可以把相关命令归组，`channels_app` 专管渠道相关命令

---

## 十、`provider login` — OAuth 登录系统

```python
_LOGIN_HANDLERS: dict[str, callable] = {}    # 注册表：名字 → 处理函数

def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator

@_register_login("openai_codex")    # 注册 openai_codex 的登录处理函数
def _login_openai_codex() -> None:
    ...

@_register_login("github_copilot")  # 注册 github_copilot 的登录处理函数
def _login_github_copilot() -> None:
    ...
```

**语法点：装饰器工厂（带参数的装饰器）**
```python
@_register_login("openai_codex")
def _login_openai_codex(): ...
```
等价于：
```python
_login_openai_codex = _register_login("openai_codex")(_login_openai_codex)
```

`_register_login("openai_codex")` 返回一个 `decorator` 函数，再用这个 `decorator` 包装 `_login_openai_codex`。  
这是装饰器最复杂的用法：**装饰器工厂** = 返回装饰器的函数。

**架构模式：注册表（Registry Pattern）**
```
_LOGIN_HANDLERS = {
    "openai_codex":   _login_openai_codex,
    "github_copilot": _login_github_copilot,
}
```
- `provider_login()` 命令执行时，查表找到对应的处理函数再调用
- 新增 OAuth 提供商只需加 `@_register_login("xxx")` 装饰器，不修改 `provider_login()` 主逻辑
- 这是**开放-封闭原则**的体现：对扩展开放，对修改关闭

---

## 总结：commands.py 的设计思路

| 层次 | 职责 |
|------|------|
| **命令层**（`@app.command()`）| 解析参数、组装模块、启动事件循环 |
| **辅助函数层**（`_xxx()`）| 终端 I/O 处理、共用逻辑封装 |
| **业务层**（其他子模块）| 真正的功能实现（在 `agent/`、`bus/` 等目录里）|

**commands.py 本身不处理业务逻辑**，它只是把各个模块拼装起来，然后用 `asyncio.run()` 启动异步事件循环。这是典型的"**指挥官模式**"——只负责调度，不亲自干活。
