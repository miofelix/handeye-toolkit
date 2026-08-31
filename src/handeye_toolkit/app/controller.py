"""CLI 与 GUI 共用的薄控制器。"""

from __future__ import annotations

from pathlib import Path

from ..application import CalibrationRun, CaptureCandidate, PoseAssessment, RunSnapshot
from ..domain import CalibrationResult, SampleRecord, SampleRole


class CalibrationController:
    def __init__(self, run: CalibrationRun) -> None:
        self.run = run

    @property
    def snapshot(self) -> RunSnapshot:
        return self.run.snapshot

    def acknowledge_and_connect(self) -> None:
        self.run.acknowledge_safety()
        self.run.connect()

    def current_role(self) -> SampleRole | None:
        snapshot = self.snapshot
        if snapshot.calibration_count < snapshot.calibration_target:
            return SampleRole.CALIBRATION
        if snapshot.validation_count < snapshot.validation_target:
            return SampleRole.VALIDATION
        return None

    def assess_pose(self) -> PoseAssessment:
        role = self.current_role()
        if role is None:
            raise RuntimeError("采集目标已经达到")
        return self.run.assess_pose(role)

    def capture(self) -> CaptureCandidate:
        role = self.current_role()
        if role is None:
            raise RuntimeError("采集目标已经达到")
        return self.run.capture_candidate(role)

    def accept(self, candidate_id: str, *, confirm_target: bool = False) -> SampleRecord:
        return self.run.accept_candidate(candidate_id, confirm_target=confirm_target)

    def reject(self, candidate_id: str, reason: str) -> None:
        self.run.reject_candidate(candidate_id, reason)

    def solve(self) -> CalibrationResult:
        return self.run.solve()

    def export(self, output_path: str | Path | None = None) -> Path:
        return self.run.export(output_path)

    def close(self) -> None:
        self.run.close()


__all__ = ["CalibrationController"]
