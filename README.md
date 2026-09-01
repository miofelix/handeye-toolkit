# Handeye Toolkit

Handeye Toolkit 是独立的多相机/Piper 手眼标定工具：读取相机图像和 Piper 法兰反馈，完成采集、恢复、质量检查、求解、本地审阅和脱敏交付。内置支持 Intel RealSense D435、带已知内参的普通 RGB 相机、DaBai DC1，以及 ChArUco 标定板。

> **安全边界**：Piper 适配器只读状态和法兰反馈，不会发送运动、使能、失能、复位、急停或夹爪控制命令。机械臂移动、CAN 映射核对和标定板安装必须由现场人员完成。

## 一览

| 项目 | 当前合同 |
| --- | --- |
| 环境 | Linux、Python 3.10–3.14、SocketCAN |
| 机械臂 | Piper、Piper H、Piper L、Piper X |
| 相机 | D435、普通 RGB、DaBai DC1 |
| 标定目标 | OpenCV 4.11 默认布局的 ChArUco 标定板 |
| 界面 | 终端向导、图形界面 |
| 输出 | 本地证据与报告、脱敏 ZIP、离线复算 |

两种模式的安装和结果方向如下：

| 模式 | 相机 | 标定板 | 结果 |
| --- | --- | --- | --- |
| `eye-to-hand` | 固定在基座侧 | 与法兰刚性固定 | `base <- camera` |
| `eye-in-hand` | 与法兰刚性固定 | 固定在基座侧 | `flange <- camera` |

`parent <- child` 表示把 `child` 坐标映射到 `parent` 坐标。所有变换均为 4×4 齐次矩阵；平移单位为米，质量指标中的角度单位为度。

## 安装

在仓库根目录创建虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

按相机安装本项目：

```bash
# D435
python -m pip install '.[runtime,piper,realsense]'

# 普通 RGB 或 DaBai
python -m pip install '.[runtime,piper]'
```

Piper SDK 使用固定版本：

```bash
python -m pip install 'git+https://github.com/agilexrobotics/pyAgxArm.git@8cd90f9106219a156c3c0d7e58ee36d838a89baf'
```

DaBai 还需按照上游说明安装与环境匹配的 `orbbec/pyorbbecsdk` v1.3.2。D435 内参由 RealSense SDK 读取，DaBai 内参由 Orbbec SDK 读取；普通 RGB 相机必须提供与固定采集分辨率对应的完整内参。硬件 SDK 不随仓库分发，许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

工具要求现场已经准备好 SocketCAN 接口，不会创建、启用或重配 CAN。

## 快速开始

直接启动统一向导：

```bash
handeye
```

也可以生成配置后显式启动：

```bash
handeye setup --output configs/handeye.yaml
handeye --config configs/handeye.yaml --ui cli --output-root runs
```

`setup` 默认拒绝覆盖已有文件；需要覆盖时使用 `--force`。新任务常用参数：

| 参数 | 含义 |
| --- | --- |
| `--config PATH` | 使用指定 YAML；省略时进入配置向导 |
| `--ui auto\|cli\|gui` | 选择界面；无桌面环境不能强制使用 GUI |
| `--can-channel CHANNEL` | 只覆盖本次新任务，不写回 YAML |
| `--output-root PATH` | 任务根目录，默认 `runs` |
| `--artifact-output PATH` | 交付包路径，默认位于任务目录 |

采集主线只有四步：现场人员手动摆姿态并保持静止，程序检查稳定性和覆盖，确认合格候选及标定板身份，达到标定/验证要求后求解并导出。

## 配置合同

配置必须且只能包含以下顶层结构。尖括号内容使用前必须替换：

```yaml
mode: eye-to-hand
policy: standard
camera:
  adapter: "<camera-adapter>"
  source_id: "<camera-source>"
  settings: {}
piper:
  model: piper
  firmware_profile: v188
  can_channel: "<can-channel>"
target:
  squares: [12, 9]
  square_size_mm: 15.0
  marker_size_mm: 11.25
  dictionary: DICT_5X5_1000
```

