"""单一、可恢复的手眼标定应用状态机。"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain import (
    CalibrationPlan,
    CalibrationResult,
    RunRecord,
    RunState,
    SampleRecord,
    SampleRole,
    SynchronizedObservation,
)
from ..ports import (
    AcquisitionRig,
    ArtifactExporter,
    CalibrationSolver,
    EventSink,
    ReportRenderer,
    RigFactory,
    RunRepository,
)
from .capture import CaptureCoordinator, CaptureRejected
from .models import CaptureCandidate, PoseAssessment, RunEvent, RunSnapshot


class CalibrationRun:
    """CLI、GUI 和嵌入调用方共享的唯一运行门面。"""

    def __init__(
        self,
        *,
        path: Path,
        record: RunRecord,
        factory: RigFactory,
        repository: RunRepository,
        solver: CalibrationSolver,
        exporter: ArtifactExporter,
        reporter: ReportRenderer,
        event_sink: EventSink | None = None,
        capture_sleep_fn: Any | None = None,
    ) -> None:
        self.path = path
        self.factory = factory
        self.repository = repository
        self.solver = solver
        self.exporter = exporter
        self.reporter = reporter
        self.event_sink = event_sink
        self.capture_sleep_fn = capture_sleep_fn
        self.rig: AcquisitionRig | None = None
        self.capture_coordinator: CaptureCoordinator | None = None
        self._pending: dict[str, CaptureCandidate] = {}
        self._safety_acknowledged = False
        self._result: CalibrationResult | None = None
        self._closed = False
        self._lease = repository.lock(path)
        self._lease.__enter__()
        try:
            locked_path, locked_record = repository.load(path)
            if locked_record.run_id != record.run_id:
                raise RuntimeError("加锁后任务记录已发生替换")
            if factory.descriptor != locked_record.acquisition:
                raise ValueError("采集工厂描述与任务记录不一致")
            self.path = locked_path
            self.record = locked_record
            if locked_record.outputs.get("result"):
                self._result = repository.load_result(locked_path)
        except BaseException:
            self._closed = True
            self._lease.__exit__(None, None, None)
            raise

    @classmethod
    def create(
        cls,
        *,
        plan: CalibrationPlan,
        factory: RigFactory,
        repository: RunRepository,
        solver: CalibrationSolver,
        exporter: ArtifactExporter,
        reporter: ReportRenderer,
        output_root: str | Path = "runs",
        event_sink: EventSink | None = None,
        capture_sleep_fn: Any | None = None,
    ) -> CalibrationRun:
        path, record = repository.create(
            plan=plan,
            acquisition=factory.descriptor,
            output_root=output_root,
        )
        run = cls(
            path=path,
            record=record,
            factory=factory,
            repository=repository,
            solver=solver,
            exporter=exporter,
            reporter=reporter,
            event_sink=event_sink,
            capture_sleep_fn=capture_sleep_fn,
        )
        run._emit("created", "已创建标定任务")
        return run

    @classmethod
    def resume(
        cls,
        run_ref: str | Path,
        *,
        factory: RigFactory,
        repository: RunRepository,
        solver: CalibrationSolver,
        exporter: ArtifactExporter,
        reporter: ReportRenderer,
        event_sink: EventSink | None = None,
        capture_sleep_fn: Any | None = None,
    ) -> CalibrationRun:
        path, record = repository.load(run_ref)
        if record.state is RunState.CLOSED:
            raise RuntimeError("任务已关闭，不能恢复")
        run = cls(
            path=path,
            record=record,
            factory=factory,
            repository=repository,
            solver=solver,
            exporter=exporter,
            reporter=reporter,
            event_sink=event_sink,
            capture_sleep_fn=capture_sleep_fn,
        )
        if run.record.state is RunState.CLOSED:
            run.close()
            raise RuntimeError("任务已关闭，不能恢复")
        run._emit("resumed", "已恢复标定任务")
        return run

    @property
    def plan(self) -> CalibrationPlan:
        return self.record.plan

    @property
    def result(self) -> CalibrationResult | None:
        return self._result

    @property
    def snapshot(self) -> RunSnapshot:
        calibration = self._count(SampleRole.CALIBRATION)
        validation = self._count(SampleRole.VALIDATION)
        return RunSnapshot(
            state=self.record.state,
            run_path=str(self.path),
            hardware_connected=self.rig is not None,
            calibration_count=calibration,
            calibration_target=self.plan.sampling.calibration_target,
            validation_count=validation,
            validation_target=self.plan.sampling.validation_target,
            quality_passed=None if self._result is None else self._result.quality.passed,
            last_error=self.record.last_error,
        )

    def _emit(self, kind: str, message: str) -> None:
        if self.event_sink is not None:
            self.event_sink(RunEvent(kind, message, self.snapshot))

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("任务门面已经关闭")

    def _count(self, role: SampleRole) -> int:
        return sum(
            1 for sample in self.record.samples if sample.role is role and sample.included
        )

    def _refresh_collection_state(self) -> None:
        if self._count(SampleRole.CALIBRATION) < self.plan.sampling.calibration_target:
            self.record.state = RunState.COLLECTING_CALIBRATION
        elif self._count(SampleRole.VALIDATION) < self.plan.sampling.validation_target:
            self.record.state = RunState.COLLECTING_VALIDATION
        else:
            self.record.state = RunState.READY_TO_SOLVE

    def _all_observations(
        self, *, included_only: bool = False
    ) -> list[tuple[SampleRecord, SynchronizedObservation]]:
        result: list[tuple[SampleRecord, SynchronizedObservation]] = []
        for sample in self.record.samples:
            if included_only and not sample.included:
                continue
            result.append((sample, self.repository.load_observation(self.path, sample)))
        return result

    def acknowledge_safety(self) -> None:
        self._ensure_open()
        events = self.record.confirmations.get("safety")
        if not isinstance(events, list):
            raise ValueError("任务安全确认记录无效")
        events.append({"confirmed_at": _utc_now()})
        try:
            self.repository.save(self.path, self.record)
        except BaseException:
            events.pop()
            raise
        self._safety_acknowledged = True
        self._emit("safety-acknowledged", "已记录本次只读安全确认")

    def connect(self) -> None:
        self._ensure_open()
        if not self._safety_acknowledged:
            raise RuntimeError("连接 Piper 前必须完成本次安全确认")
        if self.rig is not None:
            return
        if self.record.state in {RunState.CLOSED, RunState.SOLVED, RunState.EXPORTED}:
            raise RuntimeError(f"当前任务状态不能连接采集源：{self.record.state.value}")
        rig = self.factory.create(self.plan)
        if rig.descriptor != self.record.acquisition:
            raise ValueError("采集组件描述与任务记录不一致")
        try:
            rig.camera.open()
            rig.flange.open()
        except BaseException as exc:
            # open() 失败时组件也可能已经分配了部分资源，因此两端都尝试关闭。
            with contextlib.suppress(Exception):
                rig.flange.close()
            with contextlib.suppress(Exception):
                rig.camera.close()
            self.record.last_error = str(exc)
            self.repository.save(self.path, self.record)
            self._emit("hardware-error", "采集源连接失败")
            raise
        self.rig = rig
        kwargs = {} if self.capture_sleep_fn is None else {"sleep_fn": self.capture_sleep_fn}
        self.capture_coordinator = CaptureCoordinator(rig, self.plan, **kwargs)
        self.record.last_error = None
        self._refresh_collection_state()
        self.repository.save(self.path, self.record)
        self._emit("hardware-connected", "相机与 Piper 只读反馈已连接")

    def disconnect(self) -> None:
        rig = self.rig
        self.rig = None
        self.capture_coordinator = None
        if rig is None:
            return
        try:
            rig.flange.close()
        finally:
            rig.camera.close()
        self._emit("hardware-disconnected", "采集源已断开")

    def assess_pose(self, role: SampleRole | str) -> PoseAssessment:
        self._ensure_open()
        if self.capture_coordinator is None:
            raise RuntimeError("采集源尚未连接")
        normalized = SampleRole.parse(role)
        accepted = [
            observation
            for _sample, observation in self._all_observations(included_only=True)
        ]
        assessment = self.capture_coordinator.assess_pose(normalized, accepted)
        self._emit("pose-assessed", "已评估当前手动姿态")
        return assessment

    def capture_candidate(self, role: SampleRole | str) -> CaptureCandidate:
        self._ensure_open()
        if self.capture_coordinator is None:
            raise RuntimeError("采集源尚未连接")
        normalized = SampleRole.parse(role)
        accepted = [
            observation
            for _sample, observation in self._all_observations(included_only=True)
        ]
        try:
            candidate = self.capture_coordinator.capture(normalized, accepted)
        except CaptureRejected:
            self._emit("capture-rejected", "当前姿态或时序不满足采样前提")
            raise
        self._pending[candidate.candidate_id] = candidate
        self._emit("candidate-ready", "已生成待确认候选样本")
        return candidate

    def accept_candidate(
        self, candidate_id: str, *, confirm_target: bool = False
    ) -> SampleRecord:
        self._ensure_open()
        try:
            candidate = self._pending[candidate_id]
        except KeyError as exc:
            raise KeyError("候选样本不存在或已经处理") from exc
        if not candidate.acceptable:
            raise ValueError("候选样本未通过质量检查：" + "；".join(candidate.reasons))
        if candidate.observation is None:
            raise ValueError("候选样本缺少可持久化的同步观测")
        identity = candidate.detection.identity
        if identity is None or not identity.valid:
            raise ValueError("候选样本缺少有效目标身份证据")
        confirmed = self.record.confirmations.get("target")
        if confirmed is None:
            if not confirm_target:
                raise ValueError("首次保存样本前必须确认目标身份")
            confirmed = {
                "fingerprint": identity.fingerprint,
                "confirmed_at": _utc_now(),
            }
            self.record.confirmations["target"] = confirmed
        if not isinstance(confirmed, dict) or confirmed.get("fingerprint") != identity.fingerprint:
            raise ValueError("检测到的目标身份与任务确认记录不一致")
        sample = self.repository.add_sample(
            self.path,
            self.record,
            observation=candidate.observation,
            color_bgr=candidate.frame.color_bgr,
            overlay_bgr=candidate.detection.overlay_bgr,
            role=candidate.role.value,
        )
        del self._pending[candidate_id]
        self._result = None
        self._refresh_collection_state()
        self.repository.save(self.path, self.record)
        self._emit("sample-accepted", f"已保存样本 {sample.sample_id}")
        return sample

    def reject_candidate(self, candidate_id: str, reason: str) -> None:
        self._ensure_open()
        if candidate_id not in self._pending:
            raise KeyError("候选样本不存在或已经处理")
        if not str(reason).strip():
            raise ValueError("拒绝候选样本必须填写原因")
        del self._pending[candidate_id]
        self._emit("sample-rejected", "候选样本未保存")

    def set_sample_included(self, sample_id: str, included: bool, reason: str | None = None) -> None:
        self._ensure_open()
        index = next(
            (index for index, sample in enumerate(self.record.samples) if sample.sample_id == sample_id),
            None,
        )
        if index is None:
            raise KeyError(f"样本不存在：{sample_id}")
        if not included and not str(reason or "").strip():
            raise ValueError("排除样本必须填写原因")
        sample = self.record.samples[index]
        self.record.samples[index] = replace(
            sample,
            included=bool(included),
            exclusion_reason=None if included else str(reason).strip(),
        )
        self.record.outputs = {"result": None, "local_report": None, "artifact": None}
        self._result = None
        self._refresh_collection_state()
        self.repository.save(self.path, self.record)
        self._emit("sample-updated", f"已更新样本 {sample_id}")

    def solve(self) -> CalibrationResult:
        self._ensure_open()
        self.disconnect()
        if self.record.state in {
            RunState.READY,
            RunState.COLLECTING_CALIBRATION,
            RunState.COLLECTING_VALIDATION,
            RunState.READY_TO_SOLVE,
        }:
            self._refresh_collection_state()
        if self.record.state is not RunState.READY_TO_SOLVE:
            raise RuntimeError(f"当前任务尚未达到求解条件：{self.record.state.value}")
        integrity_errors = self.repository.verify(self.path, self.record)
        if integrity_errors:
            raise RuntimeError("任务证据完整性校验失败：" + "；".join(integrity_errors))
        observations = self._all_observations(included_only=False)
        target = self.record.confirmations.get("target")
        try:
            result = self.solver.solve(
                run_id=self.record.run_id,
                plan=self.plan,
                samples=observations,
                target_confirmed=isinstance(target, dict) and bool(target.get("confirmed_at")),
            )
            result_path = self.repository.write_result(self.path, result)
            report_path = self.reporter.render_local(
                run_path=self.path,
                record=self.record,
                result=result,
                observations=observations,
            )
        except BaseException as exc:
            self.record.last_error = str(exc)
            self.repository.save(self.path, self.record)
            self._emit("solve-error", "求解未完成")
            raise
        self._result = result
        self.record.outputs["result"] = result_path.name
        self.record.outputs["local_report"] = report_path.name
        self.record.outputs["artifact"] = None
        self.record.last_error = None
        self.record.state = RunState.SOLVED
        self.repository.save(self.path, self.record)
        self._emit("solved", "求解与质量评估已完成")
        return result

    def reopen(self, role: SampleRole | str = SampleRole.CALIBRATION) -> None:
        self._ensure_open()
        if self.record.state not in {RunState.SOLVED, RunState.EXPORTED}:
            raise RuntimeError("只有已求解或已导出的任务可以重新采集")
        normalized = SampleRole.parse(role)
        self.record.state = (
            RunState.COLLECTING_CALIBRATION
            if normalized is SampleRole.CALIBRATION
            else RunState.COLLECTING_VALIDATION
        )
        self.record.outputs = {"result": None, "local_report": None, "artifact": None}
        self._result = None
        self.repository.save(self.path, self.record)
        self._emit("reopened", "已重新打开采集阶段；旧结果不再有效")

    def export(self, output_path: str | Path | None = None) -> Path:
        self._ensure_open()
        if self.record.state not in {RunState.SOLVED, RunState.EXPORTED} or self._result is None:
            raise RuntimeError("只有已求解或已导出的任务可以导出")
        if not self._result.quality.passed:
            raise RuntimeError("标定结果未通过质量门禁，拒绝导出")
        observations = self._all_observations(included_only=False)
        artifact = self.exporter.export(
            run_path=self.path,
            record=self.record,
            result=self._result,
            observations=observations,
            output_path=output_path,
        )
        self.record.outputs["artifact"] = artifact.name
        self.record.state = RunState.EXPORTED
        self.repository.save(self.path, self.record)
        self._emit("exported", f"已生成交付包：{artifact}")
        return artifact

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.disconnect()
            if self.record.state is RunState.EXPORTED:
                self.record.state = RunState.CLOSED
                self.repository.save(self.path, self.record)
        finally:
            self._closed = True
            self._lease.__exit__(None, None, None)

    def __enter__(self) -> CalibrationRun:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = ["CalibrationRun"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
