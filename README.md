# Handeye Toolkit

Handeye Toolkit 是面向多种相机的独立 Piper 手眼标定工具。相机通过统一适配器合同接入；内置支持 Intel RealSense D435、带已知内参的普通 RGB 相机和 DaBai DC1。工具提供标定采集、任务恢复、质量检查、本地报告和可离线复算的脱敏交付包。

支持两种安装方式：

- `eye-to-hand`：相机固定在基座侧，结果为 `base <- camera`；
- `eye-in-hand`：相机固定在法兰侧，结果为 `flange <- camera`。

所有刚体变换都使用 `RigidTransform(parent_frame, child_frame)`，把 `child_frame` 坐标映射到 `parent_frame`。平移单位为米，角度指标单位为度。

## 安全边界

工具只读取相机图像以及 Piper 状态和法兰反馈。Piper 适配器不会发送运动、使能、失能、复位、急停或夹爪控制命令。

采集时必须由现场人员确认标定板安装牢固并手动移动机械臂。运行前应核对 CAN 通道。

## 内置相机适配器

所有相机在产品配置中使用相同的 `adapter`、`source_id` 和 `settings` 合同：

| 相机 | `adapter` | `source_id` | 内参与采集设置 |
| --- | --- | --- | --- |
| Intel RealSense D435 | `realsense-d435` | 设备序列号 | 彩色内参由 RealSense SDK 读取；可设置宽、高、帧率、超时和预热帧数 |
| 普通 RGB 相机 | `opencv-rgb` | 非负 OpenCV 设备索引 | 必须提供与固定采集分辨率对应的完整内参；可设置帧率、后端、FourCC 和预热帧数 |
| DaBai DC1 | `dabai` | 设备序列号 | 彩色内参由 Orbbec SDK 读取；可使用默认彩色流或适配器支持的流设置 |

普通 RGB 相机的采集分辨率与内参标定分辨率不一致时会拒绝采集。D435 和 DaBai 的设备选择及专属 SDK 规则只存在于各自适配器内，不进入产品层合同。

## 安装

在运行工具的 Python 环境中安装项目、运行时依赖和 Piper 只读反馈依赖：

```bash
python -m pip install '.[runtime,piper]'
python -m pip install \
  'git+https://github.com/agilexrobotics/pyAgxArm.git@8cd90f9106219a156c3c0d7e58ee36d838a89baf'
```

根据相机适配器安装对应 SDK：

- `realsense-d435`：使用 `python -m pip install '.[runtime,piper,realsense]'` 安装声明的 RealSense Python 依赖；
- `opencv-rgb`：OpenCV 已包含在 `runtime` 可选依赖中；
- `dabai`：按上游说明构建并安装 `pyorbbecsdk v1.3.2`，并安装对应 udev 规则。

仓库不复制或打包硬件 SDK；许可证和版本信息见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

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
handeye [--config PATH | --resume RUN_DIR] [--ui auto|cli|gui]
        [--can-channel CAN_CHANNEL] [--output-root PATH] [--artifact-output PATH]
handeye setup [--output PATH] [--force]
handeye resume RUN_DIR [--ui auto|cli|gui] [--artifact-output PATH]
handeye verify ARTIFACT.zip [--recompute] [--json]
```

`--can-channel` 只覆盖本次新建任务，不回写 YAML。恢复任务以任务目录中的 `session.json` 为准，不重新读取产品配置。

有桌面环境时可以使用图形界面；`--ui cli` 和 `--ui gui` 可强制选择，`--ui auto` 自动判断。CLI 与 GUI 共用同一个 `CalibrationController` 和 `CalibrationRun` 状态机。

## 配置合同

生成不带设备身份的占位配置：

```bash
handeye setup --output configs/handeye.yaml
```

相机配置统一使用以下结构：

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

占位配置中的 `camera.adapter`、`camera.source_id`、`camera.settings` 和 CAN 通道必须按现场设备填写。配置使用严格字段校验，未知字段、非有限数值和无效尺寸都会被拒绝。`standard` 是内置质量策略，标定板尺寸必须与实物或证书一致。

各相机的 `settings` 合同如下：

- `realsense-d435`：可包含 `width`、`height`、`fps`、`timeout_ms` 和 `warmup_frames`；
- `opencv-rgb`：必须包含 `width`、`height` 和 `intrinsics`，可包含 `fps`、`warmup_frames`、`backend` 和 `fourcc`；
- `dabai`：设置为空时使用 SDK 默认彩色流，也可填写适配器公开支持的流参数。

`opencv-rgb.settings.intrinsics` 固定包含 `fx`、`fy`、`cx`、`cy`、`distortion_model` 和 `distortion_coefficients`。

旧版 DaBai 配置的 `camera.serial_number` 简写仍可读取，但加载后会规范化为统一相机合同；新配置和写出结果不再使用该简写。

## 任务与交付包

任务目录保存本地采集证据、`session.json`、`result.json` 和 `report.local.html`。样本图像、设备身份和现场路径只保存在本地受控环境中。

质量检查通过后可以导出 ZIP。交付包固定包含：

```text
manifest.json
result.json
evidence.json
report.html
```

交付包不包含原始图像、相机序列号、CAN 通道或硬件 SDK 元数据。加载时会检查成员白名单、路径安全、压缩与大小上限、媒体类型、SHA-256 和跨文件引用。

```bash
handeye verify handeye-artifact_run_placeholder.zip
handeye verify handeye-artifact_run_placeholder.zip --recompute
handeye verify handeye-artifact_run_placeholder.zip --json
```

## Python API

公共模块职责：

- `handeye_toolkit.domain`：不可变值对象、坐标变换、计划、任务和结果合同；
- `handeye_toolkit.ports`：相机、只读法兰源、目标检测器、求解器、仓储、报告和导出协议；
- `handeye_toolkit.application`：采集协调器和 `CalibrationRun` 状态机；
- `handeye_toolkit.composition`：组件注册表和调用方中立的采集装置组合工厂；
- `handeye_toolkit.artifacts`：交付包导出、严格校验和离线复算；
- `handeye_toolkit.app`：统一产品配置、质量策略和启动组合；
- `handeye_toolkit.adapters`：内置相机、Piper 只读反馈和文件系统适配器。

领域 API 不导入 OpenCV、SciPy 或硬件 SDK。调用方中立 API 以 `CalibrationPlan`、`CameraFrame`、`FlangePose`、`TargetDetection`、`CalibrationRun`、`CalibrationResult`、`CalibrationArtifactExporter` 和 `load_verified_artifact` 为主。

扩展相机时，实现 `Camera` 端口，并提供接收 `ComponentDescriptor` 的适配器工厂；随后将工厂注册到 `ComponentRegistry`。产品层、采集协调器和求解器只依赖统一描述及端口，不依赖具体相机型号。

本发行版通过 `create_builtin_registry()` 注册 `realsense-d435`、`opencv-rgb`、`dabai`、`piper-readonly` 和 `charuco`。新增组件无需修改 `CalibrationRun`、采集协调器或求解器。

## 开发验证

```bash
python -m pytest
python -m ruff check src tests
python -m mypy
python -m build
git diff --check
python scripts/check_sensitive.py
```

## 许可证

项目代码使用 MIT 许可证。第三方硬件 SDK 的许可证和分发条件独立适用。
