# 静态源码审计记录

## 源码审计结论与复用决策

审计日期：2026-11-17。以下为固定提交的静态源码审查；未安装、执行这些项目或测试模型，不将 README、Skill 指令或测试文件的存在等同于已验证运行结果。

| 项目／固定提交 | 实际能力与限制 | 本项目处理 | 代码许可 |
|---|---|---|---|
| cell_su7 `209ced088b4fa5056b01dbd15e6fe3df100d2364` | 图片入口调用小描付费路径识别；去字图与文字清单由外部工作流提供。文字回填、坐标映射、复合路径缓存有真实代码；缓存将组扁平化，不负责语义分组。无独立无 GUI SVG→PNG 链路。 | 参考或抽取文字坐标／回填与缓存校验小模块；不继承付费链、自动同步、Codex 桌面唤醒、PPT/Illustrator 输出。 | MIT |
| Fig2Slide `0d1db3d4ab1407539afe5af7c2eabf2abf38d3a6` | 有 PaddleOCR 适配、矩形/直线检测、基础 manifest。默认分析插入整幅参考图；SVG exporter 无任意 path，image 使用本机 file URI；parent/group/连接字段未形成完整输出语义。渲染对照修复主要是代理工作流要求。 | 借鉴 OCR 后端接口、文字核对、区域路由与 QA 流程；不复用现有 exporter 或整图叠加基线。 | Apache-2.0 |
| codex-sci-ppt `599394eb745c8eae7c10d64fc1792fe3fe7e8fea` | Scene 字段校验和程序化几何组合真实存在；Scene 直接输出 PPTX，通用贝塞尔在另一条 SVG 缓存链路。ID 与 PPT 选择窗格耦合，组和复杂路径处理不满足独立对象模型。 | 学习字段校验、语义图元返回几何的思路、LAB 聚类与几何拟合；重写 SVG Scene 后端。 | MIT，保留继承的第三方声明 |
| Scientific PPT Drawing `a207937b44d72484ac6e5758cd9a80590f99479d` | 有受限 SVG→曲线 atom 编译、FontTools 贝塞尔归一化、候选矢量化与 ROI/边缘评分。编译展开 g；OpenCV 预览拒绝文字且不完整实现 SVG 填充/虚线语义；最终 CLI 仍输出 PPTX。 | 可抽取经核验的小型路径/检查辅助函数；借鉴 ROI 与复杂度联合筛选。另建保留组、compound path、真实文字的 Scene 与 SVG 输出。 | 目标 Skill 为 MIT，需保留其 NOTICE 继承声明 |

### 关键源码证据

