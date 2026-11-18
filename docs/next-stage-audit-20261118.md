# Scientific Figure Skill 下一阶段现状审计

审计日期：2026-11-18。实际基线：Engine **0.7.0**。本轮只冻结证据、读取源码、研究外部实现并制定计划；没有实施新 Recipe/Planner/Registry、修改核心或升级版本。

配套：[外部源码研究](../research/next-stage-source-review-20261118.md)、[下一阶段完整实施计划](next-stage-plan-0.8.md)。旧 [0.7 批准计划](implementation-plan.md) 保留为历史，其 0.6 基线和“尚未实施”不代表今天状态；当前交付事实见 [0.7 交付说明](delivery-0.7.0.md)。

## 1. 结论

当前已有一套可靠的可编辑矢量执行链：宿主提供构造程序或最终几何，builder/pathops 生成 Scene，Scene 决定 SVG，PNG 来自同一 SVG。事务保存、安全重跑、受限 clip/mask、局部遮挡、严格子集 SVG 导入、Bundle 和组合均已有实现与不同范围的实际验证。

主要缺口集中在 **可复用且可变参的绘图方法、规划到执行的稳定协议、模板实例更新，以及组合后的文字/风格/保护约束闭包**。不是再做一次图像分割，也不是重建已有 Bundle 或局部交织模块。

语义标签、语义规划和可执行语义构造必须分开：当前 Plan/context 可以表达对象角色、意图、表示方式和约束，但语义 construct 仍需宿主给出具体几何；引擎不会自行理解图片或自动挑选 Recipe。台灯和纳米载体成功说明宿主能用程序驱动引擎，不证明已有通用 Recipe 库或自主科研规划器。

## 2. 冻结及本轮实跑

| 项目 | 实际结果与证据 |
|---|---|
| 代码冻结 | [code-freeze.json](../outputs/next-stage-audit-20261118/code-freeze.json)，104 个文件，包含 src Python/Schema、顶层 tests、文档/参考/研究 Markdown 及明确列出的配置。不是整个工作区快照；未包含全部 scripts、外部仓库、依赖二进制和私图 |
| 副本校验 | 采集脚本逐个比较源与 `code-snapshot/` SHA；没有改写冻结文件 |
| 环境 | [environment.json](../outputs/next-stage-audit-20261118/environment.json)，Python 3.12.16，Conda `ScientificFigure_skill`，应用 0.7.0 |
| doctor | [doctor.json](../outputs/next-stage-audit-20261118/doctor.json)：resvg-py 0.5.0、skia-pathops 0.9.2、FontTools 4.68.0、CPU；Arial 记录 family 与 SHA |
| 当前回归 | **557 passed、2 skipped、10 subtests passed，37.90s**；[文本](../outputs/next-stage-audit-20261118/pytest-baseline.txt)、[JUnit](../outputs/next-stage-audit-20261118/pytest-baseline.xml) |
| 两项跳过 | test_bundles.py:152 与 test_evidence.py:352，Windows 符号链接权限不可用；不是对应安全分支已在本机通过 |
| 历史证据索引 | [existing-evidence-index.json](../outputs/next-stage-audit-20261118/existing-evidence-index.json)，17 份报告的 SHA/大小；本轮没有重新运行这些案例 |
| pathops API 实查 | [pathops-api.json](../outputs/next-stage-audit-20261118/pathops-api.json)：存在 simplify/stroke，未发现通用 offset 方法；这项是 API 检查，不是新几何性能试验 |

测试只证明对应程序行为。没有新进行三人盲评、领域科学核验、跨来源保留集评估或 Windows/Linux 新版性能对比。

## 3. 能力矩阵

标记以具体范围为单位：“已实现且通过”仅适用于右列证据和边界，不能扩成整个任务类别通过。

