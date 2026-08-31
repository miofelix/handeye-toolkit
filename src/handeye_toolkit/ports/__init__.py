"""应用层依赖的最小端口；实现位于算法或适配器层。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol, runtime_checkable

from ..domain import (
    AcquisitionDescriptor,
    CalibrationPlan,
    CalibrationResult,
    CameraFrame,
    FlangePose,
    PoseObservation,
    RunRecord,
    SampleRecord,
    SynchronizedObservation,
    TargetDetection,
)


@runtime_checkable
class Camera(Protocol):
    def open(self) -> None: ...

    def capture(self) -> CameraFrame: ...

    def close(self) -> None: ...


@runtime_checkable
class ReadOnlyFlangeSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> FlangePose: ...

    def close(self) -> None: ...


@runtime_checkable
class TargetDetector(Protocol):
    def detect(self, frame: CameraFrame) -> TargetDetection: ...


@dataclass(slots=True)
class AcquisitionRig:
    descriptor: AcquisitionDescriptor
    camera: Camera
    flange: ReadOnlyFlangeSource
    detector: TargetDetector


class RigFactory(Protocol):
    @property
    def descriptor(self) -> AcquisitionDescriptor: ...

    def create(self, plan: CalibrationPlan) -> AcquisitionRig: ...


class CalibrationSolver(Protocol):
    def solve(
        self,
        *,
        run_id: str,
        plan: CalibrationPlan,
        samples: Sequence[tuple[SampleRecord, PoseObservation]],
        target_confirmed: bool,
    ) -> CalibrationResult: ...


class RunRepository(Protocol):
    def lock(self, path: Path) -> ContextManager[None]: ...

    def create(
        self,
        *,
        plan: CalibrationPlan,
        acquisition: AcquisitionDescriptor,
        output_root: str | Path,
    ) -> tuple[Path, RunRecord]: ...

    def load(self, run_ref: str | Path) -> tuple[Path, RunRecord]: ...

    def save(self, path: Path, record: RunRecord) -> None: ...

    def add_sample(
        self,
        path: Path,
        record: RunRecord,
        *,
        observation: SynchronizedObservation,
        color_bgr: object,
        overlay_bgr: object,
        role: str,
    ) -> SampleRecord: ...

    def load_observation(self, path: Path, sample: SampleRecord) -> SynchronizedObservation: ...

    def verify(self, path: Path, record: RunRecord) -> list[str]: ...

    def write_result(self, path: Path, result: CalibrationResult) -> Path: ...

    def load_result(self, path: Path) -> CalibrationResult: ...


class ArtifactExporter(Protocol):
    def export(
        self,
        *,
        run_path: Path,
        record: RunRecord,
        result: CalibrationResult,
        observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
        output_path: str | Path | None = None,
    ) -> Path: ...


class ReportRenderer(Protocol):
    def render_local(
        self,
        *,
        run_path: Path,
        record: RunRecord,
        result: CalibrationResult,
        observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
    ) -> Path: ...


EventSink = Callable[[object], None]

__all__ = [
    "AcquisitionRig",
    "ArtifactExporter",
    "CalibrationSolver",
    "Camera",
    "EventSink",
    "ReadOnlyFlangeSource",
    "ReportRenderer",
    "RigFactory",
    "RunRepository",
    "TargetDetector",
]