- cell_su7：[图片入口 `run_from_image.main`](https://github.com/yrui-cmd/cell_su7/blob/209ced088b4fa5056b01dbd15e6fe3df100d2364/plugins/cell_su7/skills/cell_su7/scripts/run_from_image.py)、[文字回填 `merge_live_text`](https://github.com/yrui-cmd/cell_su7/blob/209ced088b4fa5056b01dbd15e6fe3df100d2364/plugins/cell_su7/skills/cell_su7/scripts/merge_live_text.py)、[几何缓存 `prepare_geometry_cache`](https://github.com/yrui-cmd/cell_su7/blob/209ced088b4fa5056b01dbd15e6fe3df100d2364/plugins/cell_su7/skills/cell_su7/scripts/prepare_geometry_cache.py)。主线程复核图片入口，确认它接收已去字输入并调用付费适配器。
- Fig2Slide：[pipeline](https://github.com/Yutao-Yang/fig2slide/blob/0d1db3d4ab1407539afe5af7c2eabf2abf38d3a6/src/fig2slide/pipeline.py)、[analyze_image](https://github.com/Yutao-Yang/fig2slide/blob/0d1db3d4ab1407539afe5af7c2eabf2abf38d3a6/src/fig2slide/analyze.py)、[SVG exporter](https://github.com/Yutao-Yang/fig2slide/blob/0d1db3d4ab1407539afe5af7c2eabf2abf38d3a6/src/fig2slide/svg_export.py)。主线程复核 exporter，确认没有 path 分支且图像为 file URI。
- codex-sci-ppt：[Scene 校验](https://github.com/yulinpeng2023-create/codex-sci-ppt/blob/599394eb745c8eae7c10d64fc1792fe3fe7e8fea/plugins/codex-sci-ppt/skills/codex-sci-ppt/scripts/scene_support.py)、[Scene 渲染](https://github.com/yulinpeng2023-create/codex-sci-ppt/blob/599394eb745c8eae7c10d64fc1792fe3fe7e8fea/plugins/codex-sci-ppt/skills/codex-sci-ppt/scripts/render_scene.py)。
- Scientific PPT Drawing：[SVG 编译 `compile_svg`](https://github.com/SPYfighting/my-academic-skills/blob/a207937b44d72484ac6e5758cd9a80590f99479d/scientific-ppt-drawing/scripts/figurelib/svg.py)、[预览与 QA](https://github.com/SPYfighting/my-academic-skills/blob/a207937b44d72484ac6e5758cd9a80590f99479d/scientific-ppt-drawing/scripts/figurelib/preview.py)。

**不能直接搬入核心的行为：** 扁平化语义组、按遍历序号重新命名对象、把复合路径拆成互不相关的填充形状、将组透明度当成子路径透明度逐一相乘、把半透明重复路径直接去重。这些会影响孔洞、遮挡、稳定 ID 或外观。新引擎保留原生 SVG 的 compound path、fill-rule 和组级合成。

许可原文：[cell_su7 MIT](https://github.com/yrui-cmd/cell_su7/blob/209ced088b4fa5056b01dbd15e6fe3df100d2364/LICENSE)、[Fig2Slide Apache-2.0](https://github.com/Yutao-Yang/fig2slide/blob/0d1db3d4ab1407539afe5af7c2eabf2abf38d3a6/LICENSE)、[codex-sci-ppt MIT](https://github.com/yulinpeng2023-create/codex-sci-ppt/blob/599394eb745c8eae7c10d64fc1792fe3fe7e8fea/LICENSE)、[Scientific PPT Drawing MIT](https://github.com/SPYfighting/my-academic-skills/blob/a207937b44d72484ac6e5758cd9a80590f99479d/scientific-ppt-drawing/LICENSE) 及其 [NOTICE](https://github.com/SPYfighting/my-academic-skills/blob/a207937b44d72484ac6e5758cd9a80590f99479d/scientific-ppt-drawing/NOTICE.md)。

**许可范围：** 复制或实质改编 MIT/Apache 代码须保留对应版权、许可和适用 NOTICE；Apache 修改文件标记修改。代码许可不能覆盖商业 API 服务条款、参考图、字体、模板资产及模型权重。本轮未完成二进制依赖的完整 SBOM；发布包前按实际分发版本补齐。不因代码能看见就认为可以任意复制，也不因自行重写就删除实质派生代码的来源声明。

### 基础工具审计与轻量选型

| 工具／固定源码 SHA | 证据与边界 | 决策 |
|---|---|---|
| VTracer `2b32ca9c1e3e6e489e1b4358703dbec18adcc22b`；另核对 0.6.4 `725adf536483e4fa19b2f777bf09fcd7b5105aa8` | 当前 1.0 alpha 的 Pipeline 输出几何和颜色，Python 绑定返回 SVG 字符串，不能直接当作 Rust shapes 接口。透明处理有采样启发式，不保证半透明保真；颜色层不是科学语义层。MIT 可用，当前 manifest 另标 MIT OR Apache-2.0。 | 作为局部轮廓后端；原型比较稳定版和 alpha，选定后固定版本与参数。不得据此宣称自动恢复科学结构。 |
| resvg `deda5edb2d32e8810a3e6a13be289bed73097a62` | usvg→tiny-skia→PNG，无 GUI/浏览器/GPU；静态 SVG 有支持边界。MIT 或 Apache-2.0。 | 首选 PNG 渲染候选；固定字体与版本，先验证本引擎使用的 SVG 子集。 |
| SVG.js `4b8809b1554c44a84f8992cdbda60480336cc040` | MIT；Node 可配 svgdom，但 bbox/元素创建依赖 DOM，不是 PNG renderer。 | 本轮无前端编辑器需求，直接 XML 输出更轻；不引入 Node/DOM 作为必要依赖。 |
| Paper.js `0d1446a1bb040e3c87533b28bcc384d87663587b` | MIT；有路径布尔运算。headless SVG 需 jsdom，Canvas/PNG 再需 node-canvas 等；不是任意 SVG 无损往返工具。 | 不作为基础依赖；只有原型证明确需复杂布尔运算时再评估。 |
| PaddleOCR `dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf` | Apache-2.0；有 CPU/GPU、本地模型目录接口。其默认模型会漂移，必须显式选择版本；运行时还依赖 PaddlePaddle/PaddleX。核对的 PP-OCRv5 mobile det/rec 官方模型卡为 Apache-2.0。 | 可选 OCR 后端；原型明确模型、语言和 revision，不把仓库默认配置当锁定依赖。 |
| SAM3 `eef6839bcfc3458dd52ea80535dc6bd62aea86b1` | 实际 LICENSE 是自定义 SAM License，不能按包 classifier 的 MIT 处理；官方权重 gated/manual。README 先决条件含较新 Python/PyTorch/CUDA；存在局部 CPU 参数路径，但完整 CPU 可用性未验证。 | 非默认、独立安装的高级分割插件。基础流程不等待其权重许可或 GPU 配置；不承诺 CPU 完整运行。 |

关键证据：[VTracer Pipeline](https://github.com/visioncortex/vtracer/blob/2b32ca9c1e3e6e489e1b4358703dbec18adcc22b/crates/vtracer/src/pipeline.rs)、[VTracer Python 接口](https://github.com/visioncortex/vtracer/blob/2b32ca9c1e3e6e489e1b4358703dbec18adcc22b/crates/vtracer-py/src/lib.rs)、[resvg 无 GUI 示例](https://github.com/linebender/resvg/blob/deda5edb2d32e8810a3e6a13be289bed73097a62/crates/resvg/examples/minimal.rs)、[SVG.js Node 用法](https://svgjs.dev/docs/3.2/node-js/)、[Paper.js 布尔运算](https://github.com/paperjs/paper.js/blob/0d1446a1bb040e3c87533b28bcc384d87663587b/src/path/PathItem.Boolean.js)、[PaddleOCR 本地构造接口](https://github.com/PaddlePaddle/PaddleOCR/blob/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf/paddleocr/_pipelines/ocr.py)、[SAM3 实际许可](https://github.com/facebookresearch/sam3/blob/eef6839bcfc3458dd52ea80535dc6bd62aea86b1/LICENSE)。

模型证据分别锁定：PP-OCRv5 mobile det `0d63e78e2b680928f6b1747d76a08db6e645efb7`、rec `682f20538d8c086cb2128e5cfac775e6c4904e85`；SAM3 权重仓库 `3c879f39826c281e95690f02c7821c4de09afae7`。这些是仓库 revision，不是已下载权重的 SHA-256；安装包落地时补文件哈希和许可副本。

**推荐技术组合：Python 薄 CLI 与自有 Scene/SVG 核心 + resvg 渲染 + 按需图像处理/VTracer/OCR。** Python 标准库负责序列化与工程协议；Pillow 负责图像 I/O，NumPy/OpenCV-headless 只随图像处理能力安装，曲线路径解析按需复用 FontTools。无需同时维护 Node 和 Python 两套核心。具体发行版本通过第一阶段安装与渲染测试后锁定。

