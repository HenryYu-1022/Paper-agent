# Paper-agent

English version: [README.markdown](README.markdown)

---

## 为什么做这个项目

### 痛点

如果你是一个用 **Zotero** 管理文献的研究者或知识工作者，你大概率遇到过这些困扰：

1. **AI 编程代理读不了你的论文。** [OpenAI Codex](https://openai.com/index/codex/) 和 [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) 等工具在理解、检索和整合文本方面非常强大——但它们工作在*文本文件*上，不是 PDF。你整个 Zotero 文献库被锁死在一个 AI 代理完全无法原生读取的格式里。

2. **Zotero 对 LLM 来说是一个封闭孤岛。** Zotero 组织参考文献和阅读 PDF 很好用，但它不会把你的文献库暴露为可搜索的文本语料库。你没法问 LLM "找出我库里所有讨论方法 X 的论文并总结它们的思路"——因为内容根本不以 LLM 能消费的形式存在。

3. **手动做笔记无法规模化。** 你可以一篇一篇读论文、写笔记，但要在几十甚至上百篇论文之间做交叉引用——无论是写综述、写基金还是回答一个研究问题——这种信息整合的工作量用手工来做既枯燥又容易出错。

4. **文献管理器和个人知识库之间没有桥梁。** 即使你维护了一个 Markdown 笔记库（Obsidian、Logseq、Notion 或纯文件），也没有一条自动化管线能把你的 Zotero PDF 转成结构化 Markdown，让它们和你自己的笔记住在同一个生态系统里，一起被查询和检索。

### 核心思路

**把你整个 Zotero PDF 文献库转换成 Markdown 语料库，然后让 AI 代理来读。**

这里有两条独立的数据流——PDF 文件来自磁盘，collection 层级来自 Zotero 数据库：

```text
 ┌──────────────────┐
 │  zotero.sqlite   │─── collection 层级 ───┐
 │  (Zotero 数据库) │                        │
 └──────────────────┘                        ▼
                                      ┌─────────────────┐       ┌─────────────────┐
 ┌──────────────────┐   PDF 文件      │  .md 文件 +     │       │  Codex / Claude │
 │  PDF 目录        │────────────────▶│  YAML front-    │──────▶│  Code 搜索、    │
 │  (来自 attanger  │   paper-agent  │  matter + 全文  │       │  总结、整合     │
 │   或任何来源)    │  (PDF → MD     │  + collection   │       └─────────────────┘
 └──────────────────┘   + collection │  标签 & 镜像)   │               │
                         镜像)       └─────────────────┘               ▼
                                                                ┌─────────────────┐
                                                                │  同步到你的     │
                                                                │  个人笔记库     │
                                                                └─────────────────┘
```

**关键区分：** `zotero-attanger` 导出 PDF 到磁盘，但为了节约空间可能每个文件只放在一个文件夹下。`paper-agent` **直接从 `zotero.sqlite` 读取真实的 Zotero collection 层级**，所以即使一篇论文属于 5 个 collection，它都知道——并在 Markdown 库中为每一个都创建 symlink 镜像。

工作流程是：

1. **通过 Google Drive 同步 PDF** — 用 [zotero-attanger](https://github.com/HenryYu-1022/zotero-attanger) 把 Zotero 附件同步到 Google Drive，实现多设备访问。`zotero-attanger` 把 PDF 从 Zotero 不透明的 `storage/` 目录移到 Google Drive 文件夹中。为了节省空间，每个 PDF 只存在一个文件夹下——这完全没问题，因为 `paper-agent` 是直接从 `zotero.sqlite` 读取完整 collection 层级的。

2. **转换 & 镜像** — 把 `paper-agent` 指向同步后的 PDF 目录**和**你的 `zotero.sqlite`。它用 [Marker](https://github.com/datalab-to/marker) 批量转换每个 PDF，同时读取 Zotero 数据库找出每篇论文所属的所有 collection，在 YAML frontmatter 中写入 `zotero_collections` 标签，并创建 **symlink 镜像**让 Markdown 库反映完整的 Zotero 层级。

3. **用 AI 查询** — 在 Codex 或 Claude Code 里把生成的 Markdown 库作为 workspace 打开。现在你可以跨整个文献库提问：*"哪些论文提出了用于时间序列预测的 attention 架构？"*、*"总结一下我 `强化学习` collection 下所有论文的实验设置"*、*"比较这三篇论文描述的 loss function"*。AI 代理可以在几秒钟内读取、grep 和交叉引用数千页的全文内容。

4. **保持同步** — 运行同步守护进程（`sync_zotero_collections.py`）持续监控你的 Zotero 数据库。当你在 Zotero 中移动论文到不同 collection 时，Markdown 库自动更新——新增 symlink、移除过时的镜像、刷新 frontmatter 标签。

5. **同步到笔记库** — 因为输出是带结构化 frontmatter 的纯 Markdown，你可以直接把这个库丢进（或软链接到）Obsidian、Logseq 或任何基于 Markdown 的笔记系统。AI 生成的洞见和你自己的手写批注可以共存在同一个体系里。

### 推荐：用 zotero-attanger 实现多设备 PDF 访问

[**zotero-attanger**](https://github.com/HenryYu-1022/zotero-attanger) 把你的 Zotero 附件同步到 Google Drive，让你可以在任何设备上访问 PDF。它把文件从 Zotero 不透明的 `storage/` 目录（每个文件在一个随机 8 字符文件夹里，比如 `N7SMB24A/`）移到 Google Drive 上一个正常的、可浏览的目录中。

因为 `zotero-attanger` 为了节省空间只把每个 PDF 放在一个文件夹下，所以一篇属于多个 collection 的论文在磁盘上只会出现一次。**这完全没问题** — `paper-agent` 直接从 `zotero.sqlite` 读取完整的 collection 层级，并为每个 collection 创建 symlink 镜像。

> **提示：** 如果你的 PDF 已经在一个正常文件夹中（本地或云同步），不在 Zotero 的 `storage/` 里，那就不需要 `zotero-attanger`——直接把 `paper-agent` 指向你的 PDF 根目录即可。

---

`paper-agent` 使用 [Marker](https://github.com/datalab-to/marker) 把一个 PDF 目录树转换成 Markdown 资料库。配置了 Zotero 数据库路径后，它还会直接从 `zotero.sqlite` 读取 collection 层级，创建 symlink 镜像，让 Markdown 库与你的 Zotero 组织结构完全一致。


## 功能

- 用 Marker 把 PDF 转成 Markdown
- 持续监听输入目录，自动处理新增或修改的 PDF
- 按输入目录的相对层级组织输出目录
- 识别 `Paper_1.pdf` 这类 supporting PDF，并合并到主论文 bundle
- 用 manifest 跳过未变化文件
- 源 PDF 删除后自动清理对应 Markdown 产物
- 给生成的 Markdown 写入 YAML frontmatter
- 支持手动运行，也支持 Windows/macOS 后台常驻

## 输入模型

`paper-agent` 不关心 PDF 的来源。`input_root` 可以是：

- 普通本地目录
- Google Drive、iCloud Drive、Dropbox、OneDrive 等云盘挂载目录
- `zotero-attanger` 这类工具导出的按 collection 分层目录

你只需要保证要处理的 PDF 都在同一个根目录下面。

## 目录模型

你需要配置两个根目录：

- `input_root`：PDF 输入根目录
- `output_root`：Markdown、日志、manifest、Marker 临时产物输出根目录

建议两者分开，不要把 `output_root` 放到 `input_root` 里面。

## 运行前准备

> **⚠️ 硬件要求：** Marker 使用深度学习模型来转换 PDF。你的电脑至少需要 **8 GB 内存**。强烈建议有**独立显卡** — NVIDIA 显卡 + CUDA（Windows/Linux）或 Apple Silicon + MPS（Mac）。没有 GPU 也可以跑，但会非常慢（每篇 PDF 需要几分钟而不是几秒），只适合小规模文献库。

以下是你需要安装的内容，一步一步来：

### 1. 安装 Python

需要 Python 3.10 或更新版本。如果不确定是否已安装，打开终端输入：

```bash
python3 --version
```

如果版本低于 3.10，或者命令找不到，去 [python.org](https://www.python.org/downloads/) 下载安装。

### 2. 安装 PyTorch

PyTorch 是 Marker 运行所需的深度学习框架。去 [pytorch.org](https://pytorch.org/get-started/locally/) 按你的平台选择安装命令：

- **Mac（Apple Silicon）：** PyTorch 开箱即用，自动支持 MPS 加速
- **Windows/Linux + NVIDIA 显卡：** 选择与你驱动匹配的 CUDA 版本
- **没有 GPU：** 选 CPU 版——能用，只是慢很多

### 3. 安装 Marker

```bash
pip install marker-pdf
# 如果还要支持更多文档格式：
pip install marker-pdf[full]
```

### 4. 安装项目依赖

```bash
pip install -r requirements.txt
```

### 5. 首次下载 Marker 模型（只需要一次）

第一次运行 Marker 时会自动下载 AI 模型（约 1–2 GB）。跑一下这个命令把模型缓存下来：

```bash
marker_single /path/to/any-test.pdf --output_dir /tmp/test_out --force_ocr
```

测试输出可以删掉。模型已缓存，之后不需要重新下载。

## 快速开始

### 1. 创建配置

macOS / Linux：

```bash
cp paper_to_markdown/settings.example.json paper_to_markdown/settings.json
```

Windows PowerShell：

```powershell
Copy-Item paper_to_markdown\settings.example.json paper_to_markdown\settings.json
```

示例：

```jsonc
{
  "_comment_01": "macOS 只需要 python_path；pythonw_path 仅 Windows 使用。",
  "input_root": "/Users/yourname/Documents/paper-library/input",
  "output_root": "/Users/yourname/Documents/paper-library/output",
  "python_path": "/opt/homebrew/bin/python3",
  "pythonw_path": "",
  "model_cache_dir": "/Users/yourname/.cache/marker/datalab_model_cache",
  "marker_cli": "/absolute/path/to/marker_single",
  "hf_home": "/Users/yourname/.cache/huggingface",
  "torch_device": "mps"
}
```

说明：

- `marker_cli` 可以是绝对路径，也可以直接写 `marker_single`
- `python_path` 最好写绝对路径，后台常驻时尤其重要
- macOS 运行 LaunchAgent 时只读取 `python_path`，不读取 `pythonw_path`
- `pythonw_path` 只给 Windows 后台计划任务用；如果你在 macOS，完全可以留空或不写
- `torch_device` 一般是：NVIDIA 用 `cuda`，Apple Silicon 用 `mps`，无加速时用 `cpu`
- `paper_to_markdown/settings.json` 请保存为 UTF-8 编码，尤其是路径里有中文等非 ASCII 字符时

### 2. 先下载一次 Marker 模型

```bash
marker_single /path/to/test.pdf --output_dir /tmp/test_out --force_ocr
```

### 3. 手动批量转换

```bash
cd paper_to_markdown
python3 run_once.py
```

### 4. 启动监听器

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

## 先启动哪个文件

如果你只想先记住这一段，就按下面这个表来：

| 你的目标 | 先启动这个文件 | 在哪里启动 | 会不会常驻 | 真正常驻的是谁 |
|------|------|------|------|------|
| 先把当前库批量转一遍 | `paper_to_markdown/run_once.py` | `paper_to_markdown/` | 不会 | 没有，跑完就退出 |
| 持续监听 `input_root` 里的新 PDF | `paper_to_markdown/watch_folder_resilient.py` | `paper_to_markdown/` | 会 | `watch_folder_resilient.py` 自己 |
| 持续同步 Zotero collection 镜像 | `paper_to_markdown/sync_zotero_collections.py` | `paper_to_markdown/` | 可选，会 | `sync_zotero_collections.py` 自己 |
| macOS 开机登录后自动启动 watcher | `watch_autostart.sh` | 仓库根目录 | 不会 | `autostart/paper_agent_watch_supervisor.sh` + `watch_folder_resilient.py` |
| Windows 登录后自动启动 watcher | `watch_autostart.ps1` | 仓库根目录 | 不会 | `autostart/paper_agent_watch_supervisor.ps1` + `watch_folder_resilient.py` |

可以直接这么理解：

- `run_once.py` 是手动模式的主入口。
- `watch_folder_resilient.py` 是最核心的常驻监听进程。
- `watch_autostart.sh` 和 `watch_autostart.ps1` 是推荐的一键入口，用来安装、卸载和查看自动启动状态。
- `autostart/install_or_update_launch_agent.sh`、`autostart/remove_launch_agent.sh`、`autostart/install_or_update_watch_task.ps1`、`autostart/remove_watch_task.ps1` 是底层安装/卸载脚本。
- `autostart/paper_agent_watch_supervisor.sh` 和 `autostart/paper_agent_watch_supervisor.ps1` 才是登录后在后台保活 watcher 的 supervisor。
- `sync_zotero_collections.py` 是另一条独立的守护进程，目前不会被上面的自动启动脚本顺手安装。

## 本地目录如何运行

如果你的本地 PDF 目录是：

```text
/Users/you/Documents/PapersByTopic/
  AI/
    Paper1.pdf
    Paper1_1.pdf
  Chemistry/
    Paper2.pdf
```

那就设置：

- `input_root=/Users/you/Documents/PapersByTopic`
- `output_root=/Users/you/Documents/paper-agent-output`

然后执行：

```bash
cd paper_to_markdown
python3 run_once.py
```

如果你的 PDF 是由 `zotero-attanger` 整理导出的，本质上也是一样：把它导出的 PDF 根目录填到 `input_root` 即可。

## 使用方式

`paper_to_markdown/` 目录里的脚本要在该目录中执行。根目录下的工具脚本，例如 `backfill_supporting.py`、`monitor_conversion_progress.py`、`watch_autostart.ps1`、`watch_autostart.sh`，要在项目根目录执行。更底层的自动启动辅助脚本现在都放在 `autostart/` 目录下。

### 手动转换

```bash
cd paper_to_markdown

# 转换 input_root 下全部 PDF
python3 run_once.py

# 只转换一个 PDF
python3 run_once.py --path "/path/to/input_root/subdir/Paper.pdf"

# 强制全部重转
python3 run_once.py --force

# 只测试前 5 个
python3 run_once.py --limit 5

# 清理源 PDF 已不存在的产物
python3 run_once.py --cleanup

# 指定自定义配置文件
python3 run_once.py --config /path/to/settings.json
```

### 监听模式

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

watcher 会递归监控 `input_root`，并处理：

- 新建 PDF：经过 debounce 和稳定性检查后进入队列
- 修改 PDF：重新入队
- 移动或重命名 PDF：对新路径重新排队
- 删除 PDF：删除对应 Markdown bundle、原始输出和 manifest 记录

### 补齐 supporting PDFs

```bash
python3 backfill_supporting.py
python3 backfill_supporting.py --apply
python3 backfill_supporting.py --limit 10
```

### 监控转换进度

```bash
python3 monitor_conversion_progress.py
python3 monitor_conversion_progress.py --watch --interval 30
```

## 后台常驻

### macOS LaunchAgent

```bash
zsh ./watch_autostart.sh install
zsh ./watch_autostart.sh status
zsh ./watch_autostart.sh remove
```

macOS 这里用的是当前用户的 LaunchAgent，一般不要加 `sudo`，直接运行即可：

```bash
zsh ./watch_autostart.sh install
```

- `watch_autostart.sh`：统一的安装 / 卸载 / 状态查看入口
- 它底层会调用 `autostart/install_or_update_launch_agent.sh` 和 `autostart/remove_launch_agent.sh`
- supervisor：`autostart/paper_agent_watch_supervisor.sh`
- 安装后真正常驻后台的进程：`autostart/paper_agent_watch_supervisor.sh` 和它拉起的 `paper_to_markdown/watch_folder_resilient.py`
- 默认 label：`com.paper.agent.watch`
- plist 路径：`~/Library/LaunchAgents/com.paper.agent.watch.plist`

### Windows 计划任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\YHY\Paper-agent\watch_autostart.ps1" # 如果你的本机路径不同，把这里换成你自己的文件路径
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\YHY\Paper-agent\watch_autostart.ps1" -Action status # 如果你的本机路径不同，把这里换成你自己的文件路径
powershell -NoProfile -ExecutionPolicy Bypass -File "E:\YHY\Paper-agent\watch_autostart.ps1" -Action remove # 如果你的本机路径不同，把这里换成你自己的文件路径
```

如果你是从普通 PowerShell 里想直接提权成管理员再运行安装命令，可以用这一行：

```powershell
Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "E:\YHY\Paper-agent\watch_autostart.ps1"' # 如果你的本机路径不同，把这里换成你自己的文件路径
```

- `watch_autostart.ps1`：统一的安装 / 卸载 / 状态查看入口
- 它底层会调用 `autostart/install_or_update_watch_task.ps1` 和 `autostart/remove_watch_task.ps1`
- supervisor：`autostart/paper_agent_watch_supervisor.ps1`
- 安装后真正常驻后台的进程：`autostart/paper_agent_watch_supervisor.ps1` 和它拉起的 `paper_to_markdown/watch_folder_resilient.py`
- 默认任务名：`PaperAgentWatch`

注意：这里的后台自启动目前只覆盖 PDF watcher，不会自动帮你把 `paper_to_markdown/sync_zotero_collections.py` 也装成后台服务。如果你还想持续同步 Zotero collection，需要单独启动它。

## 输出结构

输入目录示例：

```text
input_root/
  AI/
    Paper1.pdf
    Paper1_1.pdf
  Chemistry/
    Paper2.pdf
```

输出目录大致如下：

```text
output_root/
  markdown/
    AI/
      Paper1/
        Paper1.md
        supporting.md
        supporting_assets/
    Chemistry/
      Paper2/
        Paper2.md
  state/
    manifest.json
  marker_raw/
  logs/
    app.log
    failed_pdfs.txt
```

## Frontmatter

每个 Markdown 文件都会写入类似下面的 YAML frontmatter：

```yaml
---
source_pdf: /path/to/input_root/AI/Paper1.pdf
source_relpath: AI/Paper1.pdf
source_filename: Paper1.pdf
converter: marker_single
converted_at: '2026-04-09T10:30:45.123456+00:00'
torch_device: mps
force_ocr: true
document_role: main
---
```

supporting PDF 还会写入 `supporting_index` 以及主论文相关字段。

## Zotero Collection 层级镜像

如果你在 `settings.json` 中配置了 `zotero_db_path`，`paper-agent` 会只读访问你的 Zotero 数据库，找出每篇论文属于哪些 collection。然后：

1. **给每个 Markdown 文件打标签** —— 在 YAML frontmatter 中写入 `zotero_collections` 列表
2. **创建 symlink 镜像** —— 让论文出现在所有对应的 collection 文件夹中，即使 PDF 只存在于一个物理位置

例如，一篇论文在 Zotero 中同时属于 `Research/NLP` 和 `Coursework/CS229`，但磁盘上只存在于 `Research/NLP/`：

```text
output_root/
  markdown/
    Research/NLP/Paper/          ← 真实目录
      Paper.md
    Coursework/CS229/Paper/      ← symlink → Research/NLP/Paper/
```

YAML frontmatter 会包含：

```yaml
zotero_collections:
  - Coursework/CS229
  - Research/NLP
```

### 同步守护进程

当你在 Zotero 里改变了文献的 collection 归属，运行同步守护进程来更新 Markdown 库：

```bash
# 单次同步
cd paper_to_markdown
python3 sync_zotero_collections.py --once

# 持续守护进程（默认每 60 秒轮询一次）
python3 sync_zotero_collections.py

# 自定义间隔
python3 sync_zotero_collections.py --interval 30
```

守护进程会检测 collection 分配的变化，并：
- 论文加入新 collection 时创建新的 symlink 镜像
- 论文从 collection 移除时删除对应的 symlink 镜像
- 更新 YAML frontmatter 中的 `zotero_collections` 字段

Zotero 可以保持运行，数据库以只读 immutable 模式打开，不会冲突。

## Supporting PDF 识别规则

一个 PDF 会被识别为 supporting material，当且仅当：

1. 转换后的 markdown 开头在去掉空白和标点后能命中 `supportinginformation`
2. 同目录下能找到名称匹配的主 PDF

像 `_1`、`_2` 这样的数字后缀仍然会用于排序和命名，但不再是识别 supporting 的前提条件。

否则它会被当作独立论文处理。

## 失败自动重试

Marker 有时会因为 GPU 显存压力、中间状态异常等瞬态原因崩溃或失败。为此，`paper-agent` 会对失败的转换自动重试最多 3 次：

- **批量模式（`run_once.py`）**：第一遍跑完后，所有失败的 PDF 会被自动重试最多 3 次。最终的 `failed_pdfs.txt` 报告只包含重试 3 次仍然失败的 PDF。
- **单文件模式（`run_once.py --path`）**：同样应用 3 次重试逻辑。
- **监听模式（`watch_folder_resilient.py`）**：每个文件系统事件触发的 PDF 转换也会在失败时重试最多 3 次，才会被标记为失败。

重试次数由 `pipeline.py` 中的 `MAX_CONVERSION_RETRIES` 控制（默认值：3）。

## 配置项说明

配置文件路径：`paper_to_markdown/settings.json`

| 键名 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `input_root` | 是 | -- | 源 PDF 根目录 |
| `output_root` | 是 | -- | Markdown、state、logs、raw 的输出根目录 |
| `marker_cli` | 是 | -- | Marker 命令名或绝对路径，例如 `marker_single` |
| `hf_home` | 是 | -- | Hugging Face 缓存目录 |
| `python_path` | 否 | -- | macOS LaunchAgent 实际使用的 Python 路径，也可作为 Windows 回退值；建议写绝对路径 |
| `pythonw_path` | 否 | -- | 仅 Windows 后台任务优先使用的 `pythonw.exe` 路径；macOS 不需要填，也不会读取这个字段 |
| `marker_repo_root` | 否 | -- | 可选的 Marker 工作目录，仅特殊本地源码场景需要 |
| `model_cache_dir` | 否 | -- | supervisor 会导出为 `MODEL_CACHE_DIR` |
| `torch_device` | 否 | `cuda` | `cuda`、`mps` 或 `cpu` |
| `output_format` | 否 | `markdown` | Marker 输出格式 |
| `force_ocr` | 否 | `false` | 是否强制 OCR |
| `disable_image_extraction` | 否 | `false` | 是否禁用图片提取 |
| `disable_multiprocessing` | 否 | `false` | 是否禁用 Marker 多进程 |
| `paginate_output` | 否 | `false` | 是否在 Markdown 中加入分页标记 |
| `compute_sha256` | 否 | `false` | 是否额外计算 SHA256 做变化检测 |
| `log_level` | 否 | `INFO` | 日志级别 |
| `watch_debounce_seconds` | 否 | `8` | 文件变化后等待多久再处理 |
| `watch_stable_checks` | 否 | `3` | 稳定性检查次数 |
| `watch_stable_interval_seconds` | 否 | `2` | 稳定性检查间隔 |
| `watch_rescan_interval_seconds` | 否 | `60` | 定期全量重扫间隔，`0` 表示关闭 |
| `watch_initial_scan` | 否 | `true` | watcher 启动时是否先把现有未处理 PDF 入队 |
| `zotero_db_path` | 否 | -- | `zotero.sqlite` 路径；配置后启用 collection 层级镜像 |
| `collection_mirror_mode` | 否 | `symlink` | `symlink`（节约空间）或 `copy`（Windows 无管理员权限时用） |
| `zotero_sync_interval_seconds` | 否 | `60` | collection 同步守护进程的轮询间隔 |

## 逐文件说明

### 运行时核心文件

| 文件 | 用途 | 如何启动 / 使用 | 是否后台常驻 |
|------|------|------|------|
| `paper_to_markdown/run_once.py` | 手动批量转换入口，支持单文件转换、强制重转、清理孤儿产物 | `cd paper_to_markdown && python3 run_once.py` | 否 |
| `paper_to_markdown/watch_folder_resilient.py` | 监听 `input_root` 的主 watcher，处理新增、修改、移动、删除的 PDF | `cd paper_to_markdown && python3 watch_folder_resilient.py` | 是，前提是你一直让它运行 |
| `paper_to_markdown/sync_zotero_collections.py` | 把 Zotero collection 归属同步到 frontmatter 和镜像目录 | `cd paper_to_markdown && python3 sync_zotero_collections.py --once`，或不加 `--once` 持续守护 | 可选；守护模式下是 |
| `paper_to_markdown/pipeline.py` | 核心转换流水线：调用 Marker、写 Markdown/frontmatter、管理 manifest、重试和清理 | 被其他脚本导入，不直接启动 | 否 |
| `paper_to_markdown/common.py` | 公共工具函数：配置读取、路径计算、日志、manifest/state 位置、frontmatter 更新 | 被其他脚本导入，不直接启动 | 否 |
| `paper_to_markdown/zotero_collections.py` | 只读访问 `zotero.sqlite`，解析 collection 层级 | 被其他脚本导入，不直接启动 | 否 |
| `paper_to_markdown/__init__.py` | 把 `paper_to_markdown` 标记成 Python 包 | 不需要手动操作 | 否 |
| `paper_to_markdown/settings.example.json` | 配置模板文件 | 复制成 `paper_to_markdown/settings.json` 后修改路径 | 否 |
| `paper_to_markdown/settings.json` | 你本机实际运行时读取的配置 | 由你创建，所有可执行脚本都会读取它 | 否 |

### 工具和修复文件

| 文件 | 用途 | 如何启动 / 使用 | 是否后台常驻 |
|------|------|------|------|
| `backfill_supporting.py` | 检查 supporting PDF 是否缺失对应 Markdown，并按需补齐 | 在仓库根目录运行 `python3 backfill_supporting.py` 或 `python3 backfill_supporting.py --apply` | 否 |
| `monitor_conversion_progress.py` | 只读查看转换进度、manifest 状态和 ETA | 在仓库根目录运行 `python3 monitor_conversion_progress.py` 或 `python3 monitor_conversion_progress.py --watch --interval 30` | 否；`--watch` 只是终端持续刷新 |

### 后台自启动相关文件

| 文件 | 用途 | 如何启动 / 使用 | 是否后台常驻 |
|------|------|------|------|
| `watch_autostart.sh` | macOS 自动启动的统一入口，可安装、卸载或查看 LaunchAgent 状态 | 在仓库根目录运行 `zsh ./watch_autostart.sh install`、`status` 或 `remove` | 否 |
| `autostart/install_or_update_launch_agent.sh` | 安装或更新 macOS LaunchAgent，让 watcher 登录后自动启动 | 一般由 `zsh ./watch_autostart.sh install` 间接调用 | 否，它只是安装器 |
| `autostart/remove_launch_agent.sh` | 删除 macOS LaunchAgent，并停止相关 watcher 进程 | 一般由 `zsh ./watch_autostart.sh remove` 间接调用 | 否 |
| `autostart/paper_agent_watch_supervisor.sh` | macOS supervisor，负责在 watcher 退出后重新拉起 | 一般由 LaunchAgent 启动，不建议手动长期直接跑 | 是，安装后会常驻 |
| `watch_autostart.ps1` | Windows 自动启动的统一入口，可安装、卸载或查看计划任务状态 | 在仓库根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\watch_autostart.ps1`，也可加 `-Action status` 或 `-Action remove` | 否 |
| `autostart/install_or_update_watch_task.ps1` | 安装或更新 Windows 计划任务，让 watcher 登录后自动启动 | 一般由 `watch_autostart.ps1` 间接调用 | 否，它只是安装器 |
| `autostart/remove_watch_task.ps1` | 删除 Windows 计划任务 | 一般由 `watch_autostart.ps1 -Action remove` 间接调用 | 否 |
| `autostart/paper_agent_watch_supervisor.ps1` | Windows supervisor，负责在 watcher 退出后重新拉起 | 一般由计划任务启动，不建议手动长期直接跑 | 是，安装后会常驻 |

### 文档和元数据文件

| 文件 | 用途 | 如何使用 | 是否后台常驻 |
|------|------|------|------|
| `README.markdown` | 英文文档 | 阅读 | 否 |
| `README.zh-CN.markdown` | 中文文档 | 阅读 | 否 |
| `requirements.txt` | Python 依赖列表 | `pip install -r requirements.txt` | 否 |
| `docs/superpowers/specs/2026-04-09-mac-launchagent-design.md` | macOS LaunchAgent 设计说明 | 只做参考 | 否 |
| `docs/superpowers/plans/2026-04-09-mac-launchagent.md` | macOS 后台启动的实现计划记录 | 只做参考 | 否 |
| `LICENSE` | 开源许可证 | 只做参考 | 否 |

## 说明

- `settings.json` 是机器相关配置，已经加入 `.gitignore`
- 模板 `settings.example.json` 开头的 `_comment_*` 字段是给人看的说明，保留也不会影响程序运行
- `paper-agent` 不依赖 Google Drive，本地目录可直接使用
- 如果你使用 `zotero-attanger`，把它导出的 PDF 根目录填到 `input_root` 即可
- macOS LaunchAgent 不继承你交互 shell 的 PATH，而且脚本只读取 `python_path`，所以这个字段最好写绝对路径
- Windows 后台任务会优先读 `pythonw_path`；如果没填，再回退到 `python_path`
- macOS 侧通过 `plutil` 读取 JSON，Windows 侧现在显式按 UTF-8 读取 `settings.json`；如果路径里有中文，请确保配置文件保存为 UTF-8

## 致谢

本项目在 AI 编程代理的深度参与下完成——而这恰恰也是它所实现的工作流的一部分：

<table>
  <tr>
    <td align="center"><a href="https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview"><img src="https://img.shields.io/badge/Claude_Code-Anthropic-CC785C?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a></td>
    <td>架构设计、代码实现、文档撰写和迭代调试均与 <strong>Claude Code</strong> 结对完成。</td>
  </tr>
  <tr>
    <td align="center"><a href="https://openai.com/index/codex/"><img src="https://img.shields.io/badge/Codex-OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI Codex"></a></td>
    <td><strong>OpenAI Codex</strong> 用于代码审查、重构建议和实现模式的交叉验证。</td>
  </tr>
</table>

> *"我们做了一个让 AI 代理读论文的工具——然后用 AI 代理把它做出来了。"*

## 许可证

MIT。详见 `LICENSE`。
