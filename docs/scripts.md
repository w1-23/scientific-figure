# 脚本与运行说明（v0.9.0）

先完成 [依赖安装](dependencies-and-installation.md)。下文 `python` 指已安装本应用的解释器；未激活时使用 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`（Linux）。所有程序均须由宿主或用户显式执行，工程加载不会自动运行。

## 1. 独立环境安装：bootstrap.py

```bash
python -I scripts/bootstrap.py --venv .venv --dev
```

创建不继承系统包的新 venv，使用 pip 安装仓库应用及 construction；`--dev` 加 pytest。目标环境已存在时拒绝覆盖。可选：

| 参数 | 用途 |
|---|---|
| `--venv PATH` | 指定尚不存在的环境路径 |
| `--dev` | 安装测试依赖 |
| `--locked` | 在 Windows/Linux Python 3.12 使用已验证版本约束 |
| `--without-construction` | 不请求 skia-pathops；部分几何和示例因此不可用 |
| `--dry-run` | 只打印环境及安装命令，不创建目录、不安装 |

联网安装遵循正常 pip 设置；脚本不修改代理、系统 Python 或字体。

## 2. 完整公开工具链：verify_v09_workflow.py

```bash
python -I scripts/verify_v09_workflow.py --output work/workflow09
```

输入为脚本新生成的原创合成图，不依赖用户参考。输出：doctor、真实检索、四种测量、未决草稿与两阶段检查、规划上下文、完整工程、用户编辑、候选 diff/accept、局部 SVG/PNG、Bundle、双实例组合和 `verification.json`。同时移走原始源路径后重开工程，核对输入闭包。

脚本输出是机械与工程验证。视觉字段明确 `not_executed_by_automated_script`；需要宿主查看实际 preview 后另作绑定审核。

可选 `--require-wheel` 会要求应用从当前环境的 site-packages 加载，并逐字节比较仓库 `deliverables/scientific_figure_skill-0.9.0-py3-none-any.whl` 中的 74 个应用文件。先将 wheel 安装到独立环境，不使用 editable 安装，再执行：

```bash
python -I scripts/verify_v09_workflow.py --require-wheel --output work/wheel09
```

## 3. 自然语言示意图：draw_targeted_release_v09.py

```bash
python -I scripts/draw_targeted_release_v09.py --output work/targeted-release --revision 1
```

这是已将描述解释为明确绘图程序的原创例子，不是独立自然语言模型。宿主设计粒子、肿瘤目标、激光与释放的布局，使用公开 Builder、FigurePlan 与现有 arrow Recipe 生成可编辑图。

- `--output PATH`：新的输出工作目录。
- `--revision 0`：首次设计，保留了释放标签位置问题。
- `--revision 1`：实际看图后修正的标签／引线位置。

输出含草稿、候选比较、片段、plan/context、construction-check 和完整 project；主图在 `project/figure.svg`、`project/preview.png`。不会擅加制备、注射或具体实验参数。

## 4. 本机平台验证历史脚本

`verify_v09_platforms.py` 与 `verify_v09_linux.py` 保留 0.9 实际 Windows/WSL 验证的调度过程。它们含开发机器的发行版名、旧独立环境名或路径，并要求本地交付目录及既有依赖；**不是面向所有机器的一键安装器**。

- `verify_v09_platforms.py`：创建 Windows 独立应用环境，安装本地 wheel，调用完整验证，再调度 WSL；`--linux-only` 用于当时恢复 Linux 步骤。
- `verify_v09_linux.py`：在既有 Linux venv 中安装本地 wheel，将工作流输出放在原生 `/tmp`，成功后复制证据回工作区。

新机器请使用 bootstrap 或依赖文档，然后直接执行第 2 节公开验证；不必复刻本机目录名。

## 5. 原本地交付维护脚本：package_v09_delivery.py

该脚本仅对完整开发工作区制作源码、公开例子和私有参考图三个分包。它校验输入证据、源码与 wheel 一致性，逐成员生成 SHA-256，检查 ZIP CRC，并扫描公开成员是否混入私有参考图。

仓库没有完整私有工作目录，因此直接运行该维护脚本会因缺少输入而拒绝。普通使用不依赖它；从仓库构建应用 wheel 使用：

```bash
python -m pip wheel --no-deps --wheel-dir dist .
```

## 常见约束

- 输出工作目录需尚不存在；工具不覆盖旧工程。
- WSL 输出尽量在原生 Linux 文件系统，如 `~/figure-work/...`。
- `python -I` 避免当前目录等隐式 Python 导入影响，但不提供安全沙箱。
- 参考图和本地字体需满足各自使用条件；原始外部路径不是打开已闭包工程的隐式依赖。
- 成功执行、有效 Schema 或路径数量不代表科学正确或视觉通过。
