# 04 config/schema.py 完整解析

> 对应代码：`nanobot/config/schema.py`（413 行，整个项目的"数据蓝图"）
>
> 前置知识：先读 `00-整体架构概览.md`。

---

## 文件结构一览

```
schema.py
│
├── [基础类]                第 11-14 行    Base（所有配置类的父类）
│
├── [各 Channel 配置类]     第 17-217 行   每个平台一个类
│   ├── WhatsAppConfig
│   ├── TelegramConfig
│   ├── FeishuConfig
│   ├── DingTalkConfig
│   ├── DiscordConfig
│   ├── MatrixConfig
│   ├── EmailConfig
│   ├── MochatConfig（含子类 MochatMentionConfig / MochatGroupRule）
│   ├── SlackConfig（含子类 SlackDMConfig）
│   └── QQConfig
│   └── ChannelsConfig      所有平台配置的"总容器"
│
├── [Agent 配置类]          第 220-236 行
│   ├── AgentDefaults       模型、温度、token 上限等
│   └── AgentsConfig        包含 AgentDefaults
│
├── [Provider 配置类]       第 239-266 行
│   ├── ProviderConfig      单个 provider（api_key / api_base）
│   └── ProvidersConfig     所有 provider 的"总容器"
│
├── [其他服务配置类]        第 269-322 行
│   ├── HeartbeatConfig     心跳服务（间隔时间）
│   ├── GatewayConfig       网关（端口、心跳配置）
│   ├── WebSearchConfig     网页搜索（Brave API key）
│   ├── WebToolsConfig      网络工具（代理 + 搜索）
│   ├── ExecToolConfig      Shell 执行（超时）
│   ├── MCPServerConfig     MCP 工具服务器
│   └── ToolsConfig         所有工具配置的总容器
│
└── [根配置类]              第 325-412 行
    └── Config              最顶层，包含所有子配置 + 辅助方法
        ├── workspace_path  属性：返回展开后的工作目录路径
        ├── _match_provider 私有方法：根据模型名找对应 provider
        ├── get_provider    公共方法：获取 provider 配置对象
        ├── get_provider_name 公共方法：获取 provider 名称
        ├── get_api_key     公共方法：获取 API key
        └── get_api_base    公共方法：获取 API base URL
```

---

## 一、这个文件是干什么的

你的 `~/.nanobot/config.json` 文件长这样：

```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.1-codex",
      "maxTokens": 8192
    }
  },
  "providers": {
    "deepseek": {
      "apiKey": "sk-xxx"
    }
  }
}
```

`schema.py` 就是这个 JSON 的**骨架图**：定义了有哪些字段、每个字段是什么类型、默认值是什么。

加载配置时，Pydantic 会：
1. 读取 JSON 文件
2. 对照 schema 验证每个字段的类型
3. 填充没写的字段用默认值
4. 返回一个强类型的 Python 对象

---

## 二、Pydantic 基础（这个文件的核心工具）

**Pydantic** 是 Python 里最流行的数据验证库，专门用来定义和验证数据结构。

### 2.1 BaseModel：定义数据结构

```python
from pydantic import BaseModel, Field

class TelegramConfig(BaseModel):
    enabled: bool = False          # 字段名: 类型 = 默认值
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
```

和普通 Python 类的区别：

| 普通 class | Pydantic BaseModel |
|------------|-------------------|
| 手动写 `__init__` | 自动生成 |
| 运行时不检查类型 | 创建对象时自动验证类型 |
| 无默认值验证 | 可以定义复杂验证规则 |
| 不能直接转 JSON | `.model_dump()` 一行搞定 |

### 2.2 Field()：给字段加特殊配置

```python
allow_from: list[str] = Field(default_factory=list)
```

为什么不直接写 `allow_from: list[str] = []`？

**Python 的坑**：可变对象（list、dict）作为默认值，所有实例**共享同一个**列表！

```python
# 危险写法（所有 TelegramConfig 共享一个 list）
allow_from: list[str] = []

# 正确写法（每次创建新实例都调用 list() 生成一个新列表）
allow_from: list[str] = Field(default_factory=list)
```

`default_factory` 接受一个**函数**（这里是 `list`），每次创建实例时调用它生成默认值。

---

## 三、Base 类（第 11-14 行）

```python
from pydantic.alias_generators import to_camel

class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
```

### 功能/架构

这是**所有配置类的父类**（除了根 `Config` 类）。它解决了一个问题：

Python 习惯用 `snake_case`（下划线），而 JSON 配置文件习惯用 `camelCase`（驼峰）：

```python
# Python 里的字段名
allow_from: list[str]
bridge_url: str

# config.json 里对应的 key
"allowFrom": [...]
"bridgeUrl": "ws://..."
```

`alias_generator=to_camel` 让 Pydantic 自动把 `snake_case` 转换成 `camelCase` 来读取 JSON，`populate_by_name=True` 同时也接受原始 `snake_case` 写法。

### Python 语法：类继承

