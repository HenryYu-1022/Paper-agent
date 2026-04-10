# Paper-agent

English version: [README.markdown](README.markdown)

`paper-agent` 使用 [Marker](https://github.com/datalab-to/marker) 把一个 PDF 目录树转换成 Markdown 资料库。它不依赖 Google Drive，也不要求必须来自 Zotero；只要你的 PDF 已经整理在某个根目录下，无论是本地目录、云盘挂载目录，还是 `zotero-attanger` 导出的 collection 层级目录，都可以直接处理。

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

1. Python 3.10+
2. 按你的平台安装 PyTorch
3. 安装 Marker

```bash
pip install marker-pdf
# 如果还要支持更多文档格式：
pip install marker-pdf[full]
```

4. 安装项目依赖

```bash
pip install -r requirements.txt
```

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

`paper_to_markdown/` 目录里的脚本要在该目录中执行。根目录下的工具脚本，例如 `backfill_supporting.py`、`monitor_conversion_progress.py`、`install_or_update_watch_task.ps1`、`remove_watch_task.ps1`、`install_or_update_launch_agent.sh`、`remove_launch_agent.sh`，要在项目根目录执行。

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
zsh ./install_or_update_launch_agent.sh
zsh ./remove_launch_agent.sh
```

- supervisor：`paper_agent_watch_supervisor.sh`
- 默认 label：`com.paper.agent.watch`
- plist 路径：`~/Library/LaunchAgents/com.paper.agent.watch.plist`

### Windows 计划任务

```powershell
powershell -ExecutionPolicy Bypass -File .\install_or_update_watch_task.ps1
powershell -ExecutionPolicy Bypass -File .\remove_watch_task.ps1
```

- supervisor：`paper_agent_watch_supervisor.ps1`
- 默认任务名：`PaperAgentWatch`

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

## 仓库结构

```text
paper-agent/
  paper_to_markdown/
    __init__.py
    common.py
    pipeline.py
    run_once.py
    watch_folder_resilient.py
    settings.json
    settings.example.json
  backfill_supporting.py
  monitor_conversion_progress.py
  paper_agent_watch_supervisor.ps1
  paper_agent_watch_supervisor.sh
  install_or_update_watch_task.ps1
  remove_watch_task.ps1
  install_or_update_launch_agent.sh
  remove_launch_agent.sh
  requirements.txt
  README.markdown
  README.zh-CN.markdown
```

## 说明

- `settings.json` 是机器相关配置，已经加入 `.gitignore`
- 模板 `settings.example.json` 开头的 `_comment_*` 字段是给人看的说明，保留也不会影响程序运行
- `paper-agent` 不依赖 Google Drive，本地目录可直接使用
- 如果你使用 `zotero-attanger`，把它导出的 PDF 根目录填到 `input_root` 即可
- macOS LaunchAgent 不继承你交互 shell 的 PATH，而且脚本只读取 `python_path`，所以这个字段最好写绝对路径
- Windows 后台任务会优先读 `pythonw_path`；如果没填，再回退到 `python_path`

## 许可证

MIT。详见 `LICENSE`。
