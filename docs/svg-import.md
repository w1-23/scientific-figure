# 受控 SVG 复用（0.7）

`import-svg` 将明确支持的矢量子集转换为 Scene0.2，再由同一 SVG/PNG 引擎保存完整工程。它不保留 raw SVG，也不提供任意 SVG 的无损导入保证。先确认来源、版本、许可及修改范围。

```bash
figure-cli import-svg input.svg --output work/imported --scale 1
```

输入必须明确数字或 px 的 width/height；viewBox 原样保留。缺尺寸的 SVG 不自动猜视口，应由宿主明确添加尺寸并记录为来源修改。输出目录须为新目录。

支持 svg/g/path/rect/ellipse/circle、有限 text/tspan、matrix/translate/scale/rotate、展示属性、继承样式和组透明度。支持默认 objectBoundingBox 的线性渐变、单个封闭矢量 clip，以及明确 userSpaceOnUse 的有限 alpha mask。描边要求 round cap/join。字体必须匹配本机明确选中的字体；不悄悄 fallback。多行 tspan 仅接受可精确映射的 x 和1.2倍行距。

逐元素属性、命名空间、资源引用类型与闭包严格校验。拒绝 CSS/style、事件、脚本、href/外链、DTD/entity、注释/处理指令、foreign namespace、嵌套SVG、use/symbol/filter/image 等特性；不能表达时失败，不静默丢弃。需要预处理时必须显式保留原文件、修改说明，并对新输入重新检查。

限制：1MiB XML、3000元素、深度80、256资源、合计300000路径字符、64变换及有限坐标。该子集仍不能代替宿主实际查看转换前后图像，特别是渐变、透明和文字。

实际许可复用见 `scripts/verify_svg_reuse.py`：Twemoji lemon v14.0.2，CC-BY-4.0，Twitter, Inc. and contributors。原文件只有 viewBox，实验显式添加144×144尺寸；几何和样式不改。原生与导入渲染逐像素一致，主体改色、Bundle和双实例组合通过。LICENSE、原SHA和归一化修改保存于输出。此组与从冻结栅格重画的柠檬完全分开，不计为重绘能力提高。

```bash
python -I scripts/verify_svg_reuse.py --workspace . --output work/svg-reuse
```
