"""由组件注册表组装采集装置。"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import AcquisitionDescriptor, CalibrationPlan
from ..ports import AcquisitionRig
from .registry import ComponentRegistry


@dataclass(frozen=True, slots=True)
class ComponentRigFactory:
    """按采集描述解析三个独立组件，不绑定具体厂商组合。"""

    descriptor: AcquisitionDescriptor
    registry: ComponentRegistry

    def __post_init__(self) -> None:
        self.registry.validate(
            camera=self.descriptor.camera,
            flange=self.descriptor.flange,
            target=self.descriptor.target,
        )

    def create(self, plan: CalibrationPlan) -> AcquisitionRig:
        if plan.target.adapter != self.descriptor.target.adapter:
            raise ValueError("标定计划与采集描述的目标适配器不一致")
        if dict(plan.target.parameters) != dict(self.descriptor.target.settings):
            raise ValueError("标定计划与采集描述的目标规格不一致")
        return AcquisitionRig(
            descriptor=self.descriptor,
            camera=self.registry.create_camera(self.descriptor.camera),
            flange=self.registry.create_flange(self.descriptor.flange),
            detector=self.registry.create_target(self.descriptor.target, plan.detection),
        )


__all__ = ["ComponentRigFactory"]
