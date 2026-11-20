# vNext M0 基线记录（2026-11-20）

本文件只记录事实，不改变任何行为。M1/M2 完成后必须用同一份基线复验。

## 提交与版本

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/w1-23/scientific-figure` |
| main | `3650083c2d75b4a093827dd595ad46b9788b8e1e`（本地与远程一致） |
| Engine | 0.9.0（`scientific-figure-skill`） |
| Python | 3.12.16 |

vNext 技术方案文档把基线写成 `8e9177c`。该提交是 `3650083` 的祖先，`3650083` 才是记录时真实的 main；本计划以 `3650083` 为基线。

## 回归基线

```
python -m pytest -q
1056 passed, 5 skipped, 10 subtests passed in 80.61s
```

5 项 skip 是 Windows 符号链接权限限制，不是失败。

## 运行时事实

- 渲染：resvg-py 0.5.0，CPU。
- 可选几何后端：skia-pathops 0.9.2。
- 字体：Arial（`C:\Windows\Fonts\arial.ttf`，SHA-256 `edfbd22a…a634404`）。
- `figure-cli doctor`：`success`；`runtime_preflight.status`：`ok`；`adapter_status`：`adapter_not_available`。
- 依赖：Pillow 12.3.0、numpy 2.5.5、opencv-python-headless 4.14.0.94、fonttools 4.68.0、jsonschema 4.26.0、resvg-py 0.5.0。

## 导出基线

本地示例 `work/demo-citrus/project`（501 对象）：

- `figure.svg` SHA-256：`91efb13e36148d92993fd3e86030733feed99f1db6876a43c0330896b3fefd80`
- `variants/figure.final.svg` 与工程 `figure.svg` 字节一致（同一摘要）。
- `variants/figure.graphics.svg` 存在（无字版本）。

## 兼容红线

1. 回归保持全绿，测试数不减少。
2. 既有双 SVG 导出保持**字节一致**。
3. 新 QA 不进入 `write_project`，不写入工程的 `qa.json`；否则既有 Bundle 校验与基线全部失效。

机器可读副本：`verification/vnext-m0/baseline.json`。