- `mode`：`eye-to-hand` 或 `eye-in-hand`。
- `policy`：当前只能是 `standard`。
- `piper.model`：`piper`、`piper_h`、`piper_l` 或 `piper_x`。
- `piper.firmware_profile`：`default`、`v183`、`v188` 或 `v189`。
- `piper.can_channel`：非空 SocketCAN 通道，由现场核对。

### 相机设置

| `adapter` | `source_id` | `settings` 合同 |
| --- | --- | --- |
| `realsense-d435` | 设备序列号 | 可选 `width`、`height`、`fps`、`timeout_ms`、`warmup_frames`；默认 `1280×720@30`、5000 ms、30 帧 |
| `opencv-rgb` | 规范的非负设备索引 | 必填 `width`、`height`、`intrinsics`；可选 `fps`、`warmup_frames`、`backend`、`fourcc` |
| `dabai` | 设备序列号 | `{}` 使用默认彩色流；也可成组填写 `width`、`height`、`fps` 和可选 `color_format`；超时/预热默认 5000 ms/30 帧 |

普通 RGB 的 `intrinsics` 必须完整：

```yaml
intrinsics:
  fx: <fx>
  fy: <fy>
  cx: <cx>
  cy: <cy>
  distortion_model: <distortion-model-or-null>
  distortion_coefficients: [<coefficient>, <coefficient>]
```

`fx`、`fy` 必须为正数，其余数值必须有限；畸变参数可为空数组。默认 `fps` 为 30、`warmup_frames` 为 10、`backend` 为 `any`。后端还支持 `v4l2`、`avfoundation`、`dshow`、`msmf`、`gstreamer`；`fourcc` 必须是 4 个 ASCII 字符。实际帧尺寸必须严格匹配内参标定尺寸。

DaBai 强制只使用彩色流和 SDK 内参；直连不能与 ROS `astra_camera` 或其他占用同一 USB 设备的程序并行。

配置采用严格字段和数值校验。旧版仅含 `camera.serial_number` 的 DaBai 简写仍可读取，但新配置和写出结果只使用统一的 `adapter`、`source_id`、`settings` 合同。

## ChArUco 标定板合同

当前只支持与 OpenCV 4.11 `cv2.aruco.CharucoBoard` 默认布局兼容的板，`legacyPattern=false`，Marker ID 固定从 `0` 连续编号。

| 字段 | 含义 |
| --- | --- |
| `squares: [x, y]` | 沿板坐标 X、Y 方向的方格数，不是内角点数 |
| `square_size_mm` | 方格实际边长，必须为正数 |
| `marker_size_mm` | Marker 实际边长，必须小于方格边长 |
| `dictionary` | OpenCV 预定义 ArUco 字典名称 |

标定区域尺寸为 `x × square_size_mm` 和 `y × square_size_mm`，内角点数为 `(x-1) × (y-1)`，Marker 数为 `floor(x × y / 2)`。模板参数对应 180 mm × 135 mm、88 个内角点、54 个 Marker，ID 为 `0..53`；这些只是通用模板值，必须与实物或证书一致。

`target` 坐标遵循 OpenCV 对象点约定：原点位于标定区域外角，X/Y 轴沿方格排列方向，板面为 `Z=0`，Z 轴遵循右手定则；检测结果为 `camera <- target`。

使用时必须保证板平整、尺寸准确、无明显反光并刚性安装。打印时关闭页面适配，按 100% 输出并复测；不要裁切标定区域，也不要在同一任务中换板、改尺寸、改字典、改 ID 排布或改变安装关系。

## 采集与质量

一次任务中，相机分辨率、相机安装和标定板安装必须保持不变。机械臂由现场人员手动移动，采集时保持静止，并让位置、距离、方位和倾角充分变化。

内置 `standard` 策略的主要门禁：

