# Engine 0.9：两类任务共享的语义编排

执行宿主是调用此 Skill 的当前助手。宿主读参考或用户描述、解释对象和关系、选画法、运行自写程序、读预览并修正。引擎提供明确输入的检查、构造、编辑和渲染；没有第二个看图模型。Scene 是当前几何、文字、颜色、变换与关系的唯一权威。

## 何时走完整流程

复杂参考图先读 [参考任务指南](../references/reference-reconstruction.md)；自然语言任务读 [描述任务指南](../references/text-design.md)。两者随后共享 [画法选择](../references/construction-selection.md)、FigurePlan、SceneBuilder、工程与导出链。简单扁平单对象可以直接构造，不必先检索、跑测量或填写上下文。

先声明阅读顺序、必要部件、文字、前后关系与未知，再做大量几何。Recipe、合法素材、direct Scene、自写程序都可选。需要复杂膜与胞器时，通用 cell_symbol 不能代替必要结构。方法不由检索分数自动决定。

## 草稿与检查

`plan draft` 请求由宿主填写 `id, task_type, purpose, canvas`，可选 `sources, modules, relations, review_spec, audience`。对象可以省略 construction；工具只补 unresolved，不凭自然语言猜步骤。参考任务需 sources 中的真实 SHA。其余字段沿用 FigurePlan0.1 Schema；没有协议整体升版。

```bash
figure-cli plan draft request.json --output work/draft
```

输出 `figure-plan.json`、空决策的 `planning-context.json` 和 `check.json`。输出目录的父目录必须存在，已有目录拒绝覆盖。宿主继续编辑同一 FigurePlan；修改后须重新绑定上下文的 plan_sha256。

```bash
figure-cli plan check work/draft/figure-plan.json --phase semantic
```

语义检查接受 unresolved，列出待构造、未知、缺对象和未确认项。它不认证对象识别或科学关系。construction 检查会尝试原编译器，验证参数、部件、字体和明确几何；失败返回 needs_input，不暗中运行程序。

```bash
figure-cli plan check plan.json --context planning-context.json --phase construction --fragments fragments.json
```

Python 对应 `planning_tools.draft_plan, context_for, validate_context, check_plan, suggest`。上下文可选；没有上下文的旧计划继续编译。

## 比较实际候选

检索请求示例：`{"query":"细胞", "required_parts":["membrane","mitochondrion"], "scene_version":"0.3", "limit":3}`。query 为必需字符串；可选 style 只用于报告，未声明的画风适配为 unknown。检索请求不含 schema_version。

```bash
figure-cli registry search object-request.json
```

```bash
figure-cli plan suggest object-request.json
```

只返回已有条目，带固定版本、命中字段、缺少部件、真实参数入口、来源许可与限制。分数仅排序；`required_parts` 不会把一个不存在的 mouse 变成命中。符号族只是元数据，不能当可构造对象。实例化数组部件还取决于实际参数，construction check 再核实。找不到时返回空候选；suggest 同时说明 direct/program 路线。没有公开许可的自创 Recipe 仍明确 project_license_unset。

外部资产按候选的 id/version 再 `registry.resolve`，将原始记录交给 `load_asset`。不要把追加了检索字段的候选直接当作已解析资产。在线合法素材搜索仍由宿主按需使用普通工具。

## 最小规划上下文

上下文0.1严格接受：`schema_version, plan_id, plan_sha256, intent, sources`，可选 `objects, relations, evidence, reference_view`。sources 是计划 source_id → 原始文件 SHA 的映射；objects/relations 只引用已有计划 ID，不再保存对象树、path 或当前样式。

每个对象决策包含 `construction_sha256`（对当前 construction 的规范 JSON 摘要）、`reason`、`strategies`、`confirmed`。策略为 filled_shape/stroke/boolean_cut/mask/occlusion/overlay/gradient/repeated_instances/text。可选 candidates 是最多十项 `{kind, reason, id?, version?}`，记录未选理由；evidence_keys 引用内联测量的 result_sha256；unknown 保存具体未知。关系决策只需 reason/confirmed，支持相同可选说明字段。

