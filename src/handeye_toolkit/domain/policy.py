"""类型化的标定计划。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, cast

from .models import CalibrationMode, JsonValue, _json_dict, _json_mapping


def _positive(value: object, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是有限数值")
    result = float(cast(Any, value))
    if not math.isfinite(result) or result < 0.0 or (not allow_zero and result == 0.0):
        raise ValueError(f"{label} 必须是{'非负' if allow_zero else '正'}有限数值")
    return result


def _integer(value: object, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是整数")
    result = int(cast(Any, value))
    minimum = 0 if allow_zero else 1
    if result != value or result < minimum:
        raise ValueError(f"{label} 必须是不小于 {minimum} 的整数")
    return result


@dataclass(frozen=True, slots=True)
class TargetDescriptor:
    adapter: str
    parameters: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        adapter = str(self.adapter).strip()
        if not adapter or any(character.isspace() for character in adapter):
            raise ValueError("target.adapter 必须是无空白的非空字符串")
        object.__setattr__(self, "adapter", adapter)
        object.__setattr__(self, "parameters", _json_mapping(self.parameters))

    def as_dict(self) -> dict[str, JsonValue]:
        return {"adapter": self.adapter, "parameters": _json_dict(self.parameters)}


@dataclass(frozen=True, slots=True)
class SamplingPolicy:
    calibration_target: int
    validation_target: int
    candidate_frames: int
    stability_before_s: float
    stability_after_s: float
    feedback_hz: float
    max_translation_drift_m: float
    max_rotation_drift_deg: float
    max_capture_interval_s: float

    def __post_init__(self) -> None:
        for name in ("calibration_target", "validation_target", "candidate_frames"):
            object.__setattr__(self, name, _integer(getattr(self, name), name))
        for name in (
            "stability_before_s",
            "stability_after_s",
            "feedback_hz",
            "max_capture_interval_s",
        ):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        for name in ("max_translation_drift_m", "max_rotation_drift_deg"):
            object.__setattr__(
                self, name, _positive(getattr(self, name), name, allow_zero=True)
            )


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    min_position_span_m: float
    min_rotation_span_deg: float
    min_rotation_change_deg: float
    nonparallel_axis_min_angle_deg: float
    duplicate_translation_m: float
    duplicate_rotation_deg: float

    def __post_init__(self) -> None:
        for name in (
            "min_position_span_m",
            "min_rotation_span_deg",
            "min_rotation_change_deg",
            "nonparallel_axis_min_angle_deg",
            "duplicate_translation_m",
            "duplicate_rotation_deg",
        ):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        if self.nonparallel_axis_min_angle_deg >= 180.0:
            raise ValueError("nonparallel_axis_min_angle_deg 必须小于 180")


@dataclass(frozen=True, slots=True)
class DetectionPolicy:
    min_corners: int
    min_area_ratio: float
    max_reprojection_rms_px: float
    min_sharpness: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_corners", _integer(self.min_corners, "min_corners"))
        object.__setattr__(self, "min_area_ratio", _positive(self.min_area_ratio, "min_area_ratio"))
        if self.min_area_ratio >= 1.0:
            raise ValueError("min_area_ratio 必须小于 1")
        object.__setattr__(
            self,
            "max_reprojection_rms_px",
            _positive(self.max_reprojection_rms_px, "max_reprojection_rms_px"),
        )
        object.__setattr__(self, "min_sharpness", _positive(self.min_sharpness, "min_sharpness"))


@dataclass(frozen=True, slots=True)
class SolverPolicy:
    translation_gate_m: float
    rotation_gate_deg: float
    bootstrap_iterations: int
    random_seed: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "translation_gate_m", _positive(self.translation_gate_m, "translation_gate_m"))
        object.__setattr__(self, "rotation_gate_deg", _positive(self.rotation_gate_deg, "rotation_gate_deg"))
        object.__setattr__(
            self,
            "bootstrap_iterations",
            _integer(self.bootstrap_iterations, "bootstrap_iterations", allow_zero=True),
        )
        object.__setattr__(self, "random_seed", _integer(self.random_seed, "random_seed", allow_zero=True))


@dataclass(frozen=True, slots=True)
class CalibrationPlan:
    profile: str
    mode: CalibrationMode
    target: TargetDescriptor
    sampling: SamplingPolicy
    coverage: CoveragePolicy
    detection: DetectionPolicy
    solver: SolverPolicy

    def __post_init__(self) -> None:
        profile = str(self.profile).strip()
        if not profile or any(character.isspace() for character in profile):
            raise ValueError("profile 必须是无空白的非空字符串")
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "mode", CalibrationMode.parse(self.mode))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "profile": self.profile,
            "mode": self.mode.value,
            "target": self.target.as_dict(),
            "sampling": asdict(self.sampling),
            "coverage": asdict(self.coverage),
            "detection": asdict(self.detection),
            "solver": asdict(self.solver),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CalibrationPlan:
        expected = {"profile", "mode", "target", "sampling", "coverage", "detection", "solver"}
        if set(value) != expected:
            raise ValueError(
                f"plan 字段不符合 schema：缺少 {sorted(expected - set(value))}；"
                f"多出 {sorted(set(value) - expected)}"
            )
        objects: dict[str, Mapping[str, object]] = {}
        for name in ("target", "sampling", "coverage", "detection", "solver"):
            item = value[name]
            if not isinstance(item, Mapping):
                raise ValueError(f"plan.{name} 必须是对象")
            objects[name] = cast(Mapping[str, object], item)
        target = objects["target"]
        if set(target) != {"adapter", "parameters"} or not isinstance(target["parameters"], Mapping):
            raise ValueError("plan.target 字段无效")
        return cls(
            profile=str(value["profile"]),
            mode=CalibrationMode.parse(str(value["mode"])),
            target=TargetDescriptor(
                str(target["adapter"]),
                dict(cast(Mapping[str, JsonValue], target["parameters"])),
            ),
            sampling=SamplingPolicy(**dict(objects["sampling"])),  # type: ignore[arg-type]
            coverage=CoveragePolicy(**dict(objects["coverage"])),  # type: ignore[arg-type]
            detection=DetectionPolicy(**dict(objects["detection"])),  # type: ignore[arg-type]
            solver=SolverPolicy(**dict(objects["solver"])),  # type: ignore[arg-type]
        )


__all__ = [
    "CalibrationPlan",
    "CoveragePolicy",
    "DetectionPolicy",
    "SamplingPolicy",
    "SolverPolicy",
    "TargetDescriptor",
]
