"""Intel RealSense D435 彩色流相机适配器。"""

from __future__ import annotations

import math
import time
from typing import Any, Mapping, cast

import numpy as np

from ..domain import CameraFrame, CameraIntrinsics, CaptureStamp, ComponentDescriptor


def _integer(value: object, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是整数")
    try:
        result = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    minimum = 0 if allow_zero else 1
    if result < minimum or result != value:
        raise ValueError(f"{label} 必须是不小于 {minimum} 的整数")
    return result


def _distortion_name(value: object) -> str | None:
    if value is None:
        return None
    name = str(value).strip().rsplit(".", 1)[-1]
    return name or None


class RealSenseD435Camera:
    """通过 pyrealsense2 采集 D435 的 BGR8 彩色流和设备内参。"""

    def __init__(self, config: Mapping[str, Any], *, rs_module: Any = None) -> None:
        self.config = dict(config)
        self.config["width"] = _integer(self.config.get("width", 1280), "d435.width")
        self.config["height"] = _integer(self.config.get("height", 720), "d435.height")
        self.config["fps"] = _integer(self.config.get("fps", 30), "d435.fps")
        self.config["timeout_ms"] = _integer(
            self.config.get("timeout_ms", 5000), "d435.timeout_ms"
        )
        self.config["warmup_frames"] = _integer(
            self.config.get("warmup_frames", 30),
            "d435.warmup_frames",
            allow_zero=True,
        )
        self._rs = rs_module
        self._pipeline: Any = None

    def _load_sdk(self) -> Any:
        if self._rs is None:
            try:
                import pyrealsense2 as rs
            except ImportError as exc:  # pragma: no cover - 仅硬件环境执行
                raise RuntimeError("读取 D435 需要安装 pyrealsense2") from exc
            self._rs = rs
        return self._rs

    @staticmethod
    def _device_name(profile: Any, rs: Any) -> str | None:
        try:
            device = profile.get_device()
            key = rs.camera_info.name
            if hasattr(device, "supports") and not device.supports(key):
                return None
            return str(device.get_info(key)).strip() or None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def open(self) -> None:
        if self._pipeline is not None:
            return
        rs = self._load_sdk()
        pipeline = rs.pipeline()
        stream_config = rs.config()
        serial = str(self.config.get("serial_number", "")).strip()
        if serial:
            stream_config.enable_device(serial)
        stream_config.enable_stream(
            rs.stream.color,
            self.config["width"],
            self.config["height"],
            rs.format.bgr8,
            self.config["fps"],
        )
        started = False
        try:
            profile = pipeline.start(stream_config)
            started = True
            device_name = self._device_name(profile, rs)
            if device_name is not None and "D435" not in device_name.upper():
                raise RuntimeError(f"所选 RealSense 设备不是 D435：{device_name}")
            self._pipeline = pipeline
            for _ in range(self.config["warmup_frames"]):
                self._wait_for_color_frame()
        except BaseException:
            if started:
                try:
                    pipeline.stop()
                except Exception:
                    pass
            self._pipeline = None
            raise

    def _wait_for_color_frame(self) -> Any:
        if self._pipeline is None:
            raise RuntimeError("D435 尚未打开")
        frames = self._pipeline.wait_for_frames(self.config["timeout_ms"])
        if frames is None:
            raise TimeoutError(f"D435 在 {self.config['timeout_ms']} ms 内未返回帧")
        color_frame = frames.get_color_frame()
        if color_frame is None:
            raise RuntimeError("D435 帧集中缺少彩色帧")
        return color_frame

    @staticmethod
    def _frame_intrinsics(color_frame: Any) -> CameraIntrinsics:
        try:
            profile = color_frame.profile.as_video_stream_profile()
            intrinsics = profile.get_intrinsics()
        except (AttributeError, RuntimeError, TypeError) as exc:
            raise RuntimeError("无法读取 D435 彩色流内参") from exc
        coefficients = tuple(float(value) for value in getattr(intrinsics, "coeffs", ()))
        if not all(math.isfinite(value) for value in coefficients):
            raise RuntimeError("D435 畸变参数包含非有限数值")
        return CameraIntrinsics(
            fx=float(intrinsics.fx),
            fy=float(intrinsics.fy),
            cx=float(intrinsics.ppx),
            cy=float(intrinsics.ppy),
            distortion_model=_distortion_name(getattr(intrinsics, "model", None)),
            distortion_coefficients=coefficients,
        )

    def capture(self) -> CameraFrame:
        if self._pipeline is None:
            self.open()
        started_ns = time.monotonic_ns()
        color_frame = self._wait_for_color_frame()
        received_ns = time.monotonic_ns()
        color_bgr = np.asarray(color_frame.get_data())
        expected_shape = (self.config["height"], self.config["width"], 3)
        if color_bgr.shape != expected_shape:
            raise RuntimeError(
                f"D435 彩色帧尺寸不匹配：期望 {expected_shape}，实际 {color_bgr.shape}"
            )
        try:
            timestamp = float(color_frame.get_timestamp())
        except (AttributeError, TypeError, ValueError):
            timestamp = math.nan
        device_timestamp = timestamp if math.isfinite(timestamp) else None
        try:
            sequence = int(color_frame.get_frame_number())
        except (AttributeError, TypeError, ValueError):
            sequence = None
        return CameraFrame(
            color_bgr=color_bgr,
            intrinsics=self._frame_intrinsics(color_frame),
            stamp=CaptureStamp(
                host_started_ns=started_ns,
                host_received_ns=received_ns,
                device_timestamp=device_timestamp,
                sequence=sequence,
            ),
            stream={
                "backend": "pyrealsense2",
                "color_format": "BGR8",
                "width": self.config["width"],
                "height": self.config["height"],
                "fps": self.config["fps"],
            },
        )

    def close(self) -> None:
        pipeline = self._pipeline
        self._pipeline = None
        if pipeline is not None:
            pipeline.stop()

    def __enter__(self) -> RealSenseD435Camera:
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def create_realsense_d435_camera(descriptor: ComponentDescriptor) -> RealSenseD435Camera:
    """把通用组件描述收窄为 D435 彩色相机配置。"""

    if descriptor.adapter != "realsense-d435":
        raise ValueError("D435 相机工厂要求 adapter=realsense-d435")
    try:
        frame_id = descriptor.frames["camera"]
    except KeyError as exc:
        raise ValueError("D435 相机描述缺少 camera 坐标系") from exc
    allowed = {"width", "height", "fps", "timeout_ms", "warmup_frames"}
    unknown = set(descriptor.settings) - allowed
    if unknown:
        raise ValueError(f"D435 相机包含未知设置：{sorted(unknown)}")
    config = dict(descriptor.settings)
    config.update(
        {
            "name": "Intel RealSense D435",
            "serial_number": descriptor.source_id,
            "frame_id": frame_id,
        }
    )
    return RealSenseD435Camera(config)


__all__ = ["RealSenseD435Camera", "create_realsense_d435_camera"]
