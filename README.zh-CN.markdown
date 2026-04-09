# Zotero Google Drive PDF 转 Markdown

English version: [README.markdown](README.markdown)

使用 [Marker](https://github.com/VikParuchuri/marker) 自动把存放在 Google Drive 中的 Zotero 论文 PDF 转成 Markdown。这个项目面向“云盘原生”工作流设计，源 PDF 和生成后的 Markdown 都保留在 Google Drive 中，便于配合 Obsidian 或其他基于 Markdown 的工具使用。

## 功能特性

- **自动转换 PDF**：使用 Marker 进行高质量 PDF 转 Markdown，并支持 OCR
- **实时监听**：持续监控 PDF 文件夹，自动处理新增或修改的文件
- **删除同步**：源 PDF 删除后，自动清理对应 Markdown 产物
- **支持材料合并**：识别补充材料 PDF，例如 `Paper_1.pdf`，并合并到主论文的 Markdown bundle 中
- **基于 manifest 的状态跟踪**：跳过未变化文件，记录失败信息，避免重复转换
- **孤儿文件清理**：批量扫描并清理那些源 PDF 已不存在的 Markdown 输出
- **自动清理原始中间产物**：运行结束时自动删除 `marker_raw/`
- **YAML frontmatter**：每个 Markdown 文件都会写入来源路径、转换时间、文档角色等元数据
- **Windows 计划任务支持**：通过 PowerShell supervisor 在登录后自动启动 watcher
- **云盘原生**：项目目录只保存脚本和配置，实际数据留在 Google Drive

## 架构

```text
Google Drive                              本地电脑
+-----------------------------+           +----------------------------------+
| Zotero_Papers/              |           | 当前项目                         |
|   Paper1.pdf           <----+--- 读取   |   pdf_to_markdown/               |
|   Paper1_1.pdf              |           |     run_once.py                  |
|   subfolder/Paper2.pdf      |           |     watch_folder_resilient.py    |
|                             |           |     pipeline.py                  |
| zoterotomarkdown/           |           |     common.py                    |
|   markdown/            <----+--- 写入   |     settings.json                |
|   state/manifest.json       |           |                                  |
|   marker_raw/               |           | Marker（单独安装）               |
|   logs/                     |           |   marker_single.exe              |
+-----------------------------+           +----------------------------------+
```

## 运行前准备

1. **Python 3.10+**
2. **PyTorch**：请按你的平台从 [pytorch.org](https://pytorch.org) 安装
3. **Marker**：PDF 转换引擎

```powershell
pip install marker-pdf
# 或安装完整依赖
pip install marker-pdf[full]
```

4. **项目依赖**

```powershell
pip install -r requirements.txt
```

> `requirements.txt` 里当前包含 `watchdog`（目录监听）和 `PyYAML`（frontmatter 生成）。

5. **Google Drive for Desktop**：源 PDF 和输出 Markdown 都通过本地挂载的 Google Drive 路径访问

## 快速开始

### 1. 配置

复制配置模板并修改：

```powershell
Copy-Item pdf_to_markdown\settings.example.json pdf_to_markdown\settings.json
```

编辑 `settings.json`：

```jsonc
{
  "source_dir": "G:\\YourDrive\\Zotero_Papers",      // Google Drive 上的 PDF 源目录
  "work_root": "G:\\YourDrive\\zoterotomarkdown",    // Google Drive 上的输出根目录
  "marker_cli": "C:\\path\\to\\marker_single.exe",   // marker 可执行文件路径
  "marker_repo_root": "D:\\marker",                  // marker 仓库根目录
  "hf_home": "D:\\marker\\hf_cache",                 // Hugging Face 模型缓存目录
  "pythonw_path": "C:\\path\\to\\pythonw.exe"        // 仅在使用 Windows 计划任务时需要
}
```

如果只是手动运行脚本，可以不写 `pythonw_path`。如果你要启用 Windows 自动启动 watcher，这个字段必须指向有效的 `pythonw.exe`。

### 2. 下载 Marker 模型

先跑一次测试转换，触发模型下载：

```powershell
marker_single C:\path\to\any_test.pdf --output_dir C:\temp\test_out --force_ocr
```

### 3. 批量转换全部 PDF

```powershell
cd pdf_to_markdown
python run_once.py
```

转换期间会创建 `marker_raw/`，运行结束后会自动清理。

### 4. 启动监听器

```powershell
cd pdf_to_markdown
python watch_folder_resilient.py
```

watcher 会持续运行，自动处理新增 PDF，并在源 PDF 被删除时清理对应产物。watcher 退出时也会自动删除 `marker_raw/`。

## 使用说明

位于 `pdf_to_markdown/` 目录内的脚本，应在该目录中执行。位于项目根目录的工具脚本，例如 `backfill_supporting.py`、`monitor_conversion_progress.py`、`install_or_update_task.ps1`、`remove_task.ps1`，应在项目根目录执行。

### 手动转换（`run_once.py`）

```powershell
cd pdf_to_markdown

# 转换所有尚未处理的 PDF
python run_once.py

# 只转换单个 PDF
python run_once.py --path "G:\YourDrive\Zotero_Papers\Paper.pdf"

# 强制全部重转（忽略 manifest）
python run_once.py --force

# 只跑前 5 个，用于测试
python run_once.py --limit 5

# 清理孤儿 Markdown（源 PDF 已删除）
python run_once.py --cleanup

# 使用自定义配置文件
python run_once.py --config /path/to/settings.json
```

### 监听模式（`watch_folder_resilient.py`）

```powershell
cd pdf_to_markdown
python watch_folder_resilient.py
```

watcher 会递归监控 `source_dir`，并处理以下事件：

| 事件 | 行为 |
|------|------|
| PDF 新建 | 经过 debounce 和稳定性检查后加入转换队列 |
| PDF 修改 | 重新加入转换队列 |
| PDF 移动或重命名 | 对新路径重新排队转换 |
| PDF 删除 | 删除对应 Markdown bundle、原始输出和 manifest 记录 |

使用 `Ctrl+C` 停止。

### 补齐历史支持材料 PDF

用于处理那些以前漏掉的 supporting PDFs：

```powershell
# Dry run，只看哪些文件会被转换
python backfill_supporting.py

# 真正补转缺失的 supporting PDFs
python backfill_supporting.py --apply

# 限制扫描数量
python backfill_supporting.py --limit 10
```

和其他入口脚本一样，`backfill_supporting.py` 结束时也会自动清理 `marker_raw/`。

### 监控转换进度

使用独立的监控脚本查看批处理进度和预计剩余时间：

```powershell
# 查看一次快照
python monitor_conversion_progress.py

# 每 30 秒刷新一次
python monitor_conversion_progress.py --watch --interval 30
```

该脚本会读取 `state/manifest.json` 和 `logs/app.log`，输出总 PDF 数、已完成数量、当前处理文件、剩余数量、平均转换时间和 ETA，不会修改转换流程。

### Windows 计划任务

在登录时自动启动 watcher：

```powershell
# 安装或更新计划任务
powershell -ExecutionPolicy Bypass -File .\install_or_update_task.ps1

# 删除计划任务
powershell -ExecutionPolicy Bypass -File .\remove_task.ps1
```

计划任务会运行 `zotero_pdf_watch_supervisor.ps1`，它会监控 watcher 进程并在崩溃时自动重启。使用此功能前，需要在 `pdf_to_markdown/settings.json` 中设置好 `pythonw_path`。

## 输出目录结构

如果你的 PDF 库是这样：

```text
Zotero_Papers/
  Paper1.pdf
  Paper1_1.pdf          # supporting information
  subfolder/
    Paper2.pdf
```

生成出来的 Markdown 目录大致如下：

```text
zoterotomarkdown/
  markdown/
    Paper1/
      Paper1.md               # 主论文 Markdown，带 frontmatter
      supporting.md           # supporting PDF 生成的 Markdown
      supporting_assets/      # supporting PDF 的图片资源
      _page_0_Figure_1.jpeg   # 主 PDF 的图片资源
    subfolder/
      Paper2/
        Paper2.md
  state/
    manifest.json             # 转换状态跟踪
  marker_raw/                 # Marker 原始中间产物（运行结束自动清理）
  logs/
    app.log                   # 应用日志
    failed_pdfs.txt           # 失败文件报告
```

### Frontmatter

每个 Markdown 文件前面都会自动写入 YAML frontmatter：

```yaml
---
source_pdf: G:/YourDrive/Zotero_Papers/Paper1.pdf
source_relpath: Paper1.pdf
source_filename: Paper1.pdf
converter: marker_single
converted_at: '2025-01-15T10:30:45.123456+00:00'
torch_device: cuda
force_ocr: true
document_role: main
---

## Full Text

[Marker 生成的 Markdown 内容...]
```

如果是 supporting PDF，还会额外写入 `document_role: supporting`、`supporting_index` 和 `primary_source_pdf` 等字段。

## Supporting PDF 识别规则

一个 PDF 会被当作 supporting material，仅当以下 3 个条件同时满足：

1. **文件名模式匹配**：以 `_1`、`_2` 等结尾，例如 `Paper_1.pdf`
2. **主 PDF 存在**：同目录下存在 `Paper.pdf`
3. **内容检查通过**：转换后的 Markdown 前 4000 个字符中包含以下任意关键词：
   `"supporting information"`、`"supplementary information"`、`"supplemental information"`

只要有任一条件不满足，该 PDF 就会被当作独立论文处理，生成自己的 Markdown bundle。

## 配置项说明

配置文件路径：`pdf_to_markdown/settings.json`

下表中的“默认值”表示当某个配置项被省略时，运行时实际采用的回退值。`settings.example.json` 中为了示例清晰，可能会显式写入与回退值不同的示例配置。

| 键名 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `source_dir` | 是 | -- | Google Drive 上存放源 PDF 的目录 |
| `work_root` | 是 | -- | Google Drive 上输出目录根路径，包含 markdown、state、logs、raw |
| `marker_cli` | 是 | -- | `marker_single` 可执行文件路径 |
| `marker_repo_root` | 是 | -- | Marker 仓库根目录 |
| `hf_home` | 是 | -- | Hugging Face 模型缓存目录 |
| `pythonw_path` | 否 | -- | Windows 计划任务 supervisor 使用的 `pythonw.exe` 路径；启用开机自动启动时必填 |
| `model_cache_dir` | 否 | -- | supervisor 会把它导出为 `MODEL_CACHE_DIR` 环境变量 |
| `torch_device` | 否 | `cuda` | PyTorch 设备，可选 `cuda` 或 `cpu` |
| `output_format` | 否 | `markdown` | Marker 输出格式 |
| `force_ocr` | 否 | `false` | 是否强制 OCR，即使 PDF 本身可直接提取文本 |
| `disable_image_extraction` | 否 | `false` | 是否禁用图片提取 |
| `disable_multiprocessing` | 否 | `false` | 是否禁用 Marker 多进程 |
| `paginate_output` | 否 | `false` | 是否在 Markdown 中加入分页标记 |
| `compute_sha256` | 否 | `false` | 是否额外使用 SHA256 做变化检测 |
| `log_level` | 否 | `INFO` | 日志级别，可选 `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `watch_debounce_seconds` | 否 | `8` | 文件事件进入处理前的等待秒数 |
| `watch_stable_checks` | 否 | `3` | 稳定性检查次数 |
| `watch_stable_interval_seconds` | 否 | `2` | 每次稳定性检查之间的间隔秒数 |
| `watch_rescan_interval_seconds` | 否 | `60` | 定期全量重扫间隔秒数，设为 `0` 表示关闭 |
| `watch_initial_scan` | 否 | `true` | watcher 启动时是否先扫描一次未处理 PDF |

## 状态管理

### Manifest（`state/manifest.json`）

manifest 记录每个 PDF 的转换状态：

- **`success`**：转换成功，保存指纹信息（大小、修改时间，可选 sha256）、输出路径和元数据
- **`failed`**：转换失败，保存错误信息和失败时间

变化检测会比较当前文件的 `size` 和 `mtime_ns`，如果启用了 `compute_sha256`，还会比对 `sha256`。任一值发生变化，就会重新转换。

### 失败文件报告（`logs/failed_pdfs.txt`）

每次 batch 运行后，都会更新一份人类可读的失败报告，包含源路径、相对 key、时间戳和错误信息。

## 全量重建 Markdown 库

如果你想彻底重建整个 Markdown 库：

```powershell
# 1. 先停止所有 watcher
# 2. 备份旧输出（建议改名，不要直接删除）
cd G:\YourDrive\zoterotomarkdown
ren markdown markdown_old
ren state state_old
ren marker_raw marker_raw_old

# 3. 先小范围测试
cd C:\path\to\this\project\pdf_to_markdown
python run_once.py --limit 2 --force

# 4. 确认输出没问题后，再全量重建
python run_once.py --force

# 5. 最后再删除备份
# cd G:\YourDrive\zoterotomarkdown
# rmdir /s markdown_old state_old marker_raw_old
```

## 项目文件

```text
zotero-gdrive-markdown-project/
  pdf_to_markdown/
    __init__.py
    common.py                        # 共用工具、路径处理、安全删除
    pipeline.py                      # 核心转换逻辑、manifest、产物管理
    run_once.py                      # CLI：手动/批量转换与清理
    watch_folder_resilient.py        # CLI：持续监听目录
    settings.json                    # 机器本地配置（已 gitignore）
    settings.example.json            # 配置模板
  backfill_supporting.py             # CLI：补齐漏掉的 supporting PDFs
  zotero_pdf_watch_supervisor.ps1    # PowerShell watcher supervisor
  install_or_update_task.ps1         # 安装 Windows 计划任务
  remove_task.ps1                    # 删除 Windows 计划任务
  requirements.txt                   # Python 依赖
  .gitignore
  LICENSE
  README.zh-CN.markdown
  README.markdown
```

## 说明

- `settings.json` 是机器相关配置，已经加入 `.gitignore`，应提交 `settings.example.json`
- Marker 及其模型缓存应单独安装，不要放进当前仓库
- watcher 针对 Google Drive 的流式同步做了稳定性检查，避免文件尚未完全同步就开始转换
- 所有删除操作都被限制在 `markdown/` 和 `marker_raw/` 根目录范围内，以避免误删其他路径

## 许可证

MIT。详见 `LICENSE`。
