"""组件注册和通用采集装置组合合同。"""

from __future__ import annotations

import pytest
from conftest import product_document

from handeye_toolkit.app import resolve_plan, validate_product_config
from handeye_toolkit.composition import (
    ComponentRegistry,
    ComponentRigFactory,
    create_builtin_registry,
)
from handeye_toolkit.domain import (
    AcquisitionDescriptor,
    CalibrationPlan,
    CameraFrame,
    ComponentDescriptor,
    DetectionPolicy,
    FlangePose,
    TargetDetection,
)


class StubCamera:
    def open(self) -> None:
        pass

    def capture(self) -> CameraFrame:
        raise NotImplementedError

    def close(self) -> None:
        pass


class StubFlangeSource:
    def open(self) -> None:
        pass

    def read(self) -> FlangePose:
        raise NotImplementedError

    def close(self) -> None:
        pass


class StubTargetDetector:
    def detect(self, frame: CameraFrame) -> TargetDetection:
        raise NotImplementedError


def modular_descriptor() -> AcquisitionDescriptor:
    target_parameters = {"columns": 8, "rows": 6, "spacing_m": 0.02}
    return AcquisitionDescriptor(
        camera=ComponentDescriptor(
            "camera-placeholder",
            "camera-source-placeholder",
            {"camera": "camera"},
            {"stream": "color"},
        ),
        flange=ComponentDescriptor(
            "flange-placeholder",
            "flange-source-placeholder",
            {"base": "base", "flange": "flange"},
            {"read_only": True},
        ),
        target=ComponentDescriptor(
            "grid-placeholder",
            "target-source-placeholder",
            {"target": "target"},
            target_parameters,
        ),
    )


def modular_plan() -> CalibrationPlan:
    return resolve_plan(
        profile="standard",
        mode="eye-to-hand",
        target_adapter="grid-placeholder",
        target_parameters={"columns": 8, "rows": 6, "spacing_m": 0.02},
    )


def test_registry_composes_independently_registered_components() -> None:
    received: dict[str, object] = {}

    def camera_factory(descriptor: ComponentDescriptor) -> StubCamera:
        received["camera"] = descriptor
        return StubCamera()

    def flange_factory(descriptor: ComponentDescriptor) -> StubFlangeSource:
        received["flange"] = descriptor
        return StubFlangeSource()

    def target_factory(
        descriptor: ComponentDescriptor,
        policy: DetectionPolicy,
    ) -> StubTargetDetector:
        received["target"] = descriptor
        received["policy"] = policy
        return StubTargetDetector()

    registry = (
        ComponentRegistry()
        .register_camera("camera-placeholder", camera_factory)
        .register_flange("flange-placeholder", flange_factory)
        .register_target("grid-placeholder", target_factory)
    )
    descriptor = modular_descriptor()
    plan = modular_plan()

    rig = ComponentRigFactory(descriptor, registry).create(plan)

    assert isinstance(rig.camera, StubCamera)
    assert isinstance(rig.flange, StubFlangeSource)
    assert isinstance(rig.detector, StubTargetDetector)
    assert received == {
        "camera": descriptor.camera,
        "flange": descriptor.flange,
        "target": descriptor.target,
        "policy": plan.detection,
    }


def test_builtin_registry_composes_current_supported_components() -> None:
    config = validate_product_config(product_document())
    registry = create_builtin_registry()

    rig = ComponentRigFactory(config.acquisition, registry).create(config.plan)

    assert registry.camera_adapters == ("dabai",)
    assert registry.flange_adapters == ("piper-readonly",)
    assert registry.target_adapters == ("charuco",)
    assert type(rig.camera).__name__ == "DaBaiCamera"
    assert type(rig.flange).__name__ == "PiperFlangeSource"
    assert type(rig.detector).__name__ == "CharucoDetector"


def test_registry_rejects_duplicates_unknown_adapters_and_invalid_ports() -> None:
    registry = ComponentRegistry().register_camera(
        "camera-placeholder", lambda _descriptor: StubCamera()
    )
    with pytest.raises(ValueError, match="已注册"):
        registry.register_camera("camera-placeholder", lambda _descriptor: StubCamera())

    with pytest.raises(ValueError, match="flange-placeholder"):
        ComponentRigFactory(modular_descriptor(), registry)

    registry.register_flange("flange-placeholder", lambda _descriptor: object())
    registry.register_target("grid-placeholder", lambda _descriptor, _policy: StubTargetDetector())
    factory = ComponentRigFactory(modular_descriptor(), registry)
    with pytest.raises(TypeError, match="ReadOnlyFlangeSource"):
        factory.create(modular_plan())


def test_rig_factory_locks_target_adapter_and_parameters() -> None:
    registry = (
        ComponentRegistry()
        .register_camera("camera-placeholder", lambda _descriptor: StubCamera())
        .register_flange("flange-placeholder", lambda _descriptor: StubFlangeSource())
        .register_target("grid-placeholder", lambda _descriptor, _policy: StubTargetDetector())
    )
    factory = ComponentRigFactory(modular_descriptor(), registry)

    other_adapter = resolve_plan(
        profile="standard",
        mode="eye-to-hand",
        target_adapter="other-grid",
        target_parameters={"columns": 8, "rows": 6, "spacing_m": 0.02},
    )
    with pytest.raises(ValueError, match="目标适配器不一致"):
        factory.create(other_adapter)

    other_size = resolve_plan(
        profile="standard",
        mode="eye-to-hand",
        target_adapter="grid-placeholder",
        target_parameters={"columns": 9, "rows": 6, "spacing_m": 0.02},
    )
    with pytest.raises(ValueError, match="目标规格不一致"):
        factory.create(other_size)
