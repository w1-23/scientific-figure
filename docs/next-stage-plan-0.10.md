# vNext 技术方案与 0.10 落地记录

本文件承接 0.9 计划，记录 vNext 方向与本轮已落地的部分。方案书把基线写成 `8e9177c`；核对后真实 main 是 `3650083`（`8e9177c` 是它的祖先），本文件以 `3650083` 为基线，细节见 [vnext-m0-baseline.md](vnext-m0-baseline.md)。

## 方向

1. **可编辑矢量优先**：Scene 是唯一事实来源，SVG 是两个导出视图，不是中间产物。
2. **宿主原生生图可选**：图像生成只作为候选登记，必须回到 Scene 才能编辑、审阅和复验。
3. **确定性排版质检**：用程序替代"凭感觉看 PNG"能客观测量的部分。
4. **字体预设**：给出建议组合与预检，不静默替换、不随包分发字体。

## 里程碑

| 里程碑 | 内容 | 本轮状态 |
|---|---|---|
| M0 | 基线冻结（提交、版本、回归、字体、依赖、导出字节） | 已记录，无代码改动 |
| M1 | Scene 级确定性排版 QA（报告 + 诊断 SVG + CLI） | 已实现 |
| M2 | 受限自动修正（换行/避让/对齐/回移，候选→重审→接受） | 已实现 |
| M3 | 宿主生图候选登记与 `provider` 只读状态 | 未实现，仅保留命名 |
| M4 | API-SVG 严格导入与二次编辑 | 未实现 |
| M5 | 多字体 `<tspan>` 混排与 resvg 字体映射 | 未实现，当前仍单字体 |
| M6 | 跨宿主适配器与真实案例评测 | 未实现 |

M3–M6 在本轮**没有任何代码**：没有 `providers.py`、`candidate.py`、网络调用或密钥读取。

## M0 基线（事实）

- main `3650083c2d75b4a093827dd595ad46b9788b8e1e`，Engine 0.9.0，Python 3.12.16。
- `pytest -q`：1056 passed，5 skipped（Windows 符号链接权限），80.61 s。
- 渲染 resvg-py 0.5.0（CPU）；字体 Arial，SHA-256 `edfbd22a…a634404`。
- `work/demo-citrus/project/figure.svg` SHA-256 `91efb13e…efd80`。

本轮的兼容门槛：现有命令、工程和两份 SVG 导出行为不变；新能力全部 opt-in。

## M1：确定性排版 QA

模块：`layout_audit.py`（报告）、`qa_overlay.py`（诊断 SVG）、`schemas/scene-qa-request.schema.json`、`schemas/scene-qa-report.schema.json`。

十项检查，全部基于现有 `Geometry` 与 `fonts.text_line_boxes`：

| 检查 | 判定 | 默认 |
|---|---|---|
| 文字重叠 | 交叠面积 / 较小行框面积 ≥ 5% | FAIL |
| 文字与描边相交 | 线框内缩 0.6pt 后与描边带相交 | FAIL（旋转/曲线近似 WARN） |
| 容器内边距 | 文字出容器 → FAIL；内边距 < 2pt → WARN | FAIL |
| 画布越界 | 出界 > 0.5pt FAIL，> 0 WARN | FAIL |
| 有效字号 | 由 transform 缩放 × 目标物理尺寸折算；< 5pt FAIL，> 24pt WARN | FAIL / NOT_AUDITABLE |
| 面板对齐与 gutter | 同 role 同父级：边缘差 > 0.5pt 或间距偏离中位数 > 1.5pt | WARN |
| 连接器净空 | 线段与文字行框距离 ≤ 1pt；穿过为 FAIL | WARN/FAIL |
| 对比度与语义色 | WCAG 相对亮度；锁定色被改 → FAIL | WARN |
| 负空间 | 内容占比、四边留白 | INFO/WARN |
| 必需结构 | `required` 缺失/不可见/属性不符 | FAIL |

状态词汇刻意不含 `pass`：单项 `fail|warn|info|clear|exempted|not_auditable`，总体 `failed|needs_review|clear|not_auditable`。`clear` 只表示这些机械项查过且没发现。

报告契约、豁免、退出码与边界见 [references/qa-contract.md](../references/qa-contract.md)。

## M2：受限自动修正

模块：`auto_layout_fix.py`。只做四类低风险修改，且只在 `deepcopy(scene)` 上试：

| 问题 | 修正 |
|---|---|
| 文字溢出容器 | 按容器内宽换行（不改词、不改字号），并把换行后的块移回容器内 |
| 标签压在连接线上 | 沿法向 ≤ 12 单位、最多 6 个候选位移，取第一个通过者 |
| 面板 gutter/对齐不一致 | 按中位间距均分、按中位顶边对齐 |
| 对象越出画布 | 整体平移回画布内（不缩放） |

接受条件：目标检查不再 `fail`/`warn`，且没有新增 `fail`。每个问题最多处理一次，`max_rounds` 1–5，候选上限 64。被 `semantic.protected`/`locked` 标记或 role 不属于排版角色的对象只报告、不移动。结果写新目录，原工程零字节改动（测试覆盖）。

## 字体预设

`data/font_profile.json` 是建议数据：英文 Times New Roman、中文 Microsoft YaHei、数学 Cambria Math，`fallback: single_font_only`。`fonts.resolve_profile()` 扫描本机字体，报告每个 role 的解析路径、face、SHA-256 与示例字符缺失；缺字体时记入 `missing`，不做替换。`font-probe --profile` 把结果并入预检报告。渲染链路未改，仍为单字体。

## 本轮验证

- 新增 `tests/test_layout_audit.py`、`tests/test_auto_layout_fix.py`、`tests/test_font_profile.py`，并扩展 CLI 集成测试。
- 真实工程 `work/demo-citrus/project`（501 对象、45 文字）审计不再产生误报的 FAIL；剩余 WARN 是刻意叠印/近似判定。
- `--fix` 只在显式给出 `--fix-output` 时写新工程。

## 下一轮入口

M3 的功能名（`candidate register`、`provider status`）只作为路标写在本文档，不建空实现。真正开始 M3 前需要先确认：宿主的图片提供方、密钥来源与用途边界、以及"候选必须回到 Scene"的验收方式。
