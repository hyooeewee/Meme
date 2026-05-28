# Meme — 中心化记忆系统

一个基于知识图谱的个人记忆管理系统，为 AI 助手提供持久化、分层、可检索的记忆能力。

## 特性

- **三层记忆模型**：Working（每次加载）→ Archive（图遍历检索）→ Cold（BM25 搜索可命中，可回温）
- **知识图谱**：记忆之间通过 `[[link]]` 互相引用，支持 BFS 图遍历检索
- **重要性衰减**：长期未访问的记忆自动降级，频繁使用的自动升级
- **错误纠正**：记住 CLI 命令变更等操作性纠错，避免重复犯错
- **知识摄入**：从 URL、文档、会话中学习并内化为记忆
- **加密保险库**：敏感记忆通过 macOS Keychain + AES-256 加密存储
- **Obsidian 集成**：通过 symlink 在 Obsidian 中可视化记忆图谱
- **Claude Code 集成**：通过 hooks 自动注入记忆上下文

## 前置要求

- Python 3.11+
- macOS / Linux
- `uv`（推荐）或 `pip`

## 安装

### 方式 1：一键安装

```bash
curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash
```

### 方式 2：uv（推荐）

```bash
uv tool install memectl
meme setup
```

### 方式 3：pip

```bash
pip install memectl
meme setup
```

### 方式 4：源码安装（开发）

```bash
git clone https://github.com/hyooeewee/Meme.git
cd Meme
uv pip install -e .
meme setup --dev
```

`--dev` 标志会用符号链接替代复制，源码修改立即生效。

### 安装选项

```bash
# 基础安装
meme setup

# 安装 + 从现有 Claude Code 记忆迁移
meme setup --migrate

# 安装 + 设置 Obsidian 集成
meme setup --obsidian ~/Softwares/Obsidian/Meme/

# 完整安装
meme setup --migrate --obsidian ~/Softwares/Obsidian/Meme/
```

### 安装后配置

安装完成后，将 CLI 加入 PATH（二选一）：

```bash
# 方式 1：Symlink（推荐）
ln -s ~/.meme/bin/meme /usr/local/bin/meme

# 方式 2：PATH
echo 'export PATH="$HOME/.meme/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

验证安装：

```bash
meme version
meme doctor
```

## 快速开始

```bash
# 添加记忆
meme add "使用 uv 管理 Python 依赖，不使用 pip" --type feedback --importance 0.8

# 搜索记忆
meme search "python 依赖"

# 列出所有记忆
meme list

# 按层级列出
meme list --tier working

# 图遍历检索（从某个记忆出发，沿关联扩展）
meme query mem_xxx

# 从 URL 学习
meme learn https://docs.example.com/guide

