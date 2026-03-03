# Git & GitHub 学习笔记

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



test branch