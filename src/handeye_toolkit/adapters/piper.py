"""只读 Piper 反馈适配器；本模块不创建末端执行器，也不发送控制命令。"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

import numpy as np

from ..domain import CaptureStamp, ComponentDescriptor, FlangePose, RigidTransform


def _message(value: Any) -> Any:
    return getattr(value, "msg", value)


def _finite_timestamp(value: Any) -> float:
    timestamp = getattr(value, "timestamp", None)
    if timestamp is None:
        raise RuntimeError("Piper 法兰反馈缺少有效 timestamp")
    try:
        result = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Piper 法兰反馈缺少有效 timestamp") from exc
    if not math.isfinite(result):
        raise RuntimeError("Piper 法兰反馈 timestamp 非有限值")
    return result


def _pose_vector(value: Any) -> tuple[float, float, float, float, float, float]:
    raw = _message(value)
    for attribute in ("pose", "position", "data"):
        candidate = getattr(raw, attribute, None)
        if candidate is not None:
            raw = candidate
            break
    try:
        vector = np.asarray(raw, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Piper 法兰反馈无法解析为位姿") from exc
    if vector.size != 6 or not np.isfinite(vector).all():
        raise RuntimeError("Piper 法兰反馈必须是 6 个有限的米/弧度数值")
    return tuple(float(item) for item in vector)  # type: ignore[return-value]


def _pose_zyx_to_transform(pose: tuple[float, float, float, float, float, float]) -> np.ndarray:
    x, y, z, roll, pitch, yaw = pose
    sr, cr = math.sin(roll), math.cos(roll)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = np.asarray(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ],
        dtype=np.float64,
    )
    transform[:3, 3] = (x, y, z)
    return transform


class PiperFlangeSource:
    """将 pyAgxArm 收窄为连接、状态、法兰反馈和断开四个公开操作。"""

    def __init__(self, config: Mapping[str, Any], *, sdk_module: Any = None) -> None:
        self._config = dict(config)
        self._config["allow_robot_control"] = False
        self._config["can_mapping_verified"] = True
        self._sdk = sdk_module
        self._robot = None

    def open(self) -> None:
        if self._robot is not None:
            return
        if self._config.get("allow_robot_control") is not False:
            raise ValueError("手眼标定要求 allow_robot_control 保持 false")
        if self._config.get("can_mapping_verified") is not True:
            raise ValueError("robot.can_mapping_verified 尚未确认")
        if not str(self._config.get("can_channel", "")).strip():
            raise ValueError("robot.can_channel 未配置")
        if self._sdk is None:
            try:
                import pyAgxArm as sdk
            except ImportError as exc:  # pragma: no cover - hardware-only path
                raise RuntimeError("读取 Piper 反馈需要安装目标版本 pyAgxArm") from exc
            self._sdk = sdk
        sdk = self._sdk
        models = {
            "piper": sdk.ArmModel.PIPER,
            "piper_h": sdk.ArmModel.PIPER_H,
            "piper_l": sdk.ArmModel.PIPER_L,
            "piper_x": sdk.ArmModel.PIPER_X,
        }
        firmwares = {
            "default": sdk.PiperFW.DEFAULT,
            "v183": sdk.PiperFW.V183,
            "v188": sdk.PiperFW.V188,
            "v189": sdk.PiperFW.V189,
        }
        model = models.get(str(self._config.get("arm_model")))
        firmware = firmwares.get(str(self._config.get("firmware_version")))
        if model is None or firmware is None:
            raise ValueError("Piper 型号或固件配置无效")
        kwargs = {
            "robot": model,
            "firmeware_version": firmware,
            "interface": str(self._config.get("can_interface", "socketcan")),
            "channel": str(self._config["can_channel"]),
        }
        try:
            sdk_config = sdk.create_agx_arm_config(**kwargs)
        except TypeError:
            kwargs.pop("interface")
            sdk_config = sdk.create_agx_arm_config(**kwargs)
        robot = sdk.AgxArmFactory.create_arm(sdk_config)
        try:
            robot.connect()
        except BaseException:
            disconnect = getattr(robot, "disconnect", None)
            if callable(disconnect):
                disconnect()
            raise
        self._robot = robot

    def read_status(self) -> dict[str, Any]:
        if self._robot is None:
            raise RuntimeError("Piper 尚未连接")
        feedback = self._robot.get_arm_status()
        msg = _message(feedback)
        return {
            "timestamp": _finite_timestamp(feedback),
            "arm_status": getattr(msg, "arm_status", None),
            "ctrl_mode": getattr(msg, "ctrl_mode", None),
            "teach_status": getattr(msg, "teach_status", None),
            "motion_status": getattr(msg, "motion_status", None),
            "err_code": getattr(msg, "err_code", None),
            "can_ok": self._robot.is_ok()
            if callable(getattr(self._robot, "is_ok", None))
            else None,
        }

    def read(self) -> FlangePose:
        if self._robot is None:
            raise RuntimeError("Piper 尚未连接")
        started_ns = time.monotonic_ns()
        status = self.read_status()
        feedback = self._robot.get_flange_pose()
        received_ns = time.monotonic_ns()
        return FlangePose(
            transform=RigidTransform(
                "base", "flange", _pose_zyx_to_transform(_pose_vector(feedback))
            ),
            stamp=CaptureStamp(
                host_started_ns=started_ns,
                host_received_ns=received_ns,
                device_timestamp=_finite_timestamp(feedback),
            ),
            status=status,
        )

    def close(self) -> None:
        if self._robot is None:
            return
        disconnect = getattr(self._robot, "disconnect", None)
        try:
            if callable(disconnect):
                disconnect()
        finally:
            self._robot = None


def create_piper_flange_source(descriptor: ComponentDescriptor) -> PiperFlangeSource:
    """把通用组件描述收窄为只读 Piper 法兰反馈配置。"""

    if descriptor.adapter != "piper-readonly":
        raise ValueError("Piper 法兰源工厂要求 adapter=piper-readonly")
    if descriptor.settings.get("allow_robot_control") is not False:
        raise ValueError("Piper 法兰源要求 allow_robot_control=false")
    try:
        base_frame = descriptor.frames["base"]
        flange_frame = descriptor.frames["flange"]
        model = descriptor.settings["model"]
        firmware = descriptor.settings["firmware_profile"]
    except KeyError as exc:
        raise ValueError(f"Piper 法兰源描述缺少字段：{exc.args[0]}") from exc
    reserved = {
        "arm_model",
        "firmware_version",
        "can_channel",
        "base_frame",
        "flange_frame",
    } & set(descriptor.settings)
    if reserved:
        raise ValueError(f"Piper 法兰源保留设置不得覆盖：{sorted(reserved)}")

    config = dict(descriptor.settings)
    config.update(
        {
            "arm_model": model,
            "firmware_version": firmware,
            "can_channel": descriptor.source_id,
            "base_frame": base_frame,
            "flange_frame": flange_frame,
            "allow_robot_control": False,
            "can_mapping_verified": True,
        }
    )
    config.setdefault("can_interface", "socketcan")
    return PiperFlangeSource(config)


__all__ = ["PiperFlangeSource", "create_piper_flange_source"]
