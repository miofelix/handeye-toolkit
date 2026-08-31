"""应用命令的返回值和统一 UI 事件。"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import (
    CameraFrame,
    RunState,
    SampleRole,
    SynchronizedObservation,
    TargetDetection,
)


@dataclass(frozen=True, slots=True)
class PoseAssessment:
    stable: bool
    novel: bool
    translation_drift_m: float
    rotation_drift_deg: float
    position_span_m: float
    rotation_span_deg: float
    suggestions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaptureCandidate:
    candidate_id: str
    role: SampleRole
    frame: CameraFrame
    detection: TargetDetection
    observation: SynchronizedObservation | None
    pose: PoseAssessment
    reasons: tuple[str, ...]

    @property
    def acceptable(self) -> bool:
        return self.observation is not None and not self.reasons


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    state: RunState
    run_path: str
    hardware_connected: bool
    calibration_count: int
    calibration_target: int
    validation_count: int
    validation_target: int
    quality_passed: bool | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: str
    message: str
    snapshot: RunSnapshot


__all__ = ["CaptureCandidate", "PoseAssessment", "RunEvent", "RunSnapshot"]
