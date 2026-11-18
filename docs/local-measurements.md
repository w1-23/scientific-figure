# Engine 0.9 局部测量契约

当前宿主提出局部问题、读取原图和检查板、解释结果。工具只做 CPU 像素测量，不输出细胞膜身份、科学计数、OCR文字或当前Scene对象。未确定的孔洞/交织拓扑保留 unknown。

## 请求

```json
{
  "schema_version": "0.1",
  "source": {"path": "reference.png", "sha256": "实际原文件的64位小写SHA256"},
  "roi": [100, 120, 200, 150],
  "question": "此区域的边缘带是否随阈值改变？",
  "kind": "topology",
  "background": "#ffffff",
  "params": {"thresholds": [80, 120, 160], "foreground": "dark"},
  "budget": {"result_bytes": 250000}
}
```

除 budget 外字段都必填，未知字段拒绝。sha256 是真实文件字节摘要；源相对路径按请求文件父目录解析，Python dict 请求可显式传 base_directory。ROI 是 EXIF 定向源像素边界的整数 `[x,y,width,height]`，所有采样点和模板也用同一源坐标；输出 bbox 用 `[x0,y0,x1,y1]`。background 显式为 null 或 #RRGGBB/RGB数组，不能默默假定透明区为白色。

```bash
figure-cli evidence measure request.json --output work/local
```

输出目录必须不存在且父目录存在；原子发布 result.json 和 inspection_board.png。Python `measurements.measure` 返回自包含结果，`validate_measurement(result)` 离线校验并复制快照，不读取源或缓存。

## 四项操作

| kind | params | 结果及解释边界 |
|---|---|---|
| view | 可选 context_margin=16（0..1024）、max_side=128（16..512） | 指定ROI及上下文，源/局部/检查板映射；不自动找模块 |
| profile | points 与 lines 均必填；points 为源像素索引数组；lines 为 `{start:[x,y],end:[x,y],samples:32}` | 原始RGB、原始alpha、声明背景合成RGB及采样位置；采样数2..4096，最近邻floor(v+0.5)，不自动识别带宽含义 |
| topology | thresholds 为1..9个不同0..255整数；foreground 为dark/light；可选connectivity=8（4或8） | 灰度<=阈值为dark，>阈值为light；连通分量与不触ROI边界的背景孔洞候选。使用前景/背景对偶连通；阈值改变掩码即ambiguous，不能据白色证明透明或生物拓扑 |
| repeat | 同源template_roi、threshold(-1..1)；可选scale=1、nms_iou=.3、ambiguity_margin=.02 | 单尺度灰度TM_CCOEFF_NORMED与明确IoU去重；常量模板unsupported，多重或临界候选ambiguous，不称真实数量 |

profile 至少一个采样，允许 points 或 lines 之一为空。透明topology/repeat没有背景则unsupported；view/profile保留原始alpha。null背景的检查板只为显示合成在黑色上，原始alpha另存于结果。

## 来源、预算和缓存

结果含原始源SHA、ROI、question、明确参数和方法版本、坐标映射、状态、限制、测量/候选、内联PNG检查板、request_sha256、result_sha256和cache_key。校验绑定实际规范化请求与完整内容；摘要保证一致性，不能当第三方真实性签名。采用的结果可以直接内联进planning_context；临时源路径无需随工程存在。

cache_key绑定源字节、ROI、背景、参数、预算和库/算法版本，忽略位置路径和问题措辞。用户更改阈值或源会产生新键。这里提供内容寻址键，未实现隐式磁盘缓存读取；宿主可显式复用已校验结果。缓存和未采用预览不是工程依赖。

默认硬上限：输入32MiB/4000万像素；ROI100万像素；板200万像素；派生缓冲1600万像素；总采样4096；候选256；匹配 placements×template_pixels 2000万；去重工作65536；完整结果JSON1MiB。budget可以逐项降低，不能提高硬上限；字段见 measurements.LIMITS。超限明确失败，不静默截掉候选。大ROI、多阈值时应缩小局部，而非全图跑分析。

planning_context总预算也为1MiB，因此采用多项测量时建议压低每项result_bytes/max_side；不要把全部未采用测量塞入工程。源文件仍标external_not_packaged，实际保存参考PNG需单独声明reference_view摘要和映射。
