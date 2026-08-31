"""D435 和普通 RGB 相机适配器合同。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from handeye_toolkit.adapters import opencv_rgb as rgb_module
from handeye_toolkit.adapters import realsense as realsense_module
from handeye_toolkit.domain import ComponentDescriptor


class FakeRealSenseColorFrame:
    def __init__(self) -> None:
        intrinsics = SimpleNamespace(
            fx=600.0,
            fy=601.0,
            ppx=2.0,
            ppy=1.5,
            model="distortion.brown_conrady",
            coeffs=[0.1, -0.2, 0.0, 0.0, 0.01],
        )
        video_profile = SimpleNamespace(get_intrinsics=lambda: intrinsics)
        self.profile = SimpleNamespace(as_video_stream_profile=lambda: video_profile)

    def get_data(self) -> np.ndarray:
        return np.zeros((3, 4, 3), dtype=np.uint8)

    def get_timestamp(self) -> float:
        return 12.5

    def get_frame_number(self) -> int:
        return 7


class FakeRealSensePipeline:
    def __init__(self, device_name: str = "Intel RealSense D435") -> None:
        self.device_name = device_name
        self.started_with = None
        self.stopped = False

    def start(self, stream_config):
        self.started_with = stream_config
        device = SimpleNamespace(
            supports=lambda _key: True,
            get_info=lambda _key: self.device_name,
        )
        return SimpleNamespace(get_device=lambda: device)

    def wait_for_frames(self, _timeout_ms: int):
        return SimpleNamespace(get_color_frame=lambda: FakeRealSenseColorFrame())

    def stop(self) -> None:
        self.stopped = True


class FakeRealSenseConfig:
    def __init__(self) -> None:
        self.serials: list[str] = []
        self.streams: list[tuple[object, ...]] = []

    def enable_device(self, serial: str) -> None:
        self.serials.append(serial)

    def enable_stream(self, *args: object) -> None:
        self.streams.append(args)


def fake_realsense_sdk(
    pipeline: FakeRealSensePipeline,
    stream_config: FakeRealSenseConfig,
):
    return SimpleNamespace(
        pipeline=lambda: pipeline,
        config=lambda: stream_config,
        stream=SimpleNamespace(color="color"),
        format=SimpleNamespace(bgr8="bgr8"),
        camera_info=SimpleNamespace(name="name"),
    )


def test_d435_uses_color_intrinsics_and_host_capture_interval(monkeypatch) -> None:
    ticks = iter([100, 180])
    monkeypatch.setattr(realsense_module.time, "monotonic_ns", lambda: next(ticks))
    pipeline = FakeRealSensePipeline()
    stream_config = FakeRealSenseConfig()
    camera = realsense_module.RealSenseD435Camera(
        {
            "serial_number": "camera-placeholder",
            "width": 4,
            "height": 3,
            "fps": 30,
            "warmup_frames": 0,
        },
        rs_module=fake_realsense_sdk(pipeline, stream_config),
    )

    frame = camera.capture()
    camera.close()

    assert stream_config.serials == ["camera-placeholder"]
    assert stream_config.streams == [("color", 4, 3, "bgr8", 30)]
    assert frame.intrinsics.fx == 600.0
    assert frame.intrinsics.distortion_model == "brown_conrady"
    assert frame.intrinsics.distortion_coefficients == (0.1, -0.2, 0.0, 0.0, 0.01)
    assert frame.stamp.host_started_ns == 100
    assert frame.stamp.host_received_ns == 180
    assert frame.stamp.device_timestamp == 12.5
    assert frame.stamp.sequence == 7
    assert frame.stream["backend"] == "pyrealsense2"
    assert pipeline.stopped


def test_d435_rejects_other_realsense_models() -> None:
    pipeline = FakeRealSensePipeline("Intel RealSense placeholder")
    stream_config = FakeRealSenseConfig()
    camera = realsense_module.RealSenseD435Camera(
        {"width": 4, "height": 3, "warmup_frames": 0},
        rs_module=fake_realsense_sdk(pipeline, stream_config),
    )

    with pytest.raises(RuntimeError, match="不是 D435"):
        camera.open()
    assert pipeline.stopped


class FakeVideoCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.properties: list[tuple[int, float]] = []
        self.released = False

    def isOpened(self) -> bool:
        return True

    def set(self, name: int, value: float) -> bool:
        self.properties.append((name, value))
        return True

    def read(self) -> tuple[bool, np.ndarray]:
        return True, self.frame

    def release(self) -> None:
        self.released = True


def fake_cv2(capture: FakeVideoCapture):
    return SimpleNamespace(
        CAP_ANY=0,
        CAP_PROP_FRAME_WIDTH=1,
        CAP_PROP_FRAME_HEIGHT=2,
        CAP_PROP_FPS=3,
        CAP_PROP_FOURCC=4,
        VideoCapture=lambda _index, _backend: capture,
        VideoWriter_fourcc=lambda *_value: 1234,
    )


def rgb_config() -> dict[str, object]:
    return {
        "device_index": 0,
        "width": 4,
        "height": 3,
        "fps": 25.0,
        "warmup_frames": 0,
        "backend": "any",
        "intrinsics": {
            "fx": 500.0,
            "fy": 501.0,
            "cx": 2.0,
            "cy": 1.5,
            "distortion_model": "brown-conrady",
            "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }


def test_opencv_rgb_requires_intrinsics_and_fixed_resolution(monkeypatch) -> None:
    ticks = iter([200, 260, 300, 360])
    monkeypatch.setattr(rgb_module.time, "monotonic_ns", lambda: next(ticks))
    capture = FakeVideoCapture(np.zeros((3, 4, 3), dtype=np.uint8))
    camera = rgb_module.OpenCvRgbCamera(rgb_config(), cv2_module=fake_cv2(capture))

    frame = camera.capture()
    camera.close()

    assert frame.intrinsics.fx == 500.0
    assert frame.stamp.host_started_ns == 200
    assert frame.stamp.host_received_ns == 260
    assert frame.stamp.device_timestamp is None
    assert frame.stamp.sequence == 0
    assert frame.stream["color_format"] == "BGR8"
    assert capture.properties == [(1, 4.0), (2, 3.0), (3, 25.0)]
    assert capture.released

    missing = rgb_config()
    missing.pop("intrinsics")
    with pytest.raises(ValueError, match="intrinsics"):
        rgb_module.OpenCvRgbCamera(missing, cv2_module=fake_cv2(capture))

    wrong_size = FakeVideoCapture(np.zeros((2, 4, 3), dtype=np.uint8))
    camera = rgb_module.OpenCvRgbCamera(rgb_config(), cv2_module=fake_cv2(wrong_size))
    with pytest.raises(RuntimeError, match="内参标定尺寸不一致"):
        camera.capture()


def test_camera_descriptor_factories_validate_adapter_settings() -> None:
    d435 = realsense_module.create_realsense_d435_camera(
        ComponentDescriptor(
            "realsense-d435",
            "camera-placeholder",
            {"camera": "camera"},
            {"width": 4, "height": 3, "warmup_frames": 0},
        )
    )
    assert d435.config["serial_number"] == "camera-placeholder"

    settings = rgb_config()
    settings.pop("device_index")
    rgb = rgb_module.create_opencv_rgb_camera(
        ComponentDescriptor("opencv-rgb", "0", {"camera": "camera"}, settings)
    )
    assert rgb.config["device_index"] == 0

    with pytest.raises(ValueError, match="未知设置"):
        realsense_module.create_realsense_d435_camera(
            ComponentDescriptor(
                "realsense-d435",
                "camera-placeholder",
                {"camera": "camera"},
                {"unknown": True},
            )
        )
