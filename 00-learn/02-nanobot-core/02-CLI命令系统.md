# 02 nanobot CLI 命令系统（commands.py）

## typer 注册机制

`nanobot` 命令名来自两处配置的组合：

```python
# pyproject.toml 定义可执行文件名
nanobot = "nanobot.cli.commands:app"

# commands.py 里 app 就是根命令对象
app = typer.Typer(name="nanobot", ...)
```

子命令通过 `@app.command()` 装饰器自动注册，**函数名即命令名**：

```python
@app.command()
def gateway(): ...   # → nanobot gateway

@app.command()
def agent(): ...     # → nanobot agent

@app.command()
def onboard(): ...   # → nanobot onboard
```

---

## `@app.callback()` vs `if __name__ == "__main__"`

| | `@app.callback()` | `if __name__ == "__main__"` |
|---|---|---|
| **触发时机** | 每次执行任意子命令时（全局钩子）| 直接运行该 `.py` 文件时 |
| **属于谁** | typer 框架机制 | Python 语言本身 |
| **作用** | 处理全局选项（如 `--version`）| 定义脚本入口 |

`main()` 里只有 `pass`，实际逻辑在 `version_callback` 里：

```python
def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()   # 打印版本后直接退出
```

---

## gateway() 组装顺序

`nanobot gateway` 是最重要的命令，内部按顺序组装所有核心模块：

```
config = load_config()              # 1. 读配置文件
bus = MessageBus()                  # 2. 创建消息总线
provider = _make_provider()         # 3. 选 LLM（OpenAI/Claude/本地等）
session_manager = SessionManager()  # 4. 会话管理
cron = CronService()                # 5. 定时任务
agent = AgentLoop(bus, provider)    # 6. Agent 大脑
channels = ChannelManager()         # 7. 各平台适配器
heartbeat = HeartbeatService()      # 8. 心跳服务
```

---

## nanobot 命令总览

| 命令 | 作用 |
|------|------|
| `nanobot onboard` | 初次配置，生成 `~/.nanobot/config.json` |
| `nanobot gateway` | 启动完整服务（接 Telegram/飞书等） |
| `nanobot agent -m "..."` | 单条消息模式，收到回复后退出 |
| `nanobot agent` | 交互式终端聊天（类 ChatGPT CLI）|
| `nanobot channels status` | 查看各平台连接状态 |
| `nanobot channels login` | WhatsApp 扫码登录 |
| `nanobot --version` | 查看版本号 |