| 能力 | 状态 | 依据与边界 |
|---|---|---|
| Scene→SVG→PNG、组/局部变换/独立 ID | 已实现且通过 | 核心回归、历史交付和实际绘图；PNG 同源 |
| 真实 text、线性渐变、复合孔洞 | 已实现且通过 | 真实节点及编辑/导出验证；字体限明确选择的本机字体 |
| 单对象编辑、选中导出、事务保存 | 已实现且通过 | patch、revision/receipt、stage 失败回滚；成功事务不是 undo/redo |
| boolean、round 定宽描边转轮廓 | 已实现且通过 | 单一 skia-pathops 适配层；float32、arc/conic 近似边界明确 |
| 任意 path 降段、通用正负 offset、变量宽 stroke | 部分实现 | 有采样拟合与定宽轮廓；没有所述完整 API/保证 |
| SceneBuilder 局部坐标、资源、clone | 已实现且通过 | clone 独立副本、typed refs 完整重映射；不是模板更新 |
| builder 大图性能 | 已实现但未充分验证 | 纳米载体 build 43.592s；每次 _commit deepcopy+validate 全 Scene，尚未 profile，不能直接断言全部耗时归因 |
| 程序生成 provenance、B/U/C 重跑 | 已实现且通过 | 源码惰性数据；所有权/依赖/引用/顺序、陈旧候选和接受回执参与；新 Planner 来源尚未接入 |
| clip/有限 alpha mask/局部 occlusion | 已实现且通过 | Scene0.2 明确类型及预算；不支持任意 SVG mask/filter/半透明组布尔 |
| 任意移动后交织都能正确恢复 | 部分实现 | region 内重求值；新增/消失交叉或移出范围 needs_review；现有失败保留 |
| 严格 SVG 子集导入 | 已实现且通过 | Twemoji 个例原生/导入像素一致；不是任意 SVG 往返 |
| Plan0.3 语义 brief/context | 已实现且通过 | 意图、标签、来源和机械约束；不自动生成几何 |
| 统一 FigurePlan/ModulePlan/ObjectSpec/RelationSpec | 尚未实现 | 现有协议无此完整上层；应薄层降低到现有 Plan/Scene/context |
| Drawing Recipe 协议及可信目录 | 尚未实现 | 有题图程序、重复函数和构造底座，无统一参数/部件/版本/验收合同 |
| 模板绑定、实例覆盖、选择更新 | 尚未实现 | 当前 clone 与函数重复均不能代替 |
| 自动图像语义识别/独立模型服务 | 不建议实现 | 本任务明确交给宿主，不增产品 Agent/供应商 SDK |
| 现有 StyleProfile/token/机械检查 | 已实现且通过 | color/number/font/linear_gradient，初构造显式解析；attach 不自动重画 |
| 全图风格适配与科研颜色保护 | 部分实现 | 缺显式锁定/允许重映射/冲突策略及组合级执行 |
| Text Inventory | 部分实现 | 已有落地对象 text manifest、uncertainty/evidence refs；缺未落地 OCR 候选、原文/规范/建议细分 |
| 径向渐变、字重/斜体/丰富排版 | 尚未实现 | 当前严格字段不接受；需版本化且全链覆盖 |
| layout 意图/检查 | 已实现且通过 | aligned_left/top、same_width/height、contains、left_of、above 等检查；不是求解器 |
| prepare/evidence/ROI 映射 | 已实现且通过 | 实际生成板、manifest、来源和变换；不产生科学语义或最终 SVG |
| OCR/重复对象候选 | 尚未实现 | doctor 的 ocr=false；不能把已有 detail 图说成 OCR |
| 宿主首次推理前 Runtime 集成 | 尚未实现 | preflight_status 固定 not_integrated；无 prepared 消费闭环 |
| 完整成员 Bundle | 已实现且通过 | 完整工程、摘要、独立局部 ID、不可变包；不是 SVG 集合 |
| 可编辑 compose/live connector | 已实现且通过 | 几何/资源/clip/mask/occlusion/连接重映射与多实例；bbox 锚点能力有限 |
| compose 汇总 context/text/layout/style/protection | 部分实现 | 尚无组合级完整语义闭包；成员 review 不应继承为组合通过 |
| Named persistent ports、自动整图布局 | 尚未实现 | builder anchor 是程序辅助点；connector 现用 bbox 锚点；布局由宿主给 |
| Known Asset/Recipe Registry | 尚未实现 | 受控导入不等于注册表；缺锁定版本、选择规则和合法分发目录 |
| 三类任务 A/B/C 的完整泛化能力 | 已实现但未充分验证 | 已有个例与开发对照，无来源隔离保留集成绩 |
| 独立人类盲评/科学核验 | 尚未实现 | 宿主视觉检查不能代替三名评价者或领域判断 |
| 浏览器/Blender/训练/扩散/第二主几何引擎 | 不建议实现 | 超出当前独立 CPU 无 GUI 执行链目标 |

