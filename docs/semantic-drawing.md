# 语义绘图指南（目标0.9）与0.4历史结果

## 当前入口与准确基线（2026-11-18）

当前为Engine0.9.0，保留FigurePlan0.1和Scene0.3，协议版本彼此独立。当前接口、调用顺序与文件契约见[语义规划工作流](semantic-planning-workflow.md)，实测与限制见[0.9交付记录](delivery-0.9.0.md)。[0.8工作流](drawing-workflow-0.8.md)保留底座用法；[完整0.9计划](next-stage-plan-0.9.md)是本轮实施依据。下文0.4结果为历史记录。

当前宿主就是执行理解、设计和视觉判断的模型；Skill提供专业方法，引擎执行明确几何，无第二模型服务。参考图与自然语言共享FigurePlan、构造和Scene，不以“有对象清单”冒充理解完成，也不新增第二套对象图。

| 任务 | 先做的判断 | 按需指南 |
|---|---|---|
| 参考图语义复刻 | 信息模块/阅读顺序 → 实体与必要部件 → 流程、引线、放大等关系 → 文字/色义与具体未知 → 几何 | [参考图指南](../references/reference-reconstruction.md) |
| 自然语言设计 | 用户明确对象/关系 → 设计选择 → 科学假设/未知 → 模块层级 → 布局与构造；不擅加制备/给药 | [文字设计指南](../references/text-design.md) |
| 共用构造选择 | 按形态、必要部件、重复、孔洞/遮挡、画风比较实际Recipe/asset/direct/program | [构造选择表](../references/construction-selection.md) |

复杂图先有可检查的结构草稿，未选画法可保持unresolved；简单扁平图保留短流程。Recipe缺少必要部件时必须回退自由程序或混合构造，不为了命中模板损失结构。现有15族Recipe、2个符号族元数据和1个Twemoji柠檬资产不等于完整科学图库，没有mouse/flask/avatar Recipe。

## 共用执行、视觉修正与边界

- **构造已有底座：** SceneBuilder、原子add_many、skia-pathops布尔/定宽轮廓、真text、线性/径向渐变、数值虚线、受限clip/alpha mask、局部occlusion、ports/connectors及受控SVG导入均有现有实现；不再列为“等待0.7实现”。这些仍有[构造](construction-tools.md)、[效果/事务](generation-and-effects.md)和[SVG子集](svg-import.md)限制，不等于任意SVG、全局自动交织或自动识图。
- **输入与当前状态：** FigurePlan/源码/参数/选择记录是输入，接受后的Scene是当前几何、文字、变换、样式和关系的唯一权威。程序由宿主普通工具显式执行；check/compile、打开工程/Bundle、diff/accept不执行保存源码。python -I不是安全沙箱。
- **保护用户修改：** 使用现有B/U/C：B=上次纯生成，U=当前Scene，C=新纯候选。先查diff的合并预览与冲突，再按revision接受；同字段异改保U并报告，成功基线为C，不覆写用户编辑。
- **实际预览：** 读目标尺寸全图和关键局部，按模块/连接→轮廓/比例/孔洞→必要内部结构→颜色/层次→文字检查；替换旧图做同尺寸参考/旧/新对照。问题落到ID与具体动作，默认最多三轮定向修正，每轮渲染后再看，绑定当前Scene/preview记录结论。
- **判断分离：** 工程检查、宿主视觉和科学审核分开；测试、路径数、检索分数、命名部件不能证明图好或科学正确。没有OCR不宣称自动转录。旧历史数字不升级为0.9验收，不恢复已撤出的正式pilot、48项评测、独立宿主消融、人类盲评、领域审核、Runtime首轮接线、模板迁移或实验轮廓扩展。
- **来源与保护：** 外部素材、自家/外部绘图代码、几何与渲染库分别披露实际复用及版本/摘要、许可与修改；目录存在不算采用。私有参考、题图程序和派生工程只本地分包，不进公开源码/示例，不复制系统字体/环境/权重。SVG无嵌入位图/脚本/外链，PNG从同一SVG渲染。本项目独立于QAagent，不初始化Git、不提交推送、不启动部署服务或改网络/安全设置。

## 以下为0.4历史归档，不代表当前能力或待办

日期：2026-11-17。以下契约、命令、结果、测试数和未完成项均按当时记录保留；当时Engine0.4.0、Plan0.3、Patch0.3、Scene0.1，CPU、无GUI、无模型API。特别是末尾关于正式评测与平台验证的旧路线，仅说明历史状态，当前范围以上述0.9计划为准。

## 本次实际改变（0.4历史）

默认工作从整块 ROI 分色改为宿主先理解对象、选择色块/线条/渐变/层次、声明几何，再看实际预览按 ID 修改。复用已有 Scene、构造、曲线、SVG、编辑及导出，不建立另一套绘图数据模型。Skill 不自带另一个 Agent 服务，不绑定模型厂商。

