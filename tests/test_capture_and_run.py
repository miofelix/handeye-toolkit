from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from conftest import standard_plan, transform

from handeye_toolkit.adapters.filesystem import FileRunRepository
from handeye_toolkit.app.controller import CalibrationController
from handeye_toolkit.application import CalibrationRun, CaptureCoordinator
from handeye_toolkit.domain import (
    AcquisitionDescriptor,
    CalibrationMode,
    CalibrationResult,
    CameraFrame,
    CameraIntrinsics,
    CaptureStamp,
    ComponentDescriptor,
    DetectionQuality,
    FlangePose,
    QualitySummary,
    RigidTransform,
    RunState,
    SampleRole,
    TargetDetection,
    TargetIdentity,
)
from handeye_toolkit.ports import AcquisitionRig


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000

    def stamp(self) -> CaptureStamp:
        started = self.value
        self.value += 10
        return CaptureStamp(started, started + 5)


class FakeCamera:
    def __init__(self, clock: FakeClock, *, fail_open: bool = False) -> None:
        self.clock = clock
        self.fail_open = fail_open
        self.opened = False
        self.closed = False
        self.invalid_interval = False

    def open(self) -> None:
        self.opened = True
        if self.fail_open:
            raise RuntimeError("模拟相机连接失败")

    def capture(self) -> CameraFrame:
        stamp = self.clock.stamp()
        if self.invalid_interval:
            stamp = CaptureStamp(0, 1)
        return CameraFrame(
            np.zeros((8, 10, 3), dtype=np.uint8),
            CameraIntrinsics(500.0, 500.0, 5.0, 4.0),
            stamp,
        )

    def close(self) -> None:
        self.closed = True
        self.opened = False


class FakeFlange:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.matrix = np.eye(4, dtype=np.float64)
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def read(self) -> FlangePose:
        return FlangePose(
            RigidTransform("base", "flange", self.matrix),
            self.clock.stamp(),
            {"read_only": True},
        )

    def close(self) -> None:
        self.closed = True
        self.opened = False


class FakeDetector:
    def __init__(self) -> None:
        self.with_transform = True

    def detect(self, frame: CameraFrame) -> TargetDetection:
        return TargetDetection(
            transform=(
                RigidTransform(
                    "camera",
                    "target",
                    transform([0.1, -0.05, 0.03], [0.0, 0.0, 0.5]),
                )
                if self.with_transform
                else None
            ),
            overlay_bgr=frame.color_bgr,
            quality=DetectionQuality(True, (), (48.0,), {"corner_count": 48}),
            identity=TargetIdentity(
                "charuco-configured",
                "charuco-configured",
                True,
                "b" * 64,
            ),
        )


class FakeFactory:
    def __init__(
        self,
        descriptor: AcquisitionDescriptor,
        camera: FakeCamera,
        flange: FakeFlange,
        detector: FakeDetector,
    ) -> None:
        self._descriptor = descriptor
        self.camera = camera
        self.flange = flange
        self.detector = detector

    @property
    def descriptor(self) -> AcquisitionDescriptor:
        return self._descriptor

    def create(self, _plan) -> AcquisitionRig:
        return AcquisitionRig(
            self.descriptor,
            self.camera,
            self.flange,
            self.detector,
        )


class FakeSolver:
    def solve(self, *, run_id, plan, samples, target_confirmed) -> CalibrationResult:
        calibration = sum(
            sample.included and sample.role is SampleRole.CALIBRATION
            for sample, _observation in samples
        )
        validation = sum(
            sample.included and sample.role is SampleRole.VALIDATION
            for sample, _observation in samples
        )
        assert target_confirmed
        parent = "base" if plan.mode is CalibrationMode.EYE_TO_HAND else "flange"
        return CalibrationResult(
            created_at="2026-08-30T00:00:00.000Z",
            run_id=run_id,
            mode=plan.mode,
            transform=RigidTransform(parent, "camera", np.eye(4)),
            quality=QualitySummary(
                passed=True,
                reasons=(),
                method="Fake",
                sample_counts={"calibration": calibration, "validation": validation},
                validation_rms={"translation_m": 0.0, "rotation_deg": 0.0},
                validation_p95={"translation_m": 0.0, "rotation_deg": 0.0},
                coverage={
                    "position_span_m": 0.1,
                    "rotation_span_deg": 30.0,
                    "nonparallel_axes": True,
                    "duplicate_count": 0,
                },
                uncertainty={
                    "translation_p95_m": 0.0,
                    "rotation_p95_deg": 0.0,
                    "successes": 0.0,
                    "failures": 0.0,
                },
            ),
            diagnostics={},
        )


class FakeReporter:
    def render_local(self, *, run_path, record, result, observations) -> Path:
        selected = run_path / "report.local.html"
        selected.write_text("<html lang='zh-CN'></html>", encoding="utf-8")
        return selected


class FakeExporter:
    def export(
        self,
        *,
        run_path,
        record,
        result,
        observations,
        output_path=None,
    ) -> Path:
        selected = Path(output_path or run_path / f"artifact_{record.run_id}.zip").resolve()
        selected.write_bytes(b"fake-artifact")
        return selected


