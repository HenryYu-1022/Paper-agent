# 多端架构实施计划：Mac 控制端 + Win 运行端

> 目标：在 Google Drive 共享库上实现"Win 端跑 marker 转换、Mac 端巡检孤儿并清理"的分工模式，
> 同时保留单机"all-in-one"模式不受影响。

---

## 1. 拓扑与分工

```
┌─────────────────────────┐         Google Drive           ┌──────────────────────────┐
│  Mac（controller）      │     共享 input/ + output/       │  Win（runner / GPU）     │
│  - 巡检 PDF ↔ MD 对应   │ ◀───────sync (GDrive) ────────▶ │  - 执行 marker_single   │
│  - 清理孤儿 markdown    │                                │  - 跑 convert.py        │
│  - 维护 collection 镜像 │                                │  - 定时任务触发         │
│  - 不装 marker / torch  │                                │  - 不建 collection 镜像 │
└─────────────────────────┘                                └──────────────────────────┘
```

**分工原则**：Win 只"创建/更新"，Mac 只"核查/删除"，避免双端同时写同一 bundle。

---

## 2. 设计要点

- **新代码优先**：不改动 `convert.py` / `pipeline.py` 的核心转换路径，新增 `verify.py`
- **模式切换**：`settings.json` 新增 `run_mode` 字段
  - `all-in-one`（默认）— 单机全功能，行为与现有完全一致
  - `runner` — Win 端，仅转换，不建 collection 镜像
  - `controller` — Mac 端，仅巡检 + 清理孤儿 + 维护镜像
- **删除策略**：两阶段归档，**宽限期 1 天**
  1. 首次发现孤儿 → frontmatter 写入 `orphan_detected_at`
  2. 再次巡检到期 → 移到 `output_root/archive/YYYY-MM-DD/`
- **Win 触发方式**：Windows 任务计划程序定时执行 `convert.py`（不做常驻 watchdog）

---

## 3. 新增 / 修改文件清单

### 3.1 修改 `paper_to_markdown/common.py`
- `load_config` 读取新字段 `run_mode`，默认 `all-in-one`
- 当 `run_mode == "controller"` 时跳过 `marker_cli` / `hf_home` 强校验
- 其他字段校验不变
- **影响面**：只在加载期多一个分支，对现有 all-in-one 用户零影响

### 3.2 修改 `paper_to_markdown/settings.example.json`
新增字段示例：
```jsonc
"run_mode": "all-in-one",         // all-in-one | runner | controller
"orphan_grace_hours": 24,          // controller：孤儿宽限期（小时）
"archive_before_delete": true      // controller：删除前先归档
```

### 3.3 新增 `paper_to_markdown/verify.py`（controller 主脚本）

**职责**：巡检 PDF ↔ Markdown 对应，带宽限期的孤儿归档。

**核心流程**：
```
扫描 output_root/markdown/ 下所有 .md
  → 读 frontmatter: source_relpath, source_pdf_sha256, conversion_status
  → 对照 input_root 当前 PDF 集合

分类：
  ok       — PDF 存在且 sha256 匹配
  stale    — PDF 存在但 sha256 变化（等 runner 重跑，不处理）
  pending  — PDF 有 MD 无（等 runner，不处理）
  orphan   — PDF 消失

Orphan 两阶段：
  1. 首次发现 → frontmatter 写 orphan_detected_at: <ISO timestamp>
  2. 再次巡检到期（now - orphan_detected_at >= orphan_grace_hours）
     → 归档到 output_root/archive/YYYY-MM-DD/
     → archive_before_delete=false 时直接删除
  3. 若孤儿的 PDF 又出现 → 清除 orphan_detected_at
```

**CLI**：
```bash
python verify.py                  # 只报告，不改任何文件
python verify.py --apply          # 执行标记 + 到期归档
python verify.py --watch          # 循环巡检（默认 60s 间隔）
python verify.py --report-json    # 输出 JSON 报告
```

**依赖**：只用 `PyYAML` + 标准库；不需要 marker / torch。

### 3.4 修改 `paper_to_markdown/pipeline.py`（1 处开关）
在写 collection 镜像的那一步读取 `run_mode`：
- `runner` → 跳过镜像构建（避开 Windows symlink 权限问题）
- 其他模式 → 维持现状

### 3.5 修改 `paper_to_markdown/convert.py`（启动守卫）
启动时检查 `run_mode`：
- `controller` → 报错退出："controller 模式不应运行 convert.py"
- 其他 → 正常运行

