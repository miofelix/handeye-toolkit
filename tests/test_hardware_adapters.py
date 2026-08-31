from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from handeye_toolkit.adapters import dabai as dabai_module
from handeye_toolkit.adapters import piper as piper_module


class FakeColorFrame:
    def get_format(self) -> str:
        return "RGB"

    def get_timestamp(self) -> float:
        return 12.5

    def get_frame_number(self) -> int:
        return 7


class FakeFrames:
    def get_depth_frame(self):
        return None

    def get_color_frame(self) -> FakeColorFrame:
        return FakeColorFrame()


class FakePipeline:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_dabai_adapter_forces_color_only_and_host_monotonic_interval(monkeypatch) -> None:
    ticks = iter([100, 180])
    monkeypatch.setattr(dabai_module.time, "monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(
        dabai_module,
        "_orbbec_frame_to_bgr",
        lambda _frame: np.zeros((3, 4, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        dabai_module,
        "_orbbec_intrinsics",
        lambda _frame, _pipeline: SimpleNamespace(fx=500.0, fy=501.0, cx=2.0, cy=1.5),
    )
    monkeypatch.setattr(
        dabai_module,
        "_orbbec_distortion",
        lambda _frame, _pipeline: (None, ()),
    )

    camera = dabai_module.DaBaiCamera({"serial_number": "camera-placeholder"})
    pipeline = FakePipeline()
    camera._pipeline = pipeline
    monkeypatch.setattr(camera, "_wait_for_frames", lambda: FakeFrames())

    frame = camera.capture()
    camera.close()

    assert camera.config["depth_format"] == "none"
    assert camera.config["align_to_color"] is False
    assert frame.stamp.host_started_ns == 100
    assert frame.stamp.host_received_ns == 180
    assert frame.stamp.device_timestamp == 12.5
    assert frame.stamp.sequence == 7
    assert pipeline.stopped


class FakeRobot:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> None:
        self.calls.append("connect")

    def get_arm_status(self):
        self.calls.append("get_arm_status")
        return SimpleNamespace(
            timestamp=8.5,
            msg=SimpleNamespace(
                arm_status=0,
                ctrl_mode=0,
                teach_status=1,
                motion_status=0,
                err_code=0,
            ),
        )

    def is_ok(self) -> bool:
        self.calls.append("is_ok")
        return True

    def get_flange_pose(self):
        self.calls.append("get_flange_pose")
        return SimpleNamespace(timestamp=9.25, msg=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def fake_piper_sdk(robot: FakeRobot):
    class ArmModel:
        PIPER = "piper"
        PIPER_H = "piper_h"
        PIPER_L = "piper_l"
        PIPER_X = "piper_x"

    class PiperFW:
        DEFAULT = "default"
        V183 = "v183"
        V188 = "v188"
        V189 = "v189"

    class AgxArmFactory:
        @staticmethod
        def create_arm(_config):
            return robot

    return SimpleNamespace(
        ArmModel=ArmModel,
        PiperFW=PiperFW,
        AgxArmFactory=AgxArmFactory,
        create_agx_arm_config=lambda **kwargs: kwargs,
    )


def test_piper_adapter_is_read_only_and_brackets_feedback(monkeypatch) -> None:
    ticks = iter([200, 260])
    monkeypatch.setattr(piper_module.time, "monotonic_ns", lambda: next(ticks))
    robot = FakeRobot()
    source = piper_module.PiperFlangeSource(
        {
            "arm_model": "piper",
            "firmware_version": "v188",
            "can_interface": "socketcan",
            "can_channel": "channel-placeholder",
        },
        sdk_module=fake_piper_sdk(robot),
    )

    source.open()
    pose = source.read()
    source.close()

    assert source._config["allow_robot_control"] is False
    assert source._config["can_mapping_verified"] is True
    assert pose.stamp.host_started_ns == 200
    assert pose.stamp.host_received_ns == 260
    assert pose.stamp.device_timestamp == 9.25
    assert pose.status["teach_status"] == 1
    assert robot.calls == [
        "connect",
        "get_arm_status",
        "is_ok",
        "get_flange_pose",
        "disconnect",
    ]
