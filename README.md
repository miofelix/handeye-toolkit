# Handeye Toolkit

Handeye Toolkit 是独立的 Piper 手眼标定小工具，支持 DaBai、Intel RealSense D435 和带已知内参的普通 RGB 相机，提供标定采集、任务恢复、质量检查、本地报告和可离线复算的脱敏交付包。

支持两种安装方式：

- `eye-to-hand`：相机固定在基座侧，结果为 `base <- camera`；
- `eye-in-hand`：相机固定在法兰侧，结果为 `flange <- camera`。

所有刚体变换都使用 `RigidTransform(parent_frame, child_frame)`，把 `child_frame` 坐标映射到 `parent_frame`。平移单位为米，角度指标单位为度。

## 安全边界

工具对 Piper 只执行连接、状态读取、法兰位姿读取和断开。不会发送运动、使能、失能、复位、急停或夹爪控制命令。

采集时必须由现场人员确认标定板安装牢固，并手动移动机械臂。运行前应核对 CAN 通道。

## 安装

在运行工具的 Python 环境中安装项目和依赖：

```bash
python -m pip install '.[runtime,piper]'
```

D435 需要额外安装 RealSense Python SDK：

```bash
python -m pip install '.[runtime,piper,realsense]'
```

Piper 只读反馈使用 `pyAgxArm`：

```bash
python -m pip install \
  'git+https://github.com/agilexrobotics/pyAgxArm.git@8cd90f9106219a156c3c0d7e58ee36d838a89baf'
```

DaBai DC1 使用 `pyorbbecsdk v1.3.2`。Ubuntu 上可按以下方式构建并安装：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake python3-dev python3-pip python3-venv
git clone --depth 1 --branch v1.3.2 \
  https://github.com/orbbec/pyorbbecsdk.git
cd pyorbbecsdk
python -m pip install 'pybind11-global==2.11.0' wheel
cmake -S . -B build -Dpybind11_DIR="$(pybind11-config --cmakedir)"
cmake --build build --parallel
cmake --install build
python setup.py bdist_wheel
python -m pip install dist/*.whl
```

首次连接前，需要按上游说明安装并重新加载 udev 规则。仓库不复制或打包硬件 SDK；许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 命令行

直接运行 `handeye` 会启动中文向导：

```text
1) 新建标定
2) 恢复任务
3) 配置管理
4) 校验交付包
q) 退出
```

也可以直接使用命令和参数：

```text
handeye [--config PATH | --resume RUN_DIR]
        [--ui auto|cli|gui] [--can-channel CAN_CHANNEL]
        [--output-root PATH] [--artifact-output PATH]
handeye setup [--output PATH] [--force]
handeye resume RUN_DIR [--ui auto|cli|gui] [--artifact-output PATH]
handeye verify ARTIFACT.zip [--recompute] [--json]
```

`--can-channel` 只覆盖本次新建任务，不回写 YAML。恢复任务以任务目录中的 `session.json` 为准，不重新读取产品配置。

有桌面环境时可以使用图形界面；`--ui cli` 和 `--ui gui` 可强制选择，`--ui auto` 自动判断。CLI 与 GUI 共用同一个 `CalibrationController` 和 `CalibrationRun` 状态机。

## 配置

生成占位配置：

```bash
handeye setup --output configs/handeye.yaml
```

配置合同如下：

```yaml
mode: eye-to-hand
policy: standard
camera:
  serial_number: "<camera-serial>"
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

配置使用严格字段校验，未知字段、非有限数值和无效尺寸都会被拒绝。`standard` 是内置质量策略，标定板尺寸必须与实物或证书一致。

原有 `camera.serial_number` 是 DaBai 的兼容简写。D435 和普通 RGB 相机使用通用相机描述，字段为 `camera.adapter`、`camera.source_id` 和 `camera.settings`：

- `realsense-d435`：`source_id` 是设备序列号；`settings` 可设置 `width`、`height`、`fps`、`timeout_ms` 和 `warmup_frames`，彩色流内参由设备 SDK 读取；
- `opencv-rgb`：`source_id` 是非负设备索引；`settings` 必须包含 `width`、`height` 和 `intrinsics`，还可设置 `fps`、`warmup_frames`、`backend` 和 `fourcc`；
- `opencv-rgb.settings.intrinsics` 固定包含 `fx`、`fy`、`cx`、`cy`、`distortion_model` 和 `distortion_coefficients`。采集分辨率与内参标定分辨率不一致时会拒绝样本。

## 任务与交付包

任务目录保存本地采集证据、`session.json`、`result.json` 和 `report.local.html`。样本图像、设备身份和现场路径只保存在本地受控环境中。

质量检查通过后可以导出 ZIP。交付包固定包含：

```text
manifest.json
result.json
evidence.json
report.html
```

交付包不包含原始图像、相机序列号、CAN 通道、本地路径或硬件 SDK 元数据。加载时会检查成员白名单、路径安全、压缩与大小上限、媒体类型、SHA-256 和跨文件语义一致性。

校验或离线复算交付包：

```bash
handeye verify handeye-artifact_run_placeholder.zip
handeye verify handeye-artifact_run_placeholder.zip --recompute
handeye verify handeye-artifact_run_placeholder.zip --json
```

## Python API

公共 API 按职责组织：

- `handeye_toolkit.domain`：不可变值对象、坐标变换、计划、任务和结果合同；
- `handeye_toolkit.ports`：相机、只读法兰源、目标检测器、求解器、仓储、报告和导出协议；
- `handeye_toolkit.application`：采集协调器和 `CalibrationRun` 状态机；
- `handeye_toolkit.composition`：组件注册表和调用方中立的采集装置组合工厂；
- `handeye_toolkit.artifacts`：交付包导出、严格校验和离线复算；
- `handeye_toolkit.app`：Piper 产品配置及组合入口；
- `handeye_toolkit.adapters`：DaBai、D435、普通 RGB、Piper 只读反馈和文件系统适配器。

`domain`、`ports`、`application` 和基础 `composition` API 不加载 OpenCV、SciPy 或硬件 SDK。调用方中立 API 不内置外部业务配置路径或写回逻辑。

常用入口包括 `CalibrationPlan`、`CameraFrame`、`FlangePose`、`TargetDetection`、`CalibrationRun`、`CalibrationResult`、`CalibrationArtifactExporter`、`load_verified_artifact` 和 `recompute_verified_artifact`。

组件扩展合同如下：

- 相机实现 `Camera`，输出带内参和单调时钟采集区间的 `CameraFrame`；
- 机械臂实现 `ReadOnlyFlangeSource`，只输出 `base <- flange` 的 `FlangePose`，不得暴露控制动作；
- 标定板实现 `TargetDetector`，输出 `camera <- target` 的 `TargetDetection`、质量指标和身份凭据；
- 每类实现以唯一适配器 ID 注册到 `ComponentRegistry`；`ComponentRigFactory` 根据 `AcquisitionDescriptor` 独立解析并组装三个组件；
- 标定计划中的目标适配器与参数必须和采集描述一致，保证任务恢复、制品导出和离线复算使用同一份固定合同。

本发行版通过 `create_builtin_registry()` 注册 `dabai`、`realsense-d435`、`opencv-rgb`、`piper-readonly` 和 `charuco`。新增组件无需修改 `CalibrationRun`、采集协调器或求解器。

## 开发检查

```bash
python -m ruff check src tests
python -m mypy
python -m pytest
python -m build
git diff --check
python scripts/check_sensitive.py
```

## 许可证

项目代码采用 MIT License；硬件 SDK 和可选依赖遵循各自许可证。