纯 semantic 路径不调用分色、候选搜索、暗脊、骨架或末尾像素审计。Plan 0.1/0.2 原行为保留；Plan 0.3 `mode: pixel` 沿用 0.2 像素流程。旧检查不只有 MAE，拓扑、内部边界、编辑等仍保留；它们不再强制决定允许风格简化的 semantic 图是否合格。

## Plan 0.3

- `mode`: `semantic` 或 `pixel`。semantic 只接受 construct，不接受像素 region/parameters/policy/search_budget 等。
- `canvas`、`resources`、`steps[].object` 仍是现有声明式几何/资源。
- semantic 必需 `brief`：`purpose`、`target_size`、`simplification`、`protected_ids`、`allowed_omissions`、`uncertainties`。brief 不保存路径、坐标或第二份 Scene 树。
- `target_size` 与 canvas 宽高相等。source 可选；有 source 时核验原 SHA/尺寸/ROI，可独立设置目标画布，并记录正确的 source_to_scene 缩放/平移。
- `complexity_limits` 默认 draw_nodes=128、segments=2400，可事先按任务声明。路径段从真正 SVG 解析结果统计，包括隐式坐标命令、圆弧展开和 close；隐藏节点仍计数。
- `protected_ids` 检查存在、可见性标记、透明度、paint、画布交叠；组检查可见后代。不能证明被前景遮挡后的真实覆盖、科学关系或完整拓扑。关键关系仍需宿主检查。
- 构建时和工程提交时重新检查保护项/复杂度。超限或保护对象被删除/隐藏，拒绝提交并保留原工程；复核查询重新计算，不使用初稿旧审计充当当前结果。

无参考自主绘制可直接执行已提供的通用关系示例：

```bash
conda run -n ScientificFigure_skill figure-cli draw examples/plans/semantic-drawing.json --output work/semantic-example
```

有参考时：

```bash
conda run -n ScientificFigure_skill figure-cli reconstruct reference.png --plan plan.json --output work/reconstruction
```

按需仅看 RGB 内部对比证据，跳过量化、暗脊、骨架：

```bash
conda run -n ScientificFigure_skill figure-cli inspect reference.png --analyze --evidence contrast
```

该结果为 `kind: evidence_result, persistent: false`，不假冒可供旧候选搜索使用的 Analysis 0.2。未传 evidence 的旧 analyze 行为保持兼容。

## Patch 0.3：对象与资源同事务

沿用 set/add_subtree/remove_subtree/replace_subtree/reorder，新增：

```json
{
  "schema_version": "0.3",
  "base_revision": 0,
  "operations": [
    {"op":"add_resource","resource":{"id":"new-paint","kind":"linear_gradient","start":[0,0],"end":[0,1],"stops":[[0,"#b3dfcf"],[1,"#397c70"]]}},
    {"op":"set","id":"body","set":{"style":{"fill":"url(#new-paint)"}}}
  ]
}
```

- `update_resource` 使用 `id` 和 `set`，不能修改 ID；`remove_resource` 使用 ID。
- 更新共享资源影响所有引用者。要只改一个对象，先新增资源 ID 再改该对象引用。
- 在副本中完成操作，最终统一 Scene 校验，revision 只加一次；中途临时悬空、最终修好的事务可以通过，最终仍悬空/非法则原子失败。
- 旧无版本 set 和 0.2 patch 保留旧语义。资源仍只有真实 linear_gradient；本次不增加径向渐变、滤镜或新的分割算法。

## 宿主复核回执

`figure-cli review project --json` 返回 `host_review.binding`。宿主实际看过源图/当前预览后，提交符合 `schemas/host-review.schema.json` 的记录：

```json
{
  "schema_version":"0.3",
  "binding":"在此替换为当前 review 返回的 binding 对象",
  "reviewer":{"name":"Calling host","role":"host"},
  "visual":{"status":"passed","findings":[]},
  "scientific":{"status":"not_executed","findings":[]}
}
```

上面是填写说明，binding 必须替换为对象后才是合法输入；不接受这个占位字符串。

```bash
conda run -n ScientificFigure_skill figure-cli review work/figure --record host-review.json --json
```

binding 包含 Scene 内容摘要（含资源）、revision、原 source 摘要、工程 reference/preview 摘要、渲染器/字体/scale 配置摘要。结论包括 passed/failed/uncertain/not_executed；对象发现记录 issue/evidence/action/remaining_uncertainty，`$scene` 代表整体。

