"""组件适配器注册表；把组件选择与具体实现解耦。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from ..domain import ComponentDescriptor, DetectionPolicy
from ..ports import (
    Camera,
    CameraAdapterFactory,
    FlangeAdapterFactory,
    ReadOnlyFlangeSource,
    TargetAdapterFactory,
    TargetDetector,
)

FactoryT = TypeVar("FactoryT")


def _adapter_name(value: str) -> str:
    name = str(value).strip()
    if not name or any(character.isspace() for character in name):
        raise ValueError("适配器名称必须是无空白的非空字符串")
    return name


class ComponentRegistry:
    """显式注册相机、只读法兰源和标定板检测器工厂。"""

    def __init__(self) -> None:
        self._cameras: dict[str, CameraAdapterFactory] = {}
        self._flanges: dict[str, FlangeAdapterFactory] = {}
        self._targets: dict[str, TargetAdapterFactory] = {}

    @staticmethod
    def _register(
        factories: dict[str, FactoryT],
        adapter: str,
        factory: FactoryT,
        component: str,
    ) -> None:
        name = _adapter_name(adapter)
        if not callable(factory):
            raise TypeError(f"{component}适配器工厂必须可调用")
        if name in factories:
            raise ValueError(f"{component}适配器已注册：{name}")
        factories[name] = factory

    def register_camera(
        self,
        adapter: str,
        factory: CameraAdapterFactory,
    ) -> ComponentRegistry:
        self._register(self._cameras, adapter, factory, "相机")
        return self

    def register_flange(
        self,
        adapter: str,
        factory: FlangeAdapterFactory,
    ) -> ComponentRegistry:
        self._register(self._flanges, adapter, factory, "法兰源")
        return self

    def register_target(
        self,
        adapter: str,
        factory: TargetAdapterFactory,
    ) -> ComponentRegistry:
        self._register(self._targets, adapter, factory, "目标")
        return self

    @property
    def camera_adapters(self) -> tuple[str, ...]:
        return tuple(sorted(self._cameras))

    @property
    def flange_adapters(self) -> tuple[str, ...]:
        return tuple(sorted(self._flanges))

    @property
    def target_adapters(self) -> tuple[str, ...]:
        return tuple(sorted(self._targets))

    @staticmethod
    def _resolve(
        factories: Mapping[str, FactoryT],
        descriptor: ComponentDescriptor,
        component: str,
    ) -> FactoryT:
        try:
            return factories[descriptor.adapter]
        except KeyError as exc:
            available = "、".join(sorted(factories)) or "无"
            raise ValueError(
                f"未注册{component}适配器 {descriptor.adapter}；可用适配器：{available}"
            ) from exc

    def validate(
        self,
        *,
        camera: ComponentDescriptor,
        flange: ComponentDescriptor,
        target: ComponentDescriptor,
    ) -> None:
        """在创建任务前确认描述中的所有适配器均可解析。"""

        self._resolve(self._cameras, camera, "相机")
        self._resolve(self._flanges, flange, "法兰源")
        self._resolve(self._targets, target, "目标")

    def create_camera(self, descriptor: ComponentDescriptor) -> Camera:
        factory = self._resolve(self._cameras, descriptor, "相机")
        result = factory(descriptor)
        if not isinstance(result, Camera):
            raise TypeError(f"相机适配器 {descriptor.adapter} 未实现 Camera 端口")
        return result

    def create_flange(self, descriptor: ComponentDescriptor) -> ReadOnlyFlangeSource:
        factory = self._resolve(self._flanges, descriptor, "法兰源")
        result = factory(descriptor)
        if not isinstance(result, ReadOnlyFlangeSource):
            raise TypeError(f"法兰源适配器 {descriptor.adapter} 未实现 ReadOnlyFlangeSource 端口")
        return result

    def create_target(
        self,
        descriptor: ComponentDescriptor,
        policy: DetectionPolicy,
    ) -> TargetDetector:
        factory = self._resolve(self._targets, descriptor, "目标")
        result = factory(descriptor, policy)
        if not isinstance(result, TargetDetector):
            raise TypeError(f"目标适配器 {descriptor.adapter} 未实现 TargetDetector 端口")
        return result


__all__ = ["ComponentRegistry"]
