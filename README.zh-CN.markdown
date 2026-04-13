# Paper-agent

English version: [README.markdown](README.markdown)

---

## 项目概览

`paper-agent` 的目标，是把一整个 PDF 文献库转换成 AI 代理更容易读取、搜索和交叉引用的 Markdown 文献库。

它最初面向 Zotero 工作流设计，但并不要求你必须使用 Zotero。只要你有一个 PDF 文件夹树，它就可以工作。

这个项目有两类输入：

- 磁盘上的 PDF 目录树，由 `input_root` 指定
- 可选的 Zotero collection 层级，由 `zotero.sqlite` 提供

真实的 Markdown 主目录永远跟随 `input_root` 的相对路径。Zotero collection 只负责补充 frontmatter 和额外镜像目录，不决定主 bundle 的物理落点。

## 相关项目：MarkItDown

[MarkItDown](https://github.com/microsoft/markitdown) 是微软的另一个项目，重点是把多种文件格式转换成 Markdown，而不只是 PDF。

但基于当前这份 `paper-agent` 代码，必须明确几点：

- `paper-agent` 现在并不会直接调用 MarkItDown
- `paper-agent` 当前真正使用的转换器仍然是 Marker
- manifest 逻辑、raw 输出目录、supporting 放置规则、重复 bundle 规整和 watcher 流程，都是按当前 Marker 流水线设计的

所以，按“现有代码已经提供的功能”来看，两者目前最合理的配合方式是手动互补，而不是原生集成：

- `paper-agent` 负责 PDF 文献库这条主流程：批量转换、watcher、manifest 管理、supporting 归位、重复 Markdown 规整，以及 Zotero collection 元数据或镜像
- MarkItDown 适合你在这条主流程之外，单独处理其他类型文档的临时 Markdown 转换

如果只看当前代码下的工作量：

- 低工作量：在文档里说明 MarkItDown 是可搭配使用的相关项目，并手动配合使用
- 更高工作量：把 Marker 替换掉，或者在 `paper-agent` 里真正新增一条 MarkItDown 转换路径；这在当前仓库里都还没有实现，所以 README 也不会把它写成现成功能

## 项目功能

- 用 [Marker](https://github.com/datalab-to/marker) 把 PDF 转成 Markdown
- 给每个生成的 Markdown 写入 YAML frontmatter
- 让输出目录结构与 `input_root` 的相对层级保持一致
- 把正文 Markdown 和 supporting Markdown 放进同一个论文文件夹
- 把 supporting 文件统一命名为 `supporting.md`、`supporting_2.md`、`supporting_assets/` 等
- 按文件名规则识别 supporting PDF，例如 `_1`、`_2`、`si`、`supporting`、`supplementary information` 等
- 识别 `Paper.pdf`、`Paper 2.pdf`、`Paper 3.pdf` 这类重复正文导出
- 对正文和 supporting 两类 Markdown 都做近似重复去重，只保留一个 canonical 论文 bundle
- 用 `state/manifest.json` 记录转换结果
- 常规运行时自动跳过未变化的 PDF
- 源 PDF 删除后自动清理对应 Markdown 产物
- 转换失败时自动重试
- 如果配置了 `zotero.sqlite`，会把 `zotero_collections` 写入 frontmatter，并按 `symlink` 或 `copy` 创建额外 collection 镜像
- 提供一个一次性修复脚本，用于规整已经生成好的旧 Markdown 库

## 输出模型

如果你的输入树长这样：

```text
input_root/
  Topic A/
    Paper.pdf
    Paper 3.pdf
    Paper Supporting Information.pdf
  Topic B/
    Another Paper.pdf
```

规整后的 Markdown 输出会是：

```text
output_root/
  markdown/
    Topic A/
      Paper/
        Paper.md
        supporting.md
        supporting_assets/
    Topic B/
      Another Paper/
        Another Paper.md
  marker_raw/
  state/
    manifest.json
    collection_state.json
  logs/
    app.log
    failed_pdfs.txt
```

这里有几个关键点：

- `Paper 3.pdf` 如果正文和 `Paper.pdf` 近似重复，就不会再单独保留一个 Markdown 文件夹
- `Paper Supporting Information.pdf` 会被并入 `Topic A/Paper/`，而不是落成一个独立 sibling 文件夹
- 目录组织单位是“每篇论文一个 folder”

如果你启用了 Zotero collection 镜像，`output_root/markdown/` 下还可能出现同一篇论文的额外 collection 路径镜像：

- `symlink` 模式更省空间
- `copy` 模式会创建普通复制目录

## 从哪里开始

| 目标 | 启动文件 | 在哪里运行 | 是否常驻 |
|-----|-----|-----|-----|
| 全库批量转换一次 | `paper_to_markdown/run_once.py` | `paper_to_markdown/` | 否 |
| 持续监听新增或修改的 PDF | `paper_to_markdown/watch_folder_resilient.py` | `paper_to_markdown/` | 是 |
| 把 Zotero collection 同步进 frontmatter 和镜像目录 | `paper_to_markdown/sync_zotero_collections.py` | `paper_to_markdown/` | 可选 |
| 就地修复已有 Markdown 库 | `normalize_existing_markdown_library.py` | 仓库根目录 | 否 |
| 补齐缺失的 supporting Markdown | `backfill_supporting.py` | 仓库根目录 | 否 |
| 查看批量转换进度和 ETA | `monitor_conversion_progress.py` | 仓库根目录 | 否 |

## Python 文件说明

### 仓库根目录下的 Python 文件

| 文件 | 作用 | 典型用法 |
|-----|-----|-----|
| `normalize_existing_markdown_library.py` | 一次性迁移和修复脚本。用于规整已经生成好的 Markdown 库：把旧的独立 supporting bundle 挪回正文目录、合并重复正文 bundle、去重 supporting Markdown、刷新 manifest。 | 当你升级了 pipeline 逻辑，但不想重新转换 PDF 时，用它就地规整旧库。 |
| `backfill_supporting.py` | 扫描 PDF 库中缺失 `supporting*.md` 的 supporting PDF，并按需只回填这些 supporting 文件。 | 修复 supporting 缺失场景。 |
| `monitor_conversion_progress.py` | 读取 `logs/app.log` 和 `state/manifest.json`，汇总当前批次进度、当前 PDF、历史平均耗时和 ETA。 | 长时间全量转换时看进度。 |

### `paper_to_markdown/` 目录下的 Python 文件

| 文件 | 作用 | 典型用法 |
|-----|-----|-----|
| `paper_to_markdown/__init__.py` | 把 `paper_to_markdown` 标记成 Python 包。 | 不需要手动运行。 |
| `paper_to_markdown/common.py` | 公共工具模块。负责配置读取、路径计算、frontmatter 工具、supporting 和正文重复分组规则、bundle 命名以及通用文件系统辅助函数。 | 被其他可执行脚本导入。 |
| `paper_to_markdown/pipeline.py` | 核心转换引擎。负责调用 Marker、生成 Markdown bundle、放置 supporting、做近似重复去重、维护 manifest、清理产物、暴露主转换函数。 | 核心内部模块，不直接当入口运行。 |
| `paper_to_markdown/run_once.py` | 主手动 CLI 入口。支持全库批量转换、单文件转换、强制重转和孤儿产物清理。 | 日常手动批量跑库时用它。 |
| `paper_to_markdown/watch_folder_resilient.py` | `input_root` 的文件系统 watcher。负责 debounce、稳定性检测、转换新增或修改的 PDF，并在 PDF 删除时清理对应产物。 | 需要持续后台监听时用它。 |
| `paper_to_markdown/sync_zotero_collections.py` | 读取 Zotero collection 变化，并把变化同步到 frontmatter 和 collection 镜像目录；它依赖 manifest 来确定已经转换过哪些论文。 | Zotero 里移动 collection 后，同步 Markdown 库时用它。 |
| `paper_to_markdown/zotero_collections.py` | 只读访问 Zotero SQLite，解析 collection 路径，并建立 PDF 文件名到 collection 列表的映射。 | 给 pipeline 和 collection sync 提供数据。 |

## 非 Python 的启动辅助文件

虽然这些不是 `.py` 文件，但它们也是运行链条的一部分：

| 文件 | 作用 |
|-----|-----|
| `watch_autostart.sh` | macOS watcher 自动启动的一键入口，负责 install/status/remove |
| `watch_autostart.ps1` | Windows watcher 自动启动的一键入口，负责 install/status/remove |
| `autostart/paper_agent_watch_supervisor.sh` | macOS 上负责保活 watcher 的 supervisor |
| `autostart/paper_agent_watch_supervisor.ps1` | Windows 上负责保活 watcher 的 supervisor |
| `autostart/install_or_update_launch_agent.sh` | 安装或更新 macOS LaunchAgent |
| `autostart/remove_launch_agent.sh` | 卸载 macOS LaunchAgent |
| `autostart/install_or_update_watch_task.ps1` | 安装或更新 Windows Scheduled Task |
| `autostart/remove_watch_task.ps1` | 卸载 Windows Scheduled Task |

## 当前 pipeline 遵循的核心规则

### 1. 真实目录规则

真实 bundle 的物理位置由 PDF 在 `input_root` 下的相对路径决定。

如果 PDF 在：

```text
input_root/My Library/Thesis/Paper.pdf
```

那么 canonical Markdown bundle 在：

```text
output_root/markdown/My Library/Thesis/Paper/
```

### 2. Supporting 放置规则

supporting PDF 不再独立占一个论文文件夹。

一旦被识别成 supporting，它就会写进主论文 bundle 中，形式是：

- `supporting.md`
- `supporting_2.md`
- `supporting_assets/`
- `supporting_2_assets/`

### 3. 重复正文规则

如果同目录里有：

- `Paper.pdf`
- `Paper 2.pdf`
- `Paper 3.pdf`

pipeline 会先把它们作为候选重复正文分组；如果转换出来的 Markdown 正文近似重复，就只保留一个 canonical 主 bundle，并把其他 PDF 在 manifest 里重定向到同一个 canonical Markdown。

### 4. 旧库修复规则

`normalize_existing_markdown_library.py` 会复用同一套 supporting 和重复正文判定逻辑，对已经生成好的旧 Markdown 库做一次性就地修复。它是给“旧输出库”准备的，不是日常每次都要跑的主入口。

## 常用命令

### 全库批量转换

```bash
cd paper_to_markdown

# 转换 input_root 下全部 PDF
python3 run_once.py

# 只转换一个 PDF
python3 run_once.py --path "/path/to/input_root/subdir/Paper.pdf"

# 强制重转
python3 run_once.py --force

# 先只测试前 5 个
python3 run_once.py --limit 5

# 清理源 PDF 已经不存在的产物
python3 run_once.py --cleanup
```

### 持续监听

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

### 同步 Zotero collections

```bash
cd paper_to_markdown

# 单次同步
python3 sync_zotero_collections.py --once

# 守护模式
python3 sync_zotero_collections.py

# 自定义间隔
python3 sync_zotero_collections.py --interval 30
```

### 回填缺失的 supporting

```bash
python3 backfill_supporting.py
python3 backfill_supporting.py --apply
python3 backfill_supporting.py --limit 10
```

### 规整已有 Markdown 库

```bash
python3 normalize_existing_markdown_library.py
python3 normalize_existing_markdown_library.py --config paper_to_markdown/settings.json
python3 normalize_existing_markdown_library.py --limit 20
```

### 查看转换进度

```bash
python3 monitor_conversion_progress.py
python3 monitor_conversion_progress.py --watch --interval 30
```

## 配置项说明

运行时配置文件：`paper_to_markdown/settings.json`

| 配置项 | 说明 |
|-----|-----|
| `input_root` | PDF 根目录 |
| `output_root` | Markdown、state、logs、Marker 原始输出的根目录 |
| `marker_cli` | Marker 可执行文件，通常是 `marker_single` 或绝对路径 |
| `hf_home` | Hugging Face 缓存目录 |
| `python_path` | macOS LaunchAgent 使用的 Python 路径，也可作为显式解释器路径 |
| `pythonw_path` | Windows 后台优先使用的解释器 |
| `marker_repo_root` | Marker 的可选工作目录 |
| `model_cache_dir` | supervisor 可选导出的模型缓存环境变量 |
| `torch_device` | 通常是 `cuda`、`mps` 或 `cpu` |
| `force_ocr` | 是否强制 OCR |
| `disable_image_extraction` | 是否禁用图片提取 |
| `disable_multiprocessing` | 是否禁用 Marker 多进程 |
| `paginate_output` | 是否在 Markdown 中加入分页标记 |
| `compute_sha256` | 是否把 SHA256 纳入变更检测 |
| `log_level` | 日志级别 |
| `watch_debounce_seconds` | watcher 对变更事件的 debounce 延迟 |
| `watch_stable_checks` | 开始转换前的稳定性检查次数 |
| `watch_stable_interval_seconds` | 稳定性检查间隔 |
| `watch_rescan_interval_seconds` | 周期性全量重扫间隔；`0` 表示关闭 |
| `watch_initial_scan` | watcher 启动时是否把已有 PDF 入队 |
| `zotero_db_path` | `zotero.sqlite` 路径 |
| `collection_mirror_mode` | 额外 collection 镜像目录的模式：`symlink` 或 `copy` |
| `zotero_sync_interval_seconds` | collection 同步守护进程的轮询间隔 |

## Zotero Collection 行为

如果配置了 `zotero_db_path`：

- pipeline 会在 frontmatter 中写入 `zotero_collections`
- 真实的主输出目录仍然跟随 `input_root`
- Zotero 的其他 collection 位置会作为 canonical bundle 的镜像目录出现
- 镜像形式由 `collection_mirror_mode` 决定，可以是 `symlink` 或 `copy`

这个区别很重要：

- `input_root` 决定真实主 bundle 放哪
- Zotero collection 只负责补充元数据和额外镜像

## 说明和注意事项

- `output_root` 不要放在 `input_root` 里面
- 跑 Marker 强烈建议有 GPU
- 如果启用了 collection 同步，PDF 到 collection 的映射是按文件名做的，因此“文件名唯一”最稳妥
- `copy` 模式更适合 Windows 普通目录使用，但会占更多空间
- `symlink` 模式更省空间，但有些平台或权限设置下可能需要额外权限

## 许可证

MIT。详见 `LICENSE`。