记录写入现有 review.json 和工程回执，未录入不执行视觉判定；修改或克隆编辑后保留旧声明并标 `stale_after_change`。旧版本工程可加载。引擎验证摘要与格式，不证明宿主真的看了图，也不会调用视觉模型或代替科学评阅。执行 success 与质量 needs_review 继续独立。

## 四张旧图：先不改引擎，再验证新接口

1. 从原 0.3 已用栅格读取相同头像、柠檬、烧瓶和生物局部。未打开公开源 SVG 的几何来代替重绘；只读栅格和取色，宿主声明曲线/形状。
2. 在引擎还是 0.3.0 时，真实重新执行原 auto，并用 Plan 0.1 全 construct 做新路线探针。产物：`outputs/semantic-v04-probe/`，版本与声明保留。无需新算法就能改善线条与对象组织。
3. 实现新接口后，使用相同构造声明通过真正 CLI 重建为 Plan 0.3；完整工程在 `outputs/semantic-v04-delivery/`。baseline 是本次改代码前生成的 0.3 auto 工程，不用 0.4 冒充冻结旧实现。
4. 宿主实际看过同尺度总对照、单图预览和烧瓶差异，记录前三图 visual=passed（在声明简化范围内），生物 visual=uncertain；四图 scientific=not_executed。

| 旧图 | 自动→语义 绘制节点 | 自动→语义 解析路径段 | 实际视觉观察 |
|---|---:|---:|---|
| 头像 | 14→3 | 411→4 | 圆、头、身体连续，蓝色为真渐变；有意省略截图边框和背景杂色；不是原截图逐像素复刻 |
| 柠檬 | 3→4 | 312→21 | 果实平滑；前叶、后叶、叶柄独立；没有给扁平参考强加果实渐变；轮廓比例仍有小差异 |
| 烧瓶 | 1→1 | 135→24 | 瓶口、肩部、底部圆滑；内部是真孔洞，仍有轻微轮廓差异 |
| 生物局部 | 6→35 | 4023→119 | 15 条膜带独立；暗褶线、中心结构、引线分组，3 个真实渐变；小细节、连接、折叠间距与原体积感仍未完整恢复，保持待复核 |

**计数口径改变已明示：** 本表使用实际解析的 path 绘制/close 操作，包含隐式命令，和旧报告只数 L/C 等命令字母的 367/309/133/3263 不同。前后按同一解析器重算，不能把两种口径混比。ellipse/rect 单独作为绘制节点统计，不伪装为零复杂度；段数减少本身不是验收。

[同尺度四图对照](../outputs/semantic-v04-delivery/comparison.png)。各项目 `semantic/figure.svg`、`preview.png`、`scene.json`、`review.json` 可直接打开。

## 实际编辑与验证

- 四图均通过 CLI 单独改目标对象颜色并生成编辑克隆，核对仅目标对象记录变化、实际预览变化，导出独立 SVG/PNG。烧瓶改色仍保留复合孔洞。
- 柠檬叶柄、生物标注线分别实际改描边宽度；生物 `fold-right-near` 另实际移动，其他对象记录不变。
- 头像实际更新渐变色标，几何对象不变、预览发生变化；既有宿主通过记录变为 stale_after_change。生物移动后的旧复核也过期。
- 最终全套回归实际通过 **186 passed、10 subtests passed（10.63 秒）**，包含提交时保护对象检查、资源事务、复核绑定/过期和旧接口兼容。测试不替代视觉成品。
- 已真实执行无 source 的 semantic draw，生成带可编辑文字、渐变和连接线的五对象关系图：`outputs/semantic-v04-autonomous/`。这是自主构造接口验证，不能称为复杂科研机制自动创作完成。

## 实验限制与未完成项

这不是严格匹配宿主干预的因果实验。auto 组没有宿主纠正，新组包含宿主明确几何；本会话看过旧结果，没有使用独立新上下文，宿主推理/声明用时也未单独计量。没有宣称达到原计划的独立上下文、15 分钟/24 次渲染预算对照协议，也没有外部专家盲评。记录这些偏差，不把开发结果当保留集泛化证据。

Inkvec/VTracer 本机 CLI 与 Python 包均未安装，本次没有执行外部基线；它们仍是可选几何参照，未为四图引入模型、权重或新运行依赖。没有实现新的邻接区域合并算法或任意外部 SVG 导入。

生物局部是结构更清楚的风格化近似，**尚未高保真或科学验收通过**。48 张来源隔离集、独立专家、完整自动排版、复杂自主绘制、WSL/Linux 无 GUI 实测继续在完整路线中；当前仅 Windows/Anaconda CPU 实际验证。

公开源码/示例包排除用户头像、生物截图和派生工程。四图本机工程可提供给原用户私下查看，不表示获得公开再分发许可。公开柠檬/烧瓶保留 CC BY 4.0 署名、源/派生说明；包内示例不作为 Engine 的题材模板。
