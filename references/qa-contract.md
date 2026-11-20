# 排版质检契约（layout-audit）

确定性几何审计，回答"这些机械项查过没有"，不回答"这张图好不好"。审美、科学含义和期刊规范仍需人工或宿主复核。

## 何时跑

- 复杂机理图、多面板图在交付前跑一次。
- 改动几何、文字或间距后重跑。
- 不要用它替代渲染后的人工查看：它测文字框、描边、画布和间距，看不到遮挡关系、配色观感与箭头语义。

## 命令

```bash
figure-cli layout-audit PROJECT_OR_SVG \
  [--request qa.json] [--output qa.json] [--overlay qa.overlay.svg] \
  [--target-width-mm 183] [--target-dpi 300] [--strict] \
  [--fix [--fix-output CANDIDATE_DIR] [--max-rounds 3]]
```

- 输入可以是完整工程目录（`load_project`）或单个 `.svg`（离线导入，无网络）。
- `--target-width-mm` 给出目标版心宽度后，字号类检查才可计算；没有目标尺寸时该检查为 `not_auditable`。
- 回执一行 JSON：`{status, kind, revision, summary, checks, exemptions, target, verdict, report_path, overlay, fix?}`。

## 请求文件

```json
{"schema_version": "0.1",
 "target": {"width_mm": 183, "dpi": 300},
 "thresholds": {"text_text_overlap": 0.05},
 "exemptions": [
   {"check": "object_canvas_overflow", "objects": ["bleed-bg"], "reason": "出血背景"}
 ],
 "required": [{"id": "panel-a", "kind": "rect", "role": "panel"}],
 "color_semantics": {"label-a": "#112233"}}
```

规则：

- 未知阈值名、未知检查名、空 `reason` 都直接报错，不静默忽略。
- 豁免必须写 `reason`，只对列出的对象生效，命中后状态是 `exempted` 而不是消失。
- 没有命中任何检查的豁免会产生一条 `stale_exemption` INFO，提醒它可能已过期。
- 豁免不能把 `not_auditable` 变成 `clear`。
- `allow_overlap` 只对重叠类检查有效。

## 状态

单项：`fail` / `warn` / `info` / `clear` / `exempted` / `not_auditable`。

总体：

- `failed`：存在任一 `fail`。
- `needs_review`：无 `fail`，但有 `warn` 或 `not_auditable`。
- `clear`：执行过的机械项都没发现问题。
- `not_auditable`：没有任何一项可执行（如空图或全部缺少前提）。

刻意没有 `pass`：`clear` 不是质量认证。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | `clear` 或 `needs_review` |
| 1 | `failed`（`--strict` 下任何 `warn` 也算） |
| 5 | `not_auditable` |

退出码不是唯一信号：判定同样写在回执的 `verdict` 里。

## 自动修正的边界

`--fix` 只尝试四类修改：文字换行并移回容器、标签沿连接线法向避让、同级面板对齐与间距均分、对象移回画布。机制是"在内存副本上施加补丁 → 重新审计 → 目标检查不再 fail/warn 且没有新增 fail 才接受"。

- 每个问题最多处理一次；`max_rounds` 1–5，候选上限 64。
- `semantic.protected`/`locked` 或 role 不在排版角色内的对象只报告，不移动。
- 不改文字内容、不改字号、不改颜色语义。
- 只写 `--fix-output` 指定的新工程；不覆盖输入工程（测试校验字节不变）。
- 修正后仍有 WARN 时状态是 `partially_fixed`，不是"已完美"。

## 诊断 SVG

`--overlay` 输出的是主体 SVG 加一个 `<g id="qa-overlay">`，可直接用 `figure-cli render qa.overlay.svg --output qa.overlay.png` 渲染，也可在浏览器打开：

- 只使用引擎可渲染的 SVG 子集：没有 `<script>`、CSS、外部引用或 `data-*` 属性；每个发现带 `<title>`（kind、状态、对象、数值），浏览器悬停可见。
- 颜色：FAIL 红、WARN 橙、NOT_AUDITABLE 灰、EXEMPTED 蓝；标签前缀 `F:`/`W:`/`N:`/`E:`。
- 画布外的对象，其标签会被夹回画布可见范围，避免诊断信息跟着对象一起被裁掉。
- overlay 是纯诊断产物：Scene 不变，也不会被再次审计。

## 测量口径

- 文字框来自 `fonts.text_line_boxes`：与渲染同源（PIL 度量、1.2 行距、锚点 start/middle/end），按行成框，多行文字自身不算重叠。
- 描边按对象 `stroke_width` 半宽扩展；旋转/曲线元素的描边带是按包围盒的近似，报告里会标 `approximation`。
- 连接器目前只有直线段（`svg.py`），净空检查按线段实现；折线路由尚未支持。
- 与 resvg 实际渲染可能有 1–2px 级别差异，阈值已按此放宽。
- 渐变、透明或无法解析的颜色 → 该项 `not_auditable`，不猜。