## 4. 关键源码审计点

### 4.1 规划和上下文不是执行程序

[reconstruct.py](../src/scientific_figure/reconstruct.py)、[Plan0.3 Schema](../src/scientific_figure/schemas/reconstruction-plan-v03.schema.json)、[context.py](../src/scientific_figure/context.py) 表达角色、意图和构造约束；实际对象仍需几何。`depends_on` 校验顺序不等于按语义推导几何。新 Planner 应组织宿主选择与阶段检查，输出现有执行协议，避免新建一份可编辑坐标树。

现有 StyleProfile 显式解析 token，Scene 保留解析值。文本和布局 manifest 是意图/来源/检查索引，不能逆向接管 Scene 当前文字或位置。

### 4.2 builder 和几何能力需要收口

[builder.py:32](../src/scientific_figure/builder.py:32) 每次提交复制全 Scene 并验证；[builder.py:84](../src/scientific_figure/builder.py:84) 的 anchor 属辅助状态；[builder.py:126](../src/scientific_figure/builder.py:126) 的 clone 创建独立对象。

[pathops_backend.py:94](../src/scientific_figure/pathops_backend.py:94) 的 boolean 是真正面域运算；[geometry.py](../src/scientific_figure/geometry.py) 的 union 只是 bbox 合并。`curves.smooth_path` 只接闭合 M/L/Z 多边形，fit_open_path 面向采样点。不能承诺任意 path 平滑、拓扑保护或最大轮廓误差。

优先提案是增加一次验证的批量事务构造、统一 namespace 和 Recipe 部件输出，先 profile 再比较。没有理由再次建设第二套布尔后端。

### 4.3 新 metadata 必须接入真正闭包

[project.py:23](../src/scientific_figure/project.py:23) 对 extras 有显式名单；write/load、receipt、Bundle 和审阅都会处理相关文件。[generation.py:112](../src/scientific_figure/generation.py:112) 对 provenance 有白名单，当前只允许 generation/structural_edits/context/有限 brief。

因此不能直接往 Scene provenance 塞 FigurePlan、模板或 composition 字段；也不能写了一个旁路 JSON 就声称工程支持。下一阶段要注册、验证、绑定摘要、迁移和打包。加载工程、generation diff/accept 和解包仍不得执行保存源码。

### 4.4 组合几何通过，不等于成员约束已整合

[composition.py](../src/scientific_figure/composition.py) 复制 Scene 和 typed refs，保局部效果和 connector；[geometry.py:146](../src/scientific_figure/geometry.py:146) 的连接锚点来自世界 bbox。[bundles.py](../src/scientific_figure/bundles.py) 保完整成员工程。

成员 text/context/layout/token/protected IDs 尚未汇总成组合级闭包；同名 token、文字清单 ID 和受保护对象需要明确 remap/冲突策略。组合后的视觉、布局和科学关系必须重新复核。

### 4.5 prepare 的保证止于证据生成

[preparation.py:24](../src/scientific_figure/preparation.py:24) 生成实际文件与 manifest，但 `preflight_status=not_integrated`；draw/reconstruct 当前没有 prepared 消费入口。独立 Skill 不能保证任意宿主首次推理前先执行，需实际 Runtime 插件/入口对接与消费回执。

## 5. 现有任务证据，不抹掉失败