```python
class Base(BaseModel):          # Base 继承自 BaseModel
    ...

class TelegramConfig(Base):     # TelegramConfig 继承自 Base
    ...
```

括号里写父类名，子类自动拥有父类的所有属性和方法。

```
BaseModel（Pydantic 提供）
    └── Base（项目自定义，加上 camelCase 支持）
            ├── WhatsAppConfig
            ├── TelegramConfig
            ├── ChannelsConfig
            └── ... 所有配置类
```

---

## 四、各 Channel 配置类（第 17-217 行）

每个平台一个类，结构都相似，举两个典型例子：

### 4.1 TelegramConfig

```python
class TelegramConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None
    reply_to_message: bool = False
```

字段说明：
- `enabled`：这个渠道是否开启（默认关）
- `token`：Bot Token，从 Telegram 的 @BotFather 拿到
- `allow_from`：白名单，只有列表里的用户 ID 才能发消息（空列表 = 不限制）
- `proxy`：代理地址（国内访问 Telegram 必须）
- `reply_to_message`：回复时是否引用原消息

### 4.2 EmailConfig（最复杂的渠道）

```python
class EmailConfig(Base):
    enabled: bool = False
    # IMAP（接收邮件）
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True
    # SMTP（发送邮件）
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_password: str = ""
    smtp_use_tls: bool = True
    from_address: str = ""
    # 行为
    poll_interval_seconds: int = 30   # 每 30 秒检查一次新邮件
    mark_seen: bool = True            # 处理后标记为已读
    max_body_chars: int = 12000       # 邮件正文最多读多少字符
```

邮件渠道需要两套配置的原因：
- **IMAP**：用来收邮件（nanobot 定时轮询收件箱）
- **SMTP**：用来发邮件（nanobot 回复用户时）

### 4.3 ChannelsConfig（所有平台的总容器）

```python
class ChannelsConfig(Base):
    send_progress: bool = True     # 是否把 AI 思考进度实时发给用户
    send_tool_hints: bool = False  # 是否显示"正在调用工具..."的提示
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    # ... 所有平台
```

**嵌套模型**：`whatsapp` 字段的类型是 `WhatsAppConfig`，Pydantic 支持无限嵌套。这样 `config.channels.telegram.token` 就能直接访问 Telegram 的 token，就像访问普通对象属性一样。

---

## 五、Provider 配置类（第 239-266 行）

```python
class ProviderConfig(Base):
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None

class ProvidersConfig(Base):
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai:    ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek:  ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    # ... 十几个 provider
```

**设计思路**：每个 provider 都长一样（api_key + api_base + headers），所以共用一个 `ProviderConfig` 类，`ProvidersConfig` 只是把它们组合在一起。

对应 `config.json` 里的：
```json
{
  "providers": {
    "deepseek": { "apiKey": "sk-xxx" },
    "openrouter": { "apiKey": "sk-or-xxx" }
  }
}
```

### Python 语法：`dict[str, str]`

```python
extra_headers: dict[str, str] | None = None
```

- `dict[键类型, 值类型]` 是泛型注解（Python 3.9+）
- `dict[str, str]` 表示：键是字符串，值也是字符串
- 对比：`dict[str, int]` 表示值是整数

---

## 六、Agent 配置类（第 220-236 行）

```python
class AgentDefaults(Base):
    workspace: str = "~/.nanobot/workspace"
    model: str = "anthropic/claude-opus-4-5"   # 默认模型
    max_tokens: int = 8192
    temperature: float = 0.1                    # 越低越保守/确定
    max_tool_iterations: int = 40               # 最多调用工具几次
    memory_window: int = 100                    # 保留最近多少条历史
    reasoning_effort: str | None = None         # 思维链：low/medium/high
```

这些字段在 `commands.py` 的 `gateway` 函数里被读取传给 `AgentLoop`：

```python
agent = AgentLoop(
    model=config.agents.defaults.model,
    temperature=config.agents.defaults.temperature,
    max_tokens=config.agents.defaults.max_tokens,
    max_iterations=config.agents.defaults.max_tool_iterations,
    memory_window=config.agents.defaults.memory_window,
    ...
)
```

---

## 七、根配置类 Config（第 325-412 行）

```python
class Config(BaseSettings):
    agents:    AgentsConfig   = Field(default_factory=AgentsConfig)
    channels:  ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway:   GatewayConfig  = Field(default_factory=GatewayConfig)
    tools:     ToolsConfig    = Field(default_factory=ToolsConfig)

    model_config = ConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")
```

### 为什么用 `BaseSettings` 而不是 `BaseModel`？

`BaseSettings` 是 Pydantic 的特殊基类，额外支持**从环境变量读取配置**：

```bash
# 环境变量会自动覆盖 config.json 里的值
NANOBOT_AGENTS__DEFAULTS__MODEL="gpt-4o"  nanobot agent
```

