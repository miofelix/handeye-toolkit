"""DaBai、Piper 只读反馈和 ChArUco 的产品组合工厂。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..algorithms.charuco import CharucoDetector
from ..domain import AcquisitionDescriptor, CalibrationPlan
from ..ports import AcquisitionRig, Camera, ReadOnlyFlangeSource, TargetDetector
from .dabai import DaBaiCamera
from .piper import PiperFlangeSource

CameraFactory = Callable[[dict[str, Any]], Camera]
FlangeFactory = Callable[[dict[str, Any]], ReadOnlyFlangeSource]
DetectorFactory = Callable[[dict[str, Any], Any], TargetDetector]


@dataclass(frozen=True, slots=True)
class DabaiPiperRigFactory:
    descriptor: AcquisitionDescriptor
    camera_factory: CameraFactory = DaBaiCamera
    flange_factory: FlangeFactory = PiperFlangeSource
    detector_factory: DetectorFactory = CharucoDetector

    def __post_init__(self) -> None:
        if self.descriptor.camera.adapter != "dabai":
            raise ValueError("相机适配器必须是 dabai")
        if self.descriptor.flange.adapter != "piper-readonly":
            raise ValueError("法兰适配器必须是 piper-readonly")
        if self.descriptor.target.adapter != "charuco":
            raise ValueError("目标适配器必须是 charuco")
        if self.descriptor.flange.settings.get("allow_robot_control") is not False:
            raise ValueError("Piper 组合工厂要求 allow_robot_control=false")

    def create(self, plan: CalibrationPlan) -> AcquisitionRig:
        if plan.target.adapter != "charuco":
            raise ValueError("当前产品只支持 ChArUco 目标")
        if dict(plan.target.parameters) != dict(self.descriptor.target.settings):
            raise ValueError("标定计划与采集描述的目标规格不一致")
        camera_settings = {
            "name": "DaBai DC1",
            "serial_number": self.descriptor.camera.source_id,
            "frame_id": self.descriptor.camera.frames["camera"],
            "depth_format": "none",
            "align_to_color": False,
            "timeout_ms": 5000,
            "warmup_frames": 30,
        }
        flange_settings = {
            "arm_model": self.descriptor.flange.settings["model"],
            "firmware_version": self.descriptor.flange.settings["firmware_profile"],
            "can_interface": self.descriptor.flange.settings["can_interface"],
            "can_channel": self.descriptor.flange.source_id,
            "base_frame": self.descriptor.flange.frames["base"],
            "flange_frame": self.descriptor.flange.frames["flange"],
            "allow_robot_control": False,
            "can_mapping_verified": True,
        }
        return AcquisitionRig(
            descriptor=self.descriptor,
            camera=self.camera_factory(camera_settings),
            flange=self.flange_factory(flange_settings),
            detector=self.detector_factory(dict(plan.target.parameters), plan.detection),
        )


__all__ = ["DabaiPiperRigFactory"]