| 案例 | 真实状态 | 对下一阶段的含义 |
|---|---|---|
| 六图开发集 | 头像/柠檬/烧瓶/Researcher 在声明范围通过；生物 uncertain；交织标志 failed | 仍是已知开发样本，不能作保留集；膜带和连接错误未因工具链完善而消失 |
| 陌生交织开发例 | 基础及多种编辑通过；invalid-region、组合左实例越界 needs_review；右实例不变 | 证明有限 live 关系和隔离，不证明任意交织图自动恢复 |
| 台灯 | 自家引擎程序绘制，25 对象、5 次引擎渲染；插画重绘范围通过 | 可提炼参数化体块/局部坐标方法，尚无通用台灯 Recipe 合同 |
| 纳米载体整图 | 826 对象、20 资源、22 text、6 次渲染，1 轮视觉修正；编辑三 ID 与两个局部导出通过 | 有整图程序构造能力；小鼠、膜带、细胞体积有声明简化；科学核验未执行 |
| 原科研架构截图 | input_insufficient | 低清不能补造；纯 NL 重设计不能抵作原图准确重建 |
| 外部两 Skill | 原示例与独立宿主任务历史实跑；schematic 直接移动连接不 live | 学习方法，不用原 gate 代替本项目编辑验收 |
| Windows/Linux 0.7 wheel | Windows 应用独立安装并继承 Conda 依赖；WSL 原生 Linux 目录实跑通过 | 不等于全依赖离线新机安装；DrvFS 原子发布失败保留；无跨平台像素相等结论 |

纳米载体 build 43.592s、程序总 46.916s，模型时间 unavailable。不能用对象数量证明成图质量，也不能把尚未 profile 的总耗时当某单函数的性能测量。

## 6. 外部研究对产品的具体影响

1. draw/schematic 的对象分解、局部构造、文字测量和阶段预览值得吸收；示例图、生成时锚点和最终 gate 均不是通用 Recipe/live 更新机制。
2. Inkvec 的 coverage/填色候选/共享边可作窄接口参考，但自动分区、强制对称和 repair 不能保证科研结构。固定源码的 repair 存在恢复仍交叉原拟合的分支。
3. VTracer cutout 的共享边比独立轮廓更一致，仍不保证抗锯齿无缝；颜色区不是语义对象，gradient_step 不是 SVG 渐变。
4. Chat2SVG 对象拆分与看图修正可借鉴；根许可未明确，不复制实现；扩散/SAM/优化栈不进入主线。
5. StarVector/LLM4SVG 不能直接提供本产品的工程对象协议；不引权重、训练或独立模型 API。
6. 继续复用已有 skia-pathops 适配层。Paper 本轮阅读不完整，按有限证据记录，不用未经读到的算法保证填空。

细节、固定 SHA、文件、许可与未读范围见[外部研究报告](../research/next-stage-source-review-20261118.md)。

## 7. 优先顺序与保留风险

优先落地：批量构造与 namespace → 小型可变参 Recipe → 薄规划协议/编译 → 模板实例选择更新 → Registry → Evidence 消费闭环 → 组合约束闭包 → 来源隔离评测与交付。P0—P9 完整依赖、API、文件及阶段门槛见[完整计划](next-stage-plan-0.8.md)。

必须显式保留：稳定部件 ID、B/U/C 与用户编辑、模板局部覆盖、未知科学结构、保护色含义、strict SVG 能力限制、Runtime 集成边界、私图隔离、未执行人类评阅。现有 SKILL 的部分 Engine0.6 标题和 builder 文件头的 Scene0.1/no mask 描述已落后于实现，实施时统一文档；本轮没有借文档漂移误判实际能力，也没有改动旧基线。

## 8. 本轮交付状态

审计、源码研究及下一阶段完整方案属于本轮产物。P0 是明确范围的代码冻结和当前测试；P1 完成可访问来源的机制研究，Paper 完整源码/官方文档与三篇论文全文仍受限。没有因此宣称 P1 的全部原始来源已完整读完。

本轮不改 Engine0.7、不覆盖旧交付 ZIP、不初始化 Git、不提交 QAagent、不部署、不上传私图。后续大规模实施等待用户确认完整计划。