- `env_prefix="NANOBOT_"`：所有环境变量必须以 `NANOBOT_` 开头
- `env_nested_delimiter="__"`：双下划线表示嵌套层级

### Python 语法：`@property` 装饰器

```python
@property
def workspace_path(self) -> Path:
    return Path(self.agents.defaults.workspace).expanduser()
```

- `@property` 让方法可以**像属性一样访问**（不用加括号）
- 调用：`config.workspace_path`（不是 `config.workspace_path()`）
- `Path("~/.nanobot/workspace").expanduser()` 把 `~` 展开成实际路径（`C:\Users\你的名字\.nanobot\workspace`）

没有 `@property` 就得每次手动写 `.expanduser()`，有了它封装起来用起来更简洁。

---

## 八、`_match_provider()` 方法（第 339-379 行）

```python
def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
    ...
    forced = self.agents.defaults.provider
    if forced != "auto":
        p = getattr(self.providers, forced, None)
        return (p, forced) if p else (None, None)
    
    # 自动匹配逻辑...
    for spec in PROVIDERS:
        p = getattr(self.providers, spec.name, None)
        if p and model_prefix and normalized_prefix == spec.name:
            ...
```

**功能/架构**：根据配置的模型名，自动推断应该用哪个 provider。

匹配优先级（从高到低）：

```
1. config.json 里明确指定了 provider → 直接用
2. 模型名有明确前缀（如 "deepseek/..."）→ 按前缀匹配
3. 模型名包含关键词（如 "claude" → anthropic）→ 按关键词匹配
4. 找有 api_key 的 provider 兜底
```

### Python 语法：`getattr(obj, name, default)`

```python
p = getattr(self.providers, spec.name, None)
```

- 等价于 `self.providers.spec.name`，但 `spec.name` 是**变量**，不是固定属性名
- `getattr(对象, "属性名字符串", 默认值)` 动态访问属性
- 找不到属性时返回第三个参数（这里是 `None`），不报错

```python
# 比较：
config.providers.deepseek        # 只能访问固定名字
getattr(config.providers, "deepseek", None)  # 能用变量访问

name = "deepseek"
getattr(config.providers, name, None)  # 等同于上面
```

### Python 语法：`tuple` 返回多个值

```python
def _match_provider(...) -> tuple["ProviderConfig | None", str | None]:
    return (p, spec.name)   # 返回一个元组
    
# 调用方解包：
p, name = self._match_provider(model)
```

- 函数可以返回一个元组，调用方用多个变量接收
- `tuple[A, B]` 注解说明：这是一个有两个元素的元组，第一个是 A 类型，第二个是 B 类型

---

## 九、整体配置层次结构图

```
Config（根，BaseSettings，可从环境变量覆盖）
├── agents: AgentsConfig
│   └── defaults: AgentDefaults
│       ├── model: str
│       ├── temperature: float
│       ├── max_tokens: int
│       └── memory_window: int
│
├── channels: ChannelsConfig
│   ├── send_progress: bool
│   ├── telegram: TelegramConfig
│   │   ├── enabled: bool
│   │   ├── token: str
│   │   └── allow_from: list[str]
│   ├── whatsapp: WhatsAppConfig
│   └── ... 其他平台
│
├── providers: ProvidersConfig
│   ├── deepseek: ProviderConfig
│   │   └── api_key: str
│   ├── openrouter: ProviderConfig
│   └── ... 其他 provider
│
├── gateway: GatewayConfig
│   ├── port: int = 18790
│   └── heartbeat: HeartbeatConfig
│       ├── enabled: bool = True
│       └── interval_s: int = 1800
│
└── tools: ToolsConfig
    ├── web: WebToolsConfig
    │   └── search: WebSearchConfig
    ├── exec: ExecToolConfig
    └── mcp_servers: dict[str, MCPServerConfig]
```

---

## 十、Python 语法汇总

| 语法 | 代码示例 | 含义 |
|------|---------|------|
| 类继承 | `class TelegramConfig(Base)` | 继承父类的属性和方法 |
| Pydantic 字段 | `token: str = ""` | 带默认值的类型注解字段 |
| `Field(default_factory=)` | `Field(default_factory=list)` | 可变对象的安全默认值 |
| `@property` | `@property def workspace_path` | 让方法像属性一样调用 |
| `getattr()` | `getattr(obj, name, None)` | 用字符串动态访问属性 |
| 泛型 dict | `dict[str, str]` | 键值都是字符串的字典 |
| 泛型 list | `list[str]` | 元素是字符串的列表 |
| `tuple` 返回值 | `-> tuple[A, B]` | 函数返回两个值 |
| `Literal` | `Literal["open", "mention"]` | 只能是这几个固定值之一 |

### Python 语法：`Literal` 类型

```python
from typing import Literal

group_policy: Literal["open", "mention", "allowlist"] = "open"
```

- `Literal[...]` 限制这个字段只能取括号里列出的值
- 填其他值时 Pydantic 会在运行时报错
- 比普通 `str` 更安全，IDE 也能给出提示