def small_plan():
    plan = standard_plan(CalibrationMode.EYE_TO_HAND)
    return replace(
        plan,
        profile="test@1",
        sampling=replace(
            plan.sampling,
            calibration_target=1,
            validation_target=1,
            candidate_frames=1,
            stability_before_s=0.001,
            stability_after_s=0.001,
            feedback_hz=10.0,
        ),
        solver=replace(plan.solver, bootstrap_iterations=0),
    )


def descriptor(plan) -> AcquisitionDescriptor:
    return AcquisitionDescriptor(
        ComponentDescriptor("fake-camera", "camera-placeholder", {"camera": "camera"}, {}),
        ComponentDescriptor(
            "fake-flange",
            "channel-placeholder",
            {"base": "base", "flange": "flange"},
            {"allow_robot_control": False},
        ),
        ComponentDescriptor(
            "charuco",
            "target-placeholder",
            {"target": "target"},
            plan.target.parameters,
        ),
    )


def make_components(plan, *, fail_open: bool = False):
    clock = FakeClock()
    camera = FakeCamera(clock, fail_open=fail_open)
    flange = FakeFlange(clock)
    detector = FakeDetector()
    factory = FakeFactory(descriptor(plan), camera, flange, detector)
    return factory, camera, flange, detector


def test_capture_never_fabricates_missing_target_pose() -> None:
    plan = small_plan()
    factory, _camera, _flange, detector = make_components(plan)
    detector.with_transform = False
    rig = factory.create(plan)
    coordinator = CaptureCoordinator(rig, plan, sleep_fn=lambda _seconds: None)
    candidate = coordinator.capture(SampleRole.CALIBRATION, [])

    assert candidate.observation is None
    assert not candidate.acceptable
    assert any("未提供 camera <- target" in reason for reason in candidate.reasons)


def test_capture_requires_bracketing_host_timestamps() -> None:
    plan = small_plan()
    factory, camera, _flange, _detector = make_components(plan)
    camera.invalid_interval = True
    candidate = CaptureCoordinator(
        factory.create(plan),
        plan,
        sleep_fn=lambda _seconds: None,
    ).capture(SampleRole.CALIBRATION, [])
    assert not candidate.acceptable
    assert any("包围相机采集区间" in reason for reason in candidate.reasons)


def test_shared_run_controller_completes_state_machine_and_closes_export(tmp_path: Path) -> None:
    plan = small_plan()
    factory, camera, flange, _detector = make_components(plan)
    repository = FileRunRepository()
    run = CalibrationRun.create(
        plan=plan,
        factory=factory,
        repository=repository,
        solver=FakeSolver(),
        exporter=FakeExporter(),
        reporter=FakeReporter(),
        output_root=tmp_path,
        capture_sleep_fn=lambda _seconds: None,
    )
    controller = CalibrationController(run)
    controller.acknowledge_and_connect()

    first = controller.capture()
    controller.accept(first.candidate_id, confirm_target=True)
    flange.matrix = transform([0.0, 0.0, 0.1], [0.02, 0.0, 0.0])
    second = controller.capture()
    controller.accept(second.candidate_id)
    assert controller.snapshot.state is RunState.READY_TO_SOLVE

    result = controller.solve()
    assert result.quality.passed
    assert camera.closed and flange.closed
    artifact = controller.export()
    assert artifact.is_file()
    assert controller.snapshot.state is RunState.EXPORTED
    # 导出是可重试操作，可恢复 EXPORTED 状态不会进入错误分支。
    assert controller.export() == artifact
    run_path = run.path
    controller.close()

    _, saved = repository.load(run_path)
    assert saved.state is RunState.CLOSED
    with pytest.raises(RuntimeError, match="已关闭"):
        CalibrationRun.resume(
            run_path,
            factory=factory,
            repository=repository,
            solver=FakeSolver(),
            exporter=FakeExporter(),
            reporter=FakeReporter(),
        )


def test_connect_failure_closes_both_partial_components_and_persists_error(
    tmp_path: Path,
) -> None:
    plan = small_plan()
    factory, camera, flange, _detector = make_components(plan, fail_open=True)
    repository = FileRunRepository()
    run = CalibrationRun.create(
        plan=plan,
        factory=factory,
        repository=repository,
        solver=FakeSolver(),
        exporter=FakeExporter(),
        reporter=FakeReporter(),
        output_root=tmp_path,
    )
    run.acknowledge_safety()
    with pytest.raises(RuntimeError, match="模拟相机连接失败"):
        run.connect()
    assert camera.closed and flange.closed
    _, saved = repository.load(run.path)
    assert saved.last_error == "模拟相机连接失败"
    run.close()


def test_validation_pose_must_be_distinct_from_calibration_pose(tmp_path: Path) -> None:
    plan = small_plan()
    factory, _camera, _flange, _detector = make_components(plan)
    run = CalibrationRun.create(
        plan=plan,
        factory=factory,
        repository=FileRunRepository(),
        solver=FakeSolver(),
        exporter=FakeExporter(),
        reporter=FakeReporter(),
        output_root=tmp_path,
        capture_sleep_fn=lambda _seconds: None,
    )
    run.acknowledge_safety()
    run.connect()
    first = run.capture_candidate(SampleRole.CALIBRATION)
    run.accept_candidate(first.candidate_id, confirm_target=True)
    with pytest.raises(RuntimeError, match="重复"):
        run.capture_candidate(SampleRole.VALIDATION)
    run.close()