| 阶段 | 要求 |
| --- | --- |
| 样本 | 20 个标定样本、5 个验证样本；每个候选检查 10 帧 |
| 静止 | 采集窗口法兰漂移不超过 0.5 mm / 0.2°；单帧采集不超过 0.2 s |
| 去重 | 同时小于 5 mm 和 3° 的姿态视为重复 |
| 覆盖 | 位置跨度至少 0.1 m、旋转跨度至少 30°；至少两个夹角不小于 20° 的旋转轴 |
| 检测 | 至少 12 个角点、板面积至少 5%、重投影 RMS 不超过 1 px、清晰度至少 30 |
| 结果 | 验证平移 RMS 不超过 5 mm，验证旋转 RMS 不超过 1° |

首次保存样本必须确认标定板身份。后续候选必须保持相同检测配置和 ID 范围；相同布局的两块实物板无法仅凭参数指纹区分，因此现场仍不得中途换板。

质量未通过时不会导出交付包，但任务和本地证据会保留，可恢复后继续采集并重新求解。

## 恢复、结果与交付

退出不会删除已保存证据。使用任务目录或 `session.json` 恢复：

```bash
handeye resume runs/run_placeholder --ui cli
# 或
handeye --resume runs/run_placeholder --ui cli
```

恢复只使用任务快照，不重新读取原 YAML，也不接受 CAN 通道覆盖。同一任务同一时间只能由一个进程打开。

任务目录保存 `session.json`、样本的 `color.png`/`overlay.png`/`observation.json`、求解后的 `result.json` 和 `report.local.html`。本地报告嵌入样本图像，应视为现场敏感数据。

读取 `result.json` 时必须同时检查：

- `transform.parent_frame`、`child_frame` 和 4×4 `matrix`；
- `quality.passed`、失败原因、验证 RMS、覆盖和不确定性；
- 平移单位为米，不能只复制矩阵而忽略坐标方向和单位。

质量通过后，交付 ZIP 固定且只能包含：

```text
manifest.json
result.json
evidence.json
report.html
```

交付包保留离线复算所需的数值位姿，但不包含图像、相机身份、CAN 通道、现场路径或硬件 SDK 元数据。

```bash
handeye verify handeye-artifact_run_placeholder.zip
handeye verify handeye-artifact_run_placeholder.zip --recompute
handeye verify handeye-artifact_run_placeholder.zip --json
```

普通校验不连接硬件或加载硬件 SDK；`--recompute` 需要 `runtime` 算法依赖。

## Python API 合同

- `handeye_toolkit.domain`：不可变值对象、变换、计划、任务和结果；
- `handeye_toolkit.ports`：相机、只读法兰源、目标检测器、求解、仓储、报告和导出协议；
- `handeye_toolkit.application`：采集协调器和 `CalibrationRun` 状态机；
- `handeye_toolkit.composition`：组件注册表与中立组合工厂；
- `handeye_toolkit.artifacts`：固定交付包的导出、校验和离线复算；
- `handeye_toolkit.app`：产品配置、策略和启动组合。

核心类型包括 `CalibrationPlan`、`CameraFrame`、`FlangePose`、`TargetDetection`、`CalibrationRun`、`CalibrationResult`、`CalibrationArtifactExporter`、`ComponentDescriptor` 和 `ComponentRegistry`。

`RigidTransform(parent_frame, child_frame, matrix)` 把 `child_frame` 映射到 `parent_frame`。领域 API 不导入 OpenCV、SciPy 或硬件 SDK；调用方负责把结果适配到自身系统，本工具不内置外部业务配置 schema、路径或写回逻辑。内置注册表提供 `realsense-d435`、`opencv-rgb`、`dabai`、`piper-readonly` 和 `charuco`。

## 开发验证

```bash
python -m pytest
python -m build
git diff --check
python scripts/check_sensitive.py
python -m ruff check src tests
python -m mypy
```

本项目使用 MIT License。硬件 SDK 许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