确认只描述当次构造输入。当前工程是否看过，以 Scene/preview 的已有绑定为准。上下文改变、保存程序或输入快照都不会反向重画用户 Scene。模板参数更新重新绑定快照并将受影响对象 confirmed 置 false。

上下文总 JSON 最多1MiB、嵌套32层、最多64个内联测量结果。evidence 保存实际采用结果的参数、摘要、坐标和测量内容，不能只存临时路径。未打包的原始源标为 external_not_packaged（验证结果列出）；打开工程不依赖源目录或缓存。

可选 reference_view 为 `{sha256, source_id, source_to_view:[a,b,c,d,e,f]}`，sha256 是实际保存的 `reference.png` 字节摘要，与原始文件 SHA 区分。write/load_project 验证它；不重新打开外部源文件。构造程序应先用与保存一致的 PNG 编码得到摘要，或不声明随工程保留的视图。Bundle 保留完整参考文件；组合来源保留结构化输入快照，实例不继承旧审核。

## 自由程序与工程

宿主先明确运行自己编写/已获准执行的程序，得到 Scene 片段，计算 canonical_hash，填 `program: {candidate,sha256}`。片段通过 `--fragments` 的 JSON 映射传入。compile/check/打开/Bundle/diff/accept 都不执行保存源码。`python -I` 只隔离导入路径，不能当安全沙箱。

```bash
figure-cli plan compile plan.json --context planning-context.json --fragments fragments.json --reference source.png --output work/figure
```

Python 为 `compile_plan(plan, fragments=..., planning_context=...)`，返回 Scene/extras/report；使用 `write_project` 正式保存。context 内联到 `generation.json.input.inputs.planning_context`，不新增工程 extra。`--reference` 解码为 EXIF 定向 RGBA；若声明 reference_view，摘要必须与保存的 PNG 完全一致。

对已编辑工程编译 `--candidate`，走原 `generation diff/accept`；用户单独改色、移动、文字保留，同字段异改与派生依赖冲突继续拒绝。candidate 只有 scene.json 与 generation-input.json，不能随意附参考文件。若更换 reference_view，需要明确重建相应参考工程；普通候选不能偷偷换当前参考图。

```bash
figure-cli plan review work/figure
```

review 逐对象列出选定方法、部件ID、必要部件可见性、当前文字、相对纯生成基线的差异、缺失对象与未知。存在一个叫 nucleus 的 path 不等于核结构正确；must_preserve 文本仍需宿主逐项看图。

先看全图模块/连接，再看轮廓/孔洞、内部结构、颜色和文字；目标尺寸与关键局部都要读。最多三轮定向修正。替换旧图时读参考/旧版/新版同尺寸对照。绑定当前实际查看的结果，机械检查与宿主视觉分开，科学审核未要求时保持 not_executed。

## 受限局部测量

```bash
figure-cli evidence measure measure-request.json --output work/measurement
```

Python 为 `measurements.measure(request, output, base_directory=...)`。源相对路径按请求文件所在目录解析；输出目录父目录需存在。详见 [局部测量契约](local-measurements.md)。四种 kind：view/profile/topology/repeat。源 SHA、ROI、问题、背景假设与参数显式给定。结果只提供事实或候选，不创建 Scene、不自动转录文字、不识别生物角色或科学数量。

旧 `evidence IMAGE --channels ...`、prepare/consume 与文字清单保持原行为。source-space ROI 是 `[x,y,width,height]`；新测量的 bbox 是 `[x0,y0,x1,y1]`，不要混用旧候选接口的 bbox 契约。

## 完整小示例与验收

公开可运行示例由 `scripts/verify_v09_workflow.py` 建立草稿→构造选择→内联局部测量→编译→编辑→候选合并→局部导出→Bundle/组合→源移走后重开。自动脚本不声称宿主视觉通过。

```bash
python -I scripts/verify_v09_workflow.py --output work/workflow09
```

实际参考任务与“纳米颗粒靶向肿瘤并通过激光触发释放”的自然语言任务另行保存完整宿主输入、选择理由、实际预览/修正和编辑工程。私有参考及派生物本地单独打包，不进入公共源码或示例。