### 3.6 新增文档 `docs/multi-host.md`（或合并到 README 附录）
包含：
- 两端 settings.json 样板
- Windows 任务计划配置方法
- Mac `launchd` / 常驻运行方法
- 常见问题（GDrive 同步延迟、Windows symlink 等）

---

## 4. 两端配置样板

### Win 端（runner）
```json
{
  "run_mode": "runner",
  "input_root": "G:/My Drive/paper-library/input",
  "output_root": "G:/My Drive/paper-library/output",
  "marker_cli": "C:/path/to/marker_single.exe",
  "hf_home": "C:/Users/you/.cache/huggingface",
  "torch_device": "cuda",
  "python_path": "C:/path/to/python.exe",
  "force_ocr": true,
  "compute_sha256": true
}
```

### Mac 端（controller）
```json
{
  "run_mode": "controller",
  "input_root": "/Users/henry/Library/CloudStorage/GoogleDrive-.../input",
  "output_root": "/Users/henry/Library/CloudStorage/GoogleDrive-.../output",
  "zotero_db_path": "/Users/henry/Zotero/zotero.sqlite",
  "orphan_grace_hours": 24,
  "archive_before_delete": true,
  "collection_mirror_mode": "symlink",
  "zotero_sync_interval_seconds": 60
}
```

### 单机（all-in-one，现有用户）
无需改动；不写 `run_mode` 或写为 `"all-in-one"` 均可。

---

## 5. 运行方式

### Win：Windows 任务计划程序
每 10 分钟触发：
```
程序：C:\path\to\python.exe
参数：convert.py
起始于：C:\path\to\repo\paper_to_markdown
```

### Mac：常驻巡检
```bash
# 前台测试
python paper_to_markdown/verify.py --watch --apply

# 可选：同时跑 collection 镜像同步
python paper_to_markdown/sync_collections.py
```

后续可做成 `launchd` plist（放 `~/Library/LaunchAgents/`）。

---

## 6. 实施顺序

| 步 | 文件 | 可独立测试 |
|---|------|-----------|
| 1 | `common.py` 加 `run_mode` + 放宽 controller 校验 | ✅ 单元测试 |
| 2 | `verify.py` 骨架：扫描 + 报告（不写不删） | ✅ 手跑一次看输出 |
| 3 | `verify.py` 标记阶段：写 `orphan_detected_at` | ✅ 跑一次看 frontmatter 变化 |
| 4 | `verify.py` 归档阶段：到期 move 到 archive | ✅ 造一个假到期 MD 测试 |
| 5 | `verify.py` `--watch` 循环 | ✅ |
| 6 | `pipeline.py` runner 模式跳过镜像 | ✅ Win 端实跑 |
| 7 | `convert.py` controller 模式启动守卫 | ✅ |
| 8 | 文档 + 两份 example settings | — |

每步改完即可验证，不需要等全部完成再测试。

---

## 7. 风险与对策

| 风险 | 对策 |
|------|------|
| GDrive 同步延迟造成误判孤儿 | 1 天宽限期覆盖绝大多数情况；极端情况可临时调大 `orphan_grace_hours` |
| 两端时钟漂移 | `orphan_detected_at` 只在 Mac 单端写读，不涉及跨端时间比较 |
| frontmatter 并发写 | Mac 只改孤儿字段，Win 只改转换字段，路径不重叠；必要时加文件锁（后续增强） |
| 大库 sha256 计算开销 | 巡检时只在"PDF 存在"分支计算；可选 `--skip-hash` 先只按路径核对 |
| Windows symlink 权限 | runner 模式下完全跳过镜像构建，由 Mac controller 独占维护 |

---

## 8. 待后续增强（非阶段 1）

- `verify.py` 结果接入 `monitor.py`（pending / stale / orphan 计数面板）
- 文件锁协议（`.converting.lock`）防止极端并发
- Mac `launchd` plist 模板
- Win watchdog 常驻模式（替代定时任务）
- `controller` 端整合 `sync_collections.py`（让 `verify.py --watch` 顺带维护镜像）

---

## 9. 确认事项

- [ ] 计划整体方向是否符合预期
- [ ] `run_mode` 三个枚举值命名是否 OK（`all-in-one` / `runner` / `controller`）
- [ ] 宽限期 1 天、归档而非直删 是否确认
- [ ] 从第 1 步（`common.py` 改造）开始逐步实施？
