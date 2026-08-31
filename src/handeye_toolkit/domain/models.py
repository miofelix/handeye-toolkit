"""不可变的领域值对象和可持久化任务状态。"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, cast

import numpy as np

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _freeze_json(value: object, label: str = "JSON") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} 不得包含非有限数值")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} 的对象键必须是字符串")
            frozen[key] = _freeze_json(item, f"{label}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item, f"{label}[]") for item in value)
    raise ValueError(f"{label} 包含不可序列化的值")


def _thaw_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw_json(item) for item in value]
    return cast(JsonScalar, value)


def _json_mapping(value: Mapping[str, JsonValue] | None) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], _freeze_json(dict(value or {})))


def _json_dict(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _thaw_json(value))


def _identifier(value: object, label: str) -> str:
    text = str(value).strip()
    if not text or any(character.isspace() for character in text):
        raise ValueError(f"{label} 必须是无空白的非空字符串")
    return text


def _safe_id(value: object, label: str) -> str:
    text = _identifier(value, label)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", text) is None:
        raise ValueError(f"{label} 只能包含字母、数字、点、下划线和连字符")
    return text


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是有限数值")
    result = float(cast(Any, value))
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须是有限数值")
    return result


def _matrix(value: object, label: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"{label} 必须是 4x4 有限矩阵")
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9, rtol=0.0):
        raise ValueError(f"{label} 最后一行必须是 [0, 0, 0, 1]")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0.0):
        raise ValueError(f"{label} 旋转部分必须正交")
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-6):
        raise ValueError(f"{label} 旋转行列式必须为 +1")
    result = np.array(matrix, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


class CalibrationMode(str, Enum):
    EYE_TO_HAND = "eye-to-hand"
    EYE_IN_HAND = "eye-in-hand"

    @classmethod
    def parse(cls, value: CalibrationMode | str) -> CalibrationMode:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip())
        except ValueError as exc:
            raise ValueError("标定模式必须是 eye-to-hand 或 eye-in-hand") from exc


class SampleRole(str, Enum):
    CALIBRATION = "calibration"
    VALIDATION = "validation"

    @classmethod
    def parse(cls, value: SampleRole | str) -> SampleRole:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip())
        except ValueError as exc:
            raise ValueError("样本用途必须是 calibration 或 validation") from exc


class RunState(str, Enum):
    READY = "ready"
    COLLECTING_CALIBRATION = "collecting-calibration"
    COLLECTING_VALIDATION = "collecting-validation"
    READY_TO_SOLVE = "ready-to-solve"
    SOLVED = "solved"
    EXPORTED = "exported"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class RigidTransform:
    """从 ``child_frame`` 坐标到 ``parent_frame`` 坐标的刚体变换。"""

    parent_frame: str
    child_frame: str
    matrix: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_frame", _identifier(self.parent_frame, "parent_frame"))
        object.__setattr__(self, "child_frame", _identifier(self.child_frame, "child_frame"))
        if self.parent_frame == self.child_frame:
            raise ValueError("刚体变换的 parent_frame 与 child_frame 不能相同")
        object.__setattr__(self, "matrix", _matrix(self.matrix, "matrix"))

    def inverse(self) -> RigidTransform:
        return RigidTransform(self.child_frame, self.parent_frame, np.linalg.inv(self.matrix))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "parent_frame": self.parent_frame,
            "child_frame": self.child_frame,
            "matrix": self.matrix.tolist(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RigidTransform:
        _exact_keys(value, {"parent_frame", "child_frame", "matrix"}, "transform")
        return cls(
            str(value["parent_frame"]),
            str(value["child_frame"]),
            np.asarray(value["matrix"], dtype=np.float64),
        )


@dataclass(frozen=True, slots=True)
class CaptureStamp:
    """同一主机时钟上的采集区间，以及仅供审计的设备时间。"""

    host_started_ns: int
    host_received_ns: int
    device_timestamp: float | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.host_started_ns, bool) or int(self.host_started_ns) < 0:
            raise ValueError("host_started_ns 必须是非负整数")
        if isinstance(self.host_received_ns, bool) or int(self.host_received_ns) < int(
            self.host_started_ns
        ):
            raise ValueError("host_received_ns 必须不早于 host_started_ns")
        object.__setattr__(self, "host_started_ns", int(self.host_started_ns))
        object.__setattr__(self, "host_received_ns", int(self.host_received_ns))
        if self.device_timestamp is not None:
            object.__setattr__(
                self, "device_timestamp", _finite(self.device_timestamp, "device_timestamp")
            )
        if self.sequence is not None:
            if isinstance(self.sequence, bool) or int(self.sequence) < 0:
                raise ValueError("sequence 必须是非负整数或 null")
            object.__setattr__(self, "sequence", int(self.sequence))

    @property
    def midpoint_ns(self) -> int:
        return (self.host_started_ns + self.host_received_ns) // 2

    @property
    def duration_s(self) -> float:
        return (self.host_received_ns - self.host_started_ns) / 1_000_000_000.0

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "host_started_ns": self.host_started_ns,
            "host_received_ns": self.host_received_ns,
            "device_timestamp": self.device_timestamp,
            "sequence": self.sequence,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CaptureStamp:
        _exact_keys(
            value,
            {"host_started_ns", "host_received_ns", "device_timestamp", "sequence"},
            "stamp",
        )
        return cls(
            int(cast(Any, value["host_started_ns"])),
            int(cast(Any, value["host_received_ns"])),
            None
            if value["device_timestamp"] is None
            else float(cast(Any, value["device_timestamp"])),
            None if value["sequence"] is None else int(cast(Any, value["sequence"])),
        )


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    distortion_model: str | None = None
    distortion_coefficients: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        for name in ("fx", "fy", "cx", "cy"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise ValueError("相机 fx 和 fy 必须为正数")
        if self.distortion_model is not None:
            model = str(self.distortion_model).strip()
            if not model:
                raise ValueError("distortion_model 必须是非空字符串或 null")
            object.__setattr__(self, "distortion_model", model)
        coefficients = tuple(
            _finite(value, "distortion_coefficients") for value in self.distortion_coefficients
        )
        object.__setattr__(self, "distortion_coefficients", coefficients)

    @property
    def matrix(self) -> np.ndarray:
        result = np.asarray(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        result.setflags(write=False)
        return result

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "fx": self.fx,
            "fy": self.fy,
            "cx": self.cx,
            "cy": self.cy,
            "distortion_model": self.distortion_model,
            "distortion_coefficients": list(self.distortion_coefficients),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CameraIntrinsics:
        _exact_keys(
            value,
            {"fx", "fy", "cx", "cy", "distortion_model", "distortion_coefficients"},
            "intrinsics",
        )
        coefficients = value["distortion_coefficients"]
        if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
            raise ValueError("distortion_coefficients 必须是数组")
        return cls(
            _finite(value["fx"], "intrinsics.fx"),
            _finite(value["fy"], "intrinsics.fy"),
            _finite(value["cx"], "intrinsics.cx"),
            _finite(value["cy"], "intrinsics.cy"),
            None if value["distortion_model"] is None else str(value["distortion_model"]),
            tuple(float(item) for item in coefficients),
        )


@dataclass(frozen=True, slots=True)
class CameraFrame:
    color_bgr: np.ndarray
    intrinsics: CameraIntrinsics
    stamp: CaptureStamp
    stream: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        color = np.asarray(self.color_bgr)
        if color.ndim != 3 or color.shape[2] != 3:
            raise ValueError("color_bgr 必须是 HxWx3 图像")
        immutable = np.array(color, dtype=np.uint8, order="C", copy=True)
        immutable.setflags(write=False)
        object.__setattr__(self, "color_bgr", immutable)
        object.__setattr__(self, "stream", _json_mapping(self.stream))


@dataclass(frozen=True, slots=True)
class FlangePose:
    transform: RigidTransform
    stamp: CaptureStamp
    status: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.transform.parent_frame, self.transform.child_frame) != ("base", "flange"):
            raise ValueError("法兰反馈坐标合同必须是 base <- flange")
        object.__setattr__(self, "status", _json_mapping(self.status))


@dataclass(frozen=True, slots=True)
class DetectionQuality:
    passed: bool
    reasons: tuple[str, ...] = ()
    rank: tuple[float, ...] = ()
    metrics: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("passed 必须是布尔值")
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "rank", tuple(_finite(item, "rank") for item in self.rank))
        object.__setattr__(self, "metrics", _json_mapping(self.metrics))


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    expected: str
    observed: str
    valid: bool
    fingerprint: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", str(self.expected).strip())
        object.__setattr__(self, "observed", str(self.observed).strip())
        fingerprint = _identifier(self.fingerprint, "fingerprint")
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise ValueError("fingerprint 必须是小写 SHA-256")
        object.__setattr__(self, "fingerprint", fingerprint)
        if not self.expected or not self.observed or not isinstance(self.valid, bool):
            raise ValueError("目标身份字段无效")
        object.__setattr__(self, "details", _json_mapping(self.details))


@dataclass(frozen=True, slots=True)
class TargetDetection:
    transform: RigidTransform | None
    overlay_bgr: np.ndarray
    quality: DetectionQuality
    identity: TargetIdentity | None = None
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.transform is not None and (
            self.transform.parent_frame,
            self.transform.child_frame,
        ) != ("camera", "target"):
            raise ValueError("目标检测坐标合同必须是 camera <- target")
        overlay = np.asarray(self.overlay_bgr)
        if overlay.ndim != 3 or overlay.shape[2] != 3:
            raise ValueError("overlay_bgr 必须是 HxWx3 图像")
        immutable = np.array(overlay, dtype=np.uint8, order="C", copy=True)
        immutable.setflags(write=False)
        object.__setattr__(self, "overlay_bgr", immutable)
        object.__setattr__(self, "evidence", _json_mapping(self.evidence))


@dataclass(frozen=True, slots=True)
class ComponentDescriptor:
    adapter: str
    source_id: str
    frames: Mapping[str, str]
    settings: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter", _identifier(self.adapter, "adapter"))
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        normalized_frames = {
            _identifier(name, "frame role"): _identifier(frame, "frame")
            for name, frame in self.frames.items()
        }
        object.__setattr__(self, "frames", MappingProxyType(normalized_frames))
        object.__setattr__(self, "settings", _json_mapping(self.settings))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "adapter": self.adapter,
            "source_id": self.source_id,
            "frames": dict(self.frames),
            "settings": _json_dict(self.settings),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ComponentDescriptor:
        _exact_keys(value, {"adapter", "source_id", "frames", "settings"}, "component")
        frames = value["frames"]
        settings = value["settings"]
        if not isinstance(frames, Mapping) or not isinstance(settings, Mapping):
            raise ValueError("component.frames 和 settings 必须是对象")
        return cls(
            str(value["adapter"]),
            str(value["source_id"]),
            {str(k): str(v) for k, v in frames.items()},
            dict(cast(Mapping[str, JsonValue], settings)),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionDescriptor:
    camera: ComponentDescriptor
    flange: ComponentDescriptor
    target: ComponentDescriptor

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "camera": self.camera.as_dict(),
            "flange": self.flange.as_dict(),
            "target": self.target.as_dict(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> AcquisitionDescriptor:
        _exact_keys(value, {"camera", "flange", "target"}, "acquisition")
        camera = value["camera"]
        flange = value["flange"]
        target = value["target"]
        if not all(isinstance(item, Mapping) for item in (camera, flange, target)):
            raise ValueError("acquisition 子项必须是对象")
        return cls(
            ComponentDescriptor.from_mapping(cast(Mapping[str, object], camera)),
            ComponentDescriptor.from_mapping(cast(Mapping[str, object], flange)),
            ComponentDescriptor.from_mapping(cast(Mapping[str, object], target)),
        )


@dataclass(frozen=True, slots=True)
class SynchronizedObservation:
    captured_at: str
    intrinsics: CameraIntrinsics
    base_to_flange: RigidTransform
    camera_to_target: RigidTransform
    camera_stamp: CaptureStamp
    flange_before_stamp: CaptureStamp
    flange_after_stamp: CaptureStamp
    translation_drift_m: float
    rotation_drift_deg: float
    detection_metrics: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not str(self.captured_at).strip():
            raise ValueError("captured_at 必须是非空字符串")
        object.__setattr__(self, "captured_at", str(self.captured_at).strip())
        object.__setattr__(
            self, "translation_drift_m", _finite(self.translation_drift_m, "translation_drift_m")
        )
        object.__setattr__(
            self, "rotation_drift_deg", _finite(self.rotation_drift_deg, "rotation_drift_deg")
        )
        if self.translation_drift_m < 0.0 or self.rotation_drift_deg < 0.0:
            raise ValueError("漂移量不得为负数")
        if self.base_to_flange.parent_frame != "base" or self.base_to_flange.child_frame != "flange":
            raise ValueError("base_to_flange 坐标合同必须是 base <- flange")
        if self.camera_to_target.parent_frame != "camera" or self.camera_to_target.child_frame != "target":
            raise ValueError("camera_to_target 坐标合同必须是 camera <- target")
        object.__setattr__(self, "detection_metrics", _json_mapping(self.detection_metrics))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "captured_at": self.captured_at,
            "intrinsics": self.intrinsics.as_dict(),
            "base_to_flange": self.base_to_flange.as_dict(),
            "camera_to_target": self.camera_to_target.as_dict(),
            "camera_stamp": self.camera_stamp.as_dict(),
            "flange_before_stamp": self.flange_before_stamp.as_dict(),
            "flange_after_stamp": self.flange_after_stamp.as_dict(),
            "translation_drift_m": self.translation_drift_m,
            "rotation_drift_deg": self.rotation_drift_deg,
            "detection_metrics": _json_dict(self.detection_metrics),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SynchronizedObservation:
        _exact_keys(
            value,
            {
                "captured_at",
                "intrinsics",
                "base_to_flange",
                "camera_to_target",
                "camera_stamp",
                "flange_before_stamp",
                "flange_after_stamp",
                "translation_drift_m",
                "rotation_drift_deg",
                "detection_metrics",
            },
            "observation",
        )
        intrinsics = value["intrinsics"]
        base_to_flange = value["base_to_flange"]
        camera_to_target = value["camera_to_target"]
        camera_stamp = value["camera_stamp"]
        flange_before_stamp = value["flange_before_stamp"]
        flange_after_stamp = value["flange_after_stamp"]
        detection_metrics = value["detection_metrics"]
        mappings = (
            intrinsics,
            base_to_flange,
            camera_to_target,
            camera_stamp,
            flange_before_stamp,
            flange_after_stamp,
            detection_metrics,
        )
        if not all(isinstance(item, Mapping) for item in mappings):
            raise ValueError("observation 子对象无效")
        return cls(
            str(value["captured_at"]),
            CameraIntrinsics.from_mapping(cast(Mapping[str, object], intrinsics)),
            RigidTransform.from_mapping(cast(Mapping[str, object], base_to_flange)),
            RigidTransform.from_mapping(cast(Mapping[str, object], camera_to_target)),
            CaptureStamp.from_mapping(cast(Mapping[str, object], camera_stamp)),
            CaptureStamp.from_mapping(cast(Mapping[str, object], flange_before_stamp)),
            CaptureStamp.from_mapping(cast(Mapping[str, object], flange_after_stamp)),
            _finite(value["translation_drift_m"], "translation_drift_m"),
            _finite(value["rotation_drift_deg"], "rotation_drift_deg"),
            dict(cast(Mapping[str, JsonValue], detection_metrics)),
        )


class PoseObservation(Protocol):
    """求解器所需的最小位姿观测合同。"""

    @property
    def base_to_flange(self) -> RigidTransform: ...

    @property
    def camera_to_target(self) -> RigidTransform: ...


@dataclass(frozen=True, slots=True)
class SampleRecord:
    sample_id: str
    role: SampleRole
    included: bool
    exclusion_reason: str | None
    hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _safe_id(self.sample_id, "sample_id"))
        object.__setattr__(self, "role", SampleRole.parse(self.role))
        if not isinstance(self.included, bool):
            raise ValueError("included 必须是布尔值")
        reason = None if self.exclusion_reason is None else str(self.exclusion_reason).strip()
        if not self.included and not reason:
            raise ValueError("排除样本必须填写原因")
        object.__setattr__(self, "exclusion_reason", reason)
        normalized = {str(name): str(digest) for name, digest in self.hashes.items()}
        if set(normalized) != {"color.png", "overlay.png", "observation.json"}:
            raise ValueError("样本哈希成员不完整")
        if any(len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) for digest in normalized.values()):
            raise ValueError("样本哈希必须是小写 SHA-256")
        object.__setattr__(self, "hashes", MappingProxyType(normalized))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.sample_id,
            "role": self.role.value,
            "included": self.included,
            "exclusion_reason": self.exclusion_reason,
            "hashes": dict(self.hashes),
        }


@dataclass(slots=True)
class RunRecord:
    run_id: str
    created_at: str
    updated_at: str
    state: RunState
    plan: "CalibrationPlan"
    acquisition: AcquisitionDescriptor
    confirmations: dict[str, JsonValue]
    samples: list[SampleRecord]
    outputs: dict[str, str | None]
    last_error: str | None = None

    def __post_init__(self) -> None:
        self.run_id = _safe_id(self.run_id, "run_id")
        self.created_at = _identifier(self.created_at, "created_at")
        self.updated_at = _identifier(self.updated_at, "updated_at")
        self.state = RunState(self.state)
        if set(self.confirmations) != {"safety", "target"}:
            raise ValueError("confirmations 字段无效")
        if set(self.outputs) != {"result", "local_report", "artifact"}:
            raise ValueError("outputs 字段无效")
        for name, value in self.outputs.items():
            if value is not None and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value
            ) is None:
                raise ValueError(f"outputs.{name} 必须是安全的文件名或 null")
        if len({sample.sample_id for sample in self.samples}) != len(self.samples):
            raise ValueError("samples 包含重复 ID")
        if self.last_error is not None:
            self.last_error = str(self.last_error)


@dataclass(frozen=True, slots=True)
class QualitySummary:
    passed: bool
    reasons: tuple[str, ...]
    method: str
    sample_counts: Mapping[str, int]
    validation_rms: Mapping[str, float]
    validation_p95: Mapping[str, float]
    coverage: Mapping[str, JsonValue]
    uncertainty: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("quality.passed 必须是布尔值")
        object.__setattr__(self, "method", str(self.method).strip())
        if not self.method:
            raise ValueError("quality.method 不能为空")
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        if self.passed == bool(self.reasons):
            raise ValueError("quality.passed 与 reasons 不一致")
        if set(self.sample_counts) != {"calibration", "validation"} or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in self.sample_counts.values()
        ):
            raise ValueError("quality.sample_counts 无效")
        metric_keys = {"translation_m", "rotation_deg"}
        normalized_metrics: dict[str, Mapping[str, float]] = {}
        for name, values in (
            ("validation_rms", self.validation_rms),
            ("validation_p95", self.validation_p95),
        ):
            if set(values) != metric_keys:
                raise ValueError(f"quality.{name} 字段无效")
            normalized = {key: _finite(value, f"quality.{name}.{key}") for key, value in values.items()}
            if any(value < 0.0 for value in normalized.values()):
                raise ValueError(f"quality.{name} 不得为负数")
            normalized_metrics[name] = MappingProxyType(normalized)
        coverage_keys = {
            "position_span_m",
            "rotation_span_deg",
            "nonparallel_axes",
            "duplicate_count",
        }
        if set(self.coverage) != coverage_keys:
            raise ValueError("quality.coverage 字段无效")
        uncertainty_keys = {
            "translation_p95_m",
            "rotation_p95_deg",
            "successes",
            "failures",
        }
        if set(self.uncertainty) != uncertainty_keys:
            raise ValueError("quality.uncertainty 字段无效")
        normalized_uncertainty = {
            key: _finite(value, f"quality.uncertainty.{key}")
            for key, value in self.uncertainty.items()
        }
        if any(value < 0.0 for value in normalized_uncertainty.values()):
            raise ValueError("quality.uncertainty 不得为负数")
        object.__setattr__(self, "sample_counts", MappingProxyType(dict(self.sample_counts)))
        object.__setattr__(self, "validation_rms", normalized_metrics["validation_rms"])
        object.__setattr__(self, "validation_p95", normalized_metrics["validation_p95"])
        object.__setattr__(self, "coverage", _json_mapping(self.coverage))
        object.__setattr__(self, "uncertainty", MappingProxyType(normalized_uncertainty))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "method": self.method,
            "sample_counts": dict(self.sample_counts),
            "validation_rms": dict(self.validation_rms),
            "validation_p95": dict(self.validation_p95),
            "coverage": _json_dict(self.coverage),
            "uncertainty": dict(self.uncertainty),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> QualitySummary:
        _exact_keys(
            value,
            {
                "passed",
                "reasons",
                "method",
                "sample_counts",
                "validation_rms",
                "validation_p95",
                "coverage",
                "uncertainty",
            },
            "quality",
        )
        reasons = value["reasons"]
        if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
            raise ValueError("quality.reasons 必须是数组")
        objects: dict[str, Mapping[object, object]] = {}
        for name in (
            "sample_counts",
            "validation_rms",
            "validation_p95",
            "coverage",
            "uncertainty",
        ):
            item = value[name]
            if not isinstance(item, Mapping):
                raise ValueError(f"quality.{name} 必须是对象")
            objects[name] = cast(Mapping[object, object], item)
        return cls(
            passed=cast(bool, value["passed"]),
            reasons=tuple(str(item) for item in reasons),
            method=str(value["method"]),
            sample_counts={
                str(k): int(cast(Any, v)) for k, v in objects["sample_counts"].items()
            },
            validation_rms={
                str(k): float(cast(Any, v))
                for k, v in objects["validation_rms"].items()
            },
            validation_p95={
                str(k): float(cast(Any, v))
                for k, v in objects["validation_p95"].items()
            },
            coverage=dict(cast(Mapping[str, JsonValue], objects["coverage"])),
            uncertainty={
                str(k): float(cast(Any, v))
                for k, v in objects["uncertainty"].items()
            },
        )


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    created_at: str
    run_id: str
    mode: CalibrationMode
    transform: RigidTransform
    quality: QualitySummary
    diagnostics: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _identifier(self.created_at, "created_at"))
        object.__setattr__(self, "run_id", _safe_id(self.run_id, "run_id"))
        object.__setattr__(self, "mode", CalibrationMode.parse(self.mode))
        expected_parent = "base" if self.mode is CalibrationMode.EYE_TO_HAND else "flange"
        if (self.transform.parent_frame, self.transform.child_frame) != (
            expected_parent,
            "camera",
        ):
            raise ValueError("result.transform 与标定模式的坐标合同不一致")
        object.__setattr__(self, "diagnostics", _json_mapping(self.diagnostics))

    def as_dict(self, *, include_diagnostics: bool = True) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "created_at": self.created_at,
            "tool": {"name": "handeye-toolkit"},
            "run_id": self.run_id,
            "mode": self.mode.value,
            "transform": self.transform.as_dict(),
            "quality": self.quality.as_dict(),
        }
        if include_diagnostics:
            value["diagnostics"] = _json_dict(self.diagnostics)
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CalibrationResult:
        _exact_keys(
            value,
            {
                "created_at",
                "tool",
                "run_id",
                "mode",
                "transform",
                "quality",
                "diagnostics",
            },
            "result",
        )
        objects: dict[str, Mapping[str, object]] = {}
        for name in ("tool", "transform", "quality", "diagnostics"):
            item = value[name]
            if not isinstance(item, Mapping):
                raise ValueError(f"result.{name} 必须是对象")
            objects[name] = cast(Mapping[str, object], item)
        tool = objects["tool"]
        if set(tool) != {"name"} or tool["name"] != "handeye-toolkit":
            raise ValueError("result.tool 无效")
        return cls(
            created_at=str(value["created_at"]),
            run_id=str(value["run_id"]),
            mode=CalibrationMode.parse(str(value["mode"])),
            transform=RigidTransform.from_mapping(objects["transform"]),
            quality=QualitySummary.from_mapping(objects["quality"]),
            diagnostics=dict(cast(Mapping[str, JsonValue], objects["diagnostics"])),
        )


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} 字段不符合 schema：缺少 {sorted(expected - actual)}；"
            f"多出 {sorted(actual - expected)}"
        )


from .policy import CalibrationPlan  # noqa: E402  # 避免运行时循环导入

__all__ = [
    "AcquisitionDescriptor",
    "CalibrationMode",
    "CalibrationResult",
    "CameraFrame",
    "CameraIntrinsics",
    "CaptureStamp",
    "ComponentDescriptor",
    "DetectionQuality",
    "FlangePose",
    "JsonValue",
    "QualitySummary",
    "PoseObservation",
    "RigidTransform",
    "RunRecord",
    "RunState",
    "SampleRecord",
    "SampleRole",
    "SynchronizedObservation",
    "TargetDetection",
    "TargetIdentity",
]
