

## Git vs GitHub

| | Git | GitHub |
|---|---|---|
| 本质 | 工具（软件） | 网站（平台） |
| 功能 | 在本地记录代码修改历史 | 把 Git 仓库托管到云端 |
| 比喻 | 相机（拍照记录） | 相册网站（存储和分享） |

---

## 最常用的 6 个命令

```bash
git status              # 查看当前状态（哪些文件被修改了）
git pull                # 拉取最新代码（从 GitHub 同步到本地）
git log --oneline -10   # 查看最近 10 条提交历史
git add .               # 暂存所有改动
git commit -m "说明"    # 提交快照（本地保存）
git push                # 上传到 GitHub
git restore .           # 撤销所有未提交的修改
git reset HEAD~1        # 撤销最近一次 commit（改动保留，只取消快照）
```

---

## 核心工作流（不可打乱顺序）

```
改代码 → git add → git commit → git push
          ↑暂存      ↑本地快照     ↑上传GitHub
        （购物车）  （付款确认）  （寄出包裹）
```

**关键点**：
- `add` 后不 `commit`，`push` 不会包含这次改动
- 没有 `commit` 就无法 `push`，顺序强制不可跳过
- 多个文件用 `git add .` 一次暂存所有，不用逐个写

---

## Fork、Clone、PR 的关系

```
HKUDS/nanobot（原仓库）
    ↓ Fork（GitHub 网页点按钮）
out-of-scale/nanobot（我的副本，有完全控制权）
    ↓ git clone（下载到本地）
本地修改 → git add → git commit → git push
    ↓ Pull Request（PR）
请求原作者合并我的改动
```

- **Fork**：在 GitHub 上把别人的仓库复制到自己账号下
- **Clone**：把仓库下载到本地电脑
- **PR**：请求原作者审查并合并你的代码

---

## Sync fork

当原仓库（HKUDS/nanobot）有新更新时，点 GitHub 上的 **Sync fork** 按钮，把原仓库的新代码同步到自己的 Fork。

---

## git commit 的作用

commit 是给当前改动**拍一张快照**，永久保存在本地历史里。  
每条 commit 有唯一 ID（哈希值），可以随时回溯到任意历史版本。

提交说明规范（nanobot 项目使用的格式）：
```
fix(模块): 修复了某个 bug
feat: 新增了某个功能
docs: 修改了文档
chore: 杂务，不影响功能
```



---

## 分支（Branch）与合并（Merge）

分支 = 从主线分出去的独立开发线，改动互不影响。

```
main:        A → B → C
                      ↘
my-feature:            D → E  ← 在这里改代码，不影响 main
```

**常用命令：**
```bash
git checkout -b 分支名   # 创建并切换到新分支
git branch               # 查看所有分支（* 表示当前所在分支）
git checkout main        # 切回 main 分支
git merge 分支名         # 把指定分支的改动合并进当前分支
git branch -d 分支名     # 删除已合并的分支
```

**关键理解**：
- 创建分支时，复制当前 main 的全部代码
- 在分支上的改动不影响 main，完全隔离
- PR（Pull Request）本质上就是请求别人把你的**分支**合并进他们的 **main**

**真实团队开发流程：**
```
main（受保护）→ 每人创建 feature 分支 → 开发测试 → 发 PR → Code Review → merge 回 main
```

---

## 项目架构：入口理解

### 项目结构三个关键文件

| 文件 | 作用 | 类比 |
|------|------|------|
| `pyproject.toml` | 描述如何安装、依赖是什么、入口命令是什么 | 工商注册信息（给 pip 看） |
| `nanobot/__init__.py` | 声明这个文件夹是 Python 包，导出版本号 | 门牌号（给 Python 解释器看） |
| `nanobot/__main__.py` | 支持 `python -m nanobot` 启动方式 | 侧门（和 `nanobot` 命令等效） |

### 程序启动流程

```
输入 "nanobot" 命令
      ↓
pyproject.toml [scripts] 指向：
nanobot = "nanobot.cli.commands:app"
      ↓
执行 nanobot/cli/commands.py → app()
      ↓（等效）
nanobot/__main__.py → app()（python -m nanobot 时走这条路）
```

`__init__.py` 是**被动顺带执行的**（import 包时），不是启动主角。

### pyproject.toml 核心依赖速查

| 依赖 | 用途 |
|------|------|
| `litellm` | 统一调用各家 LLM（OpenAI/Claude/DeepSeek）|
| `pydantic` | 数据验证和强类型配置 |
| `typer` | CLI 命令行框架 |
| `loguru` | 日志库 |
| `python-telegram-bot` | Telegram SDK |
| `lark-oapi` | 飞书 SDK |
| `mcp` | MCP 工具协议 |
| `croniter` | 定时任务 cron 表达式解析 |

### `pip install -e .` 解析

```bash
cd nanobot       # 必须先 cd！因为 "." 代表当前目录，pip 从这里找 pyproject.toml
pip install -e . # -e = editable 模式，改代码立刻生效，无需重新安装
```

### setup.py vs pyproject.toml（新旧对比）

| | `setup.py`（旧） | `pyproject.toml`（新）|
|---|---|---|
| 格式 | Python 脚本，可执行代码 | 纯静态 TOML 配置 |
| 问题 | 安装前需运行代码，有安全风险 | 纯数据，工具可安全静态分析 |
| 现状 | 不再推荐 | 官方推荐（nanobot 使用此方式）|

### bridge/ 目录（WhatsApp 专用）

其他平台有官方 Python SDK，可直接 import。  
WhatsApp 无官方机器人 API，只能用 Node.js 的 `whatsapp-web.js` 模拟浏览器登录。

```
[Python 核心] ←── WebSocket ──→ [Node.js Bridge (bridge/)]
                                        ↕
                                whatsapp-web.js
                                        ↕
                                 WhatsApp Web
```

`bridge/` 本质是独立的 Node.js 服务（TypeScript 写），充当"翻译官"，通过 WebSocket 与 Python 主程序通信。