# 从本地文件学习
meme learn --file ./notes.md
```

## 命令参考

### 记忆管理

| 命令 | 说明 |
|------|------|
| `meme add "内容" [选项]` | 添加新记忆 |
| `meme list [选项]` | 列出记忆 |
| `meme search "关键词"` | BM25 关键词搜索 |
| `meme query mem_id` | 图遍历检索（核心命令） |
| `meme edit mem_id` | 编辑记忆内容和元数据 |
| `meme delete mem_id` | 删除记忆 |
| `meme forget mem_id [选项]` | 遗忘记忆（软/硬/彻底） |

**add 选项：**
- `--type TYPE` — 类型：feedback / project / user / reference / knowledge / correction
- `--importance N` — 重要性 0.0~1.0（默认 0.5）
- `--tags TAG1,TAG2` — 标签
- `--links mem_a,mem_b` — 关联记忆
- `--sensitive` — 加密存储到 vault

**list 选项：**
- `--tier TIER` — 按层级过滤：working / archive / cold
- `--tag TAG` — 按标签过滤
- `--sort ORDER` — 排序：importance / recent / heat
- `--format FORMAT` — 输出格式：text / json

**forget 选项：**
- `--hard` — 从文件系统彻底删除
- `--hard --purge` — 彻底删除 + 清理 git history

### 学习与摄入

| 命令 | 说明 |
|------|------|
| `meme learn <url>` | 从 URL 抓取内容，提炼为记忆 |
| `meme learn --file <path>` | 从本地文件提取，提炼为记忆 |

### 生命周期

| 命令 | 说明 |
|------|------|
| `meme decay [--dry-run]` | 执行重要性衰减扫描 |
| `meme promote mem_id` | 手动提升到 working 层 |
| `meme demote mem_id` | 手动降级 |
| `meme warm mem_id` | 将 cold 记忆回温到 archive |
| `meme link mem_a mem_b` | 创建记忆关联 |
| `meme suggest-links` | 基于使用模式建议新关联 |
| `meme daydream [--dry-run] [--apply]` | 语义聚类 + 链接整合 |
| `meme dream` | 自动夜间整理（读取配置） |
| `meme heat` | 显示当前会话热度 |

### 迁移

| 命令 | 说明 |
|------|------|
| `meme import --from claude` | 从 Claude Code 项目记忆迁移 |
| `meme import --from claude-global` | 从 Claude Code 全局配置迁移 |
| `meme import --from codex [--path]` | 从 Codex 迁移 |
| `meme sync --incremental` | 增量同步（检测源文件变化） |

### 维护

| 命令 | 说明 |
|------|------|
| `meme doctor [--fix]` | 健康检查 + 自动修复 |
| `meme config [--get KEY] [--set KEY=VAL] [--edit]` | 查看或修改配置 |
| `meme backup` | 手动备份 |
| `meme gc` | 清理旧备份 |
| `meme reindex` | 重建 index.json + graph.json |
| `meme stats` | 统计信息 |
| `meme export [--format json\|md]` | 导出所有记忆 |

### 系统

| 命令 | 说明 |
|------|------|
| `meme version` | 显示当前版本 + 检测最新版本 |
| `meme upgrade [--check] [--force]` | 自我升级 |
| `meme changelog` | 查看版本变更历史 |
| `meme uninstall [--keep-data]` | 卸载 Meme |

## 记忆类型

| 类型 | 用途 | 默认重要性 |
|------|------|-----------|
| `feedback` | 用户对 AI 行为的纠正和偏好 | 0.6 |
| `project` | 项目相关的上下文和状态 | 0.5 |
| `user` | 用户身份、背景、偏好 | 0.8 |
| `reference` | 外部资源的指针和摘要 | 0.4 |
| `knowledge` | 从文档/URL 学习的知识 | 0.5 |
| `correction` | CLI 命令变更等操作性纠错 | 0.9 |

## 三层记忆模型

```
┌─────────────────────────────────────────────────┐
│  Working (importance >= 0.8)                     │
│  每次会话自动加载，token 预算 2000               │
├─────────────────────────────────────────────────┤
│  Archive (0.2 <= importance < 0.8)               │
│  图遍历检索时按距离衰减加载                      │
│  load_weight = importance × (0.4 ^ distance)     │
├─────────────────────────────────────────────────┤
│  Cold (importance < 0.2)                         │
│  BM25 搜索可命中，连续 3 次命中自动回温          │
└─────────────────────────────────────────────────┘
```

## 图遍历检索

查询命中一个记忆节点时，沿知识图谱 BFS 扩展：

```
查询 "docker 权限问题"
  │
  ├─ 命中: feedback_docker_permission.md (distance: 0) → 全量加载
  │
  ├─ 1 级连接 (distance: 1) → 加载摘要
  │   ├─ install_permission.md
  │   └─ project_ecoctrl.md
  │
  └─ 2 级连接 (distance: 2) → 仅标签
      └─ knowledge_docker.md
```

## Obsidian 集成

在 Obsidian vault 中创建 symlink 指向 `~/.meme/`：

```bash
ln -s ~/.meme/ ~/Softwares/Obsidian/Meme/
```

然后在 Obsidian 中打开该 vault，即可：
- 使用 `[[wiki-link]]` 语法在记忆之间导航
- 在图谱视图中可视化整个记忆网络
- 在反向链接面板查看哪些记忆引用了当前记忆

## Claude Code 集成

`meme setup` 自动注册三个 hooks：

| Hook | 触发时机 | 行为 |
|------|----------|------|
| SessionStart | 会话开始 | 加载 working 记忆 + 纠正记忆 |
| UserPromptSubmit | 用户输入 | 关键词搜索 → 图遍历 → 注入相关记忆 |
| SessionEnd | 会话结束 | 写回 access_count，自动升降级 |

## 加密保险库

敏感记忆（API key、密码等）使用 macOS Keychain + AES-256 加密：

```bash
# 添加加密记忆
meme add "API key: sk-xxx" --sensitive --type knowledge

# 搜索时只显示摘要，解密需要 Keychain 授权
meme search "api key"
```

## 开发

```bash
# 开发模式安装（符号链接而非复制）
uv pip install -e .
meme setup --dev

# 运行测试
uv run pytest tests/ -v

# 带覆盖率运行
uv run pytest tests/ -v --cov=src/meme --cov-report=term-missing
```

## 目录结构

```
~/.meme/
├── MEMORY.md                    # 主索引
├── working/                     # 第一层：每次加载
├── archive/                     # 第二层：图遍历加载
│   ├── projects/
│   ├── feedback/
│   └── knowledge/
├── cold/                        # 第三层：仅搜索
├── vault/                       # 加密记忆
├── backups/                     # tar.gz 备份
├── meta/
│   ├── index.json               # 全量索引
│   ├── graph.json               # 邻接表
│   └── session_heat.json        # 会话热度（临时文件）
└── bin/
    ├── meme                     # CLI 入口
    ├── query.sh                 # UserPromptSubmit hook
    ├── session_start.sh         # SessionStart hook
    └── session_end.sh           # SessionEnd hook
```

## 卸载

```bash
# 卸载但保留数据
meme uninstall --keep-data

# 完全卸载
meme uninstall
```

## 许可证

MIT
