# 植物细胞氮同化：约束包复核

这张图没有用约束包重画，而是把**已交付的 `redraw.svg`** 当作 Scene 0.3 导入，从 `reference.webp` 建多尺度边缘约束包，再逐个细胞器提问：你画的这部分，是否覆盖了参考图在你这个范围内的边界？全部原始数值见 [evidence-check.json](evidence-check.json)，复现命令：

```bash
python examples/plant-nitrogen-assimilation/check.py work/plantcheck --deliver examples/plant-nitrogen-assimilation
```

## 三种 scope 的区别

| scope | 参考边的取舍 | 适用对象 |
|---|---|---|
| `region`（默认） | 整个矩形范围内所有参考边 | 小对象或独立分区 |
| `object` | 对象实际绘制足迹（alpha）内的参考边 | 细胞器：把内部结构（嵴、基粒、膜带）计入，排除邻接结构 |
| `outline` | 轮廓带（足迹减去内缩 1px 后外扩）内的参考边 | 含大面积填充、内部另有对象的组，如细胞壁／细胞质整组 |

范围外包一层 `allowed_deviation + 1` 像素的容差带。`object` 让"线粒体内部的嵴是否还在""内质网的多层折叠是否还在"真正进入 recall，而不是只比外轮廓；`outline` 避免细胞壁因为把整个细胞质算进足迹而与别的细胞器重复计分。

## 结果

11 个绑定：**10 passed，1 needs_review，0 failed**；图级结论 `needs_review`（有 1 项低于阈值）。

| 绑定对象 | scope | 状态 | recall | 平均偏移 (px) |
|---|---|---|---|---|
| cell-envelope | outline | passed | 1.000 | 1.28 |
| vacuole | outline | passed | 0.970 | 1.54 |
| chloroplast-top-left | object | **needs_review** | 0.680 | 3.30 |
| chloroplast-top-right | object | passed | 0.708 | 2.39 |
| chloroplast-bottom-left | object | passed | 0.739 | 2.22 |
| mitochondrion-left | object | passed | 0.871 | 1.49 |
| mitochondrion-upper-right | object | passed | 0.814 | 1.84 |
| mitochondrion-right | object | passed | 0.816 | 1.73 |
| nucleus | object | passed | 0.680 | 2.70 |
| rough-endoplasmic-reticulum | object | passed | 0.852 | 2.08 |
| golgi | object | passed | 0.845 | 2.22 |

三个线粒体、内质网和高尔基体都是 `object` scope 下的通过，即它们的**内部**结构（嵴、多层膜折叠、堆叠层）也在被比较，不只是外轮廓对得上。

## 已修正：左上叶绿体的比例与姿态

首次复核时这一项是 recall 0.549、平均偏移 4.05px：重绘的叶绿体比参考长约 15%、厚约 40%，倾角偏了约 4°，基粒整体偏内。

修正内容（都在 `redraw.svg` 的该组内，未改其它对象）：

| 项 | 原 | 现 |
|---|---|---|
| 组变换 | `matrix(0.85303502 -0.4925 0.4825 0.83571451 217 190)` | `matrix(0.818914 -0.399411 0.391301 0.802286 215.153595 185.97663)` |
| 四个基粒堆 | 按原局部坐标 | 按参考中心各平移 0.6–13.8px |

变换由与参考边的套准搜索得到（缩放 0.925、旋转 −4°、平移 −2/−4），不是试阈值；基粒平移取参考暗色分量与重绘基粒质心的差。结果：

| | 修正前 | 修正后 |
|---|---|---|
| recall | 0.549 | **0.680** |
| 平均偏移 | 4.05px | **3.30px** |
| precision | — | 0.88 |

[修正前对照](compare-chloroplast-top-left-before.png) · [修正后对照](compare-chloroplast-top-left.png) · [参考边（红）与重绘边（蓝）叠加](compare-chloroplast-top-left-edges.png)

**仍在待复核**：3.30px 仍高于 3.0px 容差，但已经不是姿态问题——继续做全局缩放/旋转只会变差（套准搜索证实当前姿态是局部最优），残差集中在基粒内部的层间距：参考每堆可见 4–5 层薄片，重绘的盘更厚、层数更少。要消掉这 0.3px 需要重画基粒堆的层叠，不属于本次姿态修正。其余 10 个对象不受影响。

## 读了什么、没读什么

- **读了**：参考图多尺度边缘（medium 尺度）与该对象笔迹的一致程度、平均/最大偏移、参考边与绘制边各自的存在性。
- **没读**：语义。"这是叶绿体""这层是液泡膜"由宿主判断；typed edge class 是启发式分桶，不是对象识别。
- **高分不等于科学正确**：recall 高只说明参考中的可见边被覆盖；不能证明被省略的结构不重要，也不能证明画对的是同一个生物学对象。
- **object scope 的盲区**：足迹由绘制结果决定，若某对象画得过大，参考边大多仍落在足迹内；此时靠偏移量和人工对照发现问题。
- **液泡为何不用 `negative_space`**：该绑定问的是"区域内封闭空腔是否被对象分开"，针对背景孔洞；液泡本身是一个实体细胞器，用它只会持续返回 needs_review。液泡用 `boundary` + `outline`。
- **事后复核的边界**：本检查验证的是既有 SVG 与参考图的一致性，**不**证明这张图是用新的语义规划流程从头重画出来的。
- 参考图权利说明同 [README](../../README.md)：仅作对照，不在代码许可范围内。
