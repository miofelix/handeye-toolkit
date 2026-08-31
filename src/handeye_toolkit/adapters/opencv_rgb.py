"""基于 OpenCV VideoCapture 的普通 RGB 相机适配器。"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

import numpy as np

from ..domain import CameraFrame, CameraIntrinsics, CaptureStamp, ComponentDescriptor


def _positive_integer(value: object, label: str, *, allow_zero: bool = False) -> int:
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


def _positive_float(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是正有限数值")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是正有限数值") from exc
    if not np.isfinite(result) or result <= 0.0:
        raise ValueError(f"{label} 必须是正有限数值")
    return result


def _camera_intrinsics(value: object) -> CameraIntrinsics:
    if isinstance(value, CameraIntrinsics):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("rgb.intrinsics 必须是对象")
    expected = {
        "fx",
        "fy",
        "cx",
        "cy",
        "distortion_model",
        "distortion_coefficients",
    }
    if set(value) != expected:
        raise ValueError(
            f"rgb.intrinsics 字段不符合合同：缺少 {sorted(expected - set(value))}；"
            f"多出 {sorted(set(value) - expected)}"
        )
    coefficients = value["distortion_coefficients"]
    if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
        raise ValueError("rgb.intrinsics.distortion_coefficients 必须是数组")
    return CameraIntrinsics(
        fx=float(value["fx"]),
        fy=float(value["fy"]),
        cx=float(value["cx"]),
        cy=float(value["cy"]),
        distortion_model=(
            None if value["distortion_model"] is None else str(value["distortion_model"])
        ),
        distortion_coefficients=tuple(float(item) for item in coefficients),
    )


class OpenCvRgbCamera:
    """使用显式内参和固定分辨率采集普通 USB RGB 相机。"""

    _BACKENDS = {
        "any": "CAP_ANY",
        "v4l2": "CAP_V4L2",
        "avfoundation": "CAP_AVFOUNDATION",
        "dshow": "CAP_DSHOW",
        "msmf": "CAP_MSMF",
        "gstreamer": "CAP_GSTREAMER",
    }

    def __init__(self, config: Mapping[str, Any], *, cv2_module: Any = None) -> None:
        self.config = dict(config)
        self.config["device_index"] = _positive_integer(
            self.config.get("device_index"), "rgb.device_index", allow_zero=True
        )
        self.config["width"] = _positive_integer(self.config.get("width"), "rgb.width")
        self.config["height"] = _positive_integer(self.config.get("height"), "rgb.height")
        self.config["fps"] = _positive_float(self.config.get("fps", 30.0), "rgb.fps")
        self.config["warmup_frames"] = _positive_integer(
            self.config.get("warmup_frames", 10), "rgb.warmup_frames", allow_zero=True
        )
        backend = str(self.config.get("backend", "any")).strip().lower()
        if backend not in self._BACKENDS:
            raise ValueError(f"rgb.backend 无效：{backend}")
        self.config["backend"] = backend
        fourcc = self.config.get("fourcc")
        if fourcc is not None and (len(str(fourcc)) != 4 or not str(fourcc).isascii()):
            raise ValueError("rgb.fourcc 必须是 4 个 ASCII 字符")
        self.intrinsics = _camera_intrinsics(self.config.get("intrinsics"))
        self._cv2 = cv2_module
        self._capture: Any = None
        self._sequence = 0

    def _backend_id(self) -> int:
        self._load_backend()
        attribute = self._BACKENDS[self.config["backend"]]
        value = getattr(self._cv2, attribute, None)
        if value is None:
            raise RuntimeError(f"当前 OpenCV 不支持视频后端 {self.config['backend']}")
        return int(value)

    def _load_backend(self) -> Any:
        if self._cv2 is None:
            try:
                import cv2
            except ImportError as exc:  # pragma: no cover - 仅运行环境执行
                raise RuntimeError("读取普通 RGB 相机需要安装 OpenCV") from exc
            self._cv2 = cv2
        return self._cv2

    def open(self) -> None:
        if self._capture is not None:
            return
        cv = self._load_backend()
        capture = cv.VideoCapture(self.config["device_index"], self._backend_id())
        try:
            if not capture.isOpened():
                raise RuntimeError(f"无法打开 RGB 相机索引 {self.config['device_index']}")
            capture.set(cv.CAP_PROP_FRAME_WIDTH, float(self.config["width"]))
            capture.set(cv.CAP_PROP_FRAME_HEIGHT, float(self.config["height"]))
            capture.set(cv.CAP_PROP_FPS, float(self.config["fps"]))
            fourcc = self.config.get("fourcc")
            if fourcc is not None:
                capture.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*str(fourcc)))
            for _ in range(self.config["warmup_frames"]):
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise RuntimeError("RGB 相机预热期间读取失败")
        except BaseException:
            capture.release()
            raise
        self._capture = capture
        self._sequence = 0

    def capture(self) -> CameraFrame:
        if self._capture is None:
            self.open()
        assert self._capture is not None
        started_ns = time.monotonic_ns()
        ok, color_bgr = self._capture.read()
        received_ns = time.monotonic_ns()
        if not ok or color_bgr is None:
            raise RuntimeError("RGB 相机读取失败")
        pixels = np.asarray(color_bgr)
        expected_shape = (self.config["height"], self.config["width"], 3)
        if pixels.shape != expected_shape:
            raise RuntimeError(
                f"RGB 相机帧尺寸与内参标定尺寸不一致：期望 {expected_shape}，实际 {pixels.shape}"
            )
        sequence = self._sequence
        self._sequence += 1
        return CameraFrame(
            color_bgr=pixels,
            intrinsics=self.intrinsics,
            stamp=CaptureStamp(started_ns, received_ns, sequence=sequence),
            stream={
                "backend": self.config["backend"],
                "color_format": "BGR8",
                "width": self.config["width"],
                "height": self.config["height"],
                "fps": self.config["fps"],
            },
        )

    def close(self) -> None:
        capture = self._capture
        self._capture = None
        if capture is not None:
            capture.release()

    def __enter__(self) -> OpenCvRgbCamera:
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def create_opencv_rgb_camera(descriptor: ComponentDescriptor) -> OpenCvRgbCamera:
    """把通用组件描述收窄为普通 RGB 相机配置。"""

    if descriptor.adapter != "opencv-rgb":
        raise ValueError("普通 RGB 相机工厂要求 adapter=opencv-rgb")
    try:
        device_index = int(descriptor.source_id)
        frame_id = descriptor.frames["camera"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("普通 RGB 相机要求非负整数 source_id 和 camera 坐标系") from exc
    if device_index < 0 or str(device_index) != descriptor.source_id:
        raise ValueError("普通 RGB 相机 source_id 必须是规范的非负整数")
    allowed = {
        "width",
        "height",
        "fps",
        "warmup_frames",
        "backend",
        "fourcc",
        "intrinsics",
    }
    unknown = set(descriptor.settings) - allowed
    if unknown:
        raise ValueError(f"普通 RGB 相机包含未知设置：{sorted(unknown)}")
    config = dict(descriptor.settings)
    config.update({"device_index": device_index, "frame_id": frame_id})
    return OpenCvRgbCamera(cast(Mapping[str, Any], config))


__all__ = ["OpenCvRgbCamera", "create_opencv_rgb_camera"]
