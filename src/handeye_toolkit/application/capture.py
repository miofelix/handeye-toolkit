"""带明确时间误差边界的手动静止采样事务。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..domain import (
    CalibrationPlan,
    CameraFrame,
    FlangePose,
    RigidTransform,
    SampleRole,
    SynchronizedObservation,
    TargetDetection,
)
from ..domain.geometry import coverage_metrics, mean_transform, transform_error
from ..ports import AcquisitionRig
from .models import CaptureCandidate, PoseAssessment


class CaptureRejected(RuntimeError):
    """当前姿态或时序不满足采样前提，但任务仍可继续。"""


@dataclass(frozen=True, slots=True)
class _PoseWindow:
    values: tuple[FlangePose, ...]
    transform: RigidTransform
    translation_drift_m: float
    rotation_drift_deg: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class CaptureCoordinator:
    def __init__(
        self,
        rig: AcquisitionRig,
        plan: CalibrationPlan,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rig = rig
        self.plan = plan
        self.sleep_fn = sleep_fn

    def _pose_window(self, duration_s: float) -> _PoseWindow:
        count = max(2, int(round(duration_s * self.plan.sampling.feedback_hz)) + 1)
        interval = duration_s / max(1, count - 1)
        values: list[FlangePose] = []
        previous_ns: int | None = None
        for index in range(count):
            pose = self.rig.flange.read()
            received_ns = pose.stamp.host_received_ns
            if previous_ns is not None and received_ns <= previous_ns:
                raise CaptureRejected("法兰反馈的主机接收时间未递增")
            previous_ns = received_ns
            values.append(pose)
            if index + 1 < count:
                self.sleep_fn(interval)
        matrices = [item.transform.matrix for item in values]
        transform = RigidTransform("base", "flange", mean_transform(matrices))
        translation = 0.0
        rotation = 0.0
        for matrix in matrices:
            item_translation, item_rotation = transform_error(transform.matrix, matrix)
            translation = max(translation, item_translation)
            rotation = max(rotation, item_rotation)
        return _PoseWindow(tuple(values), transform, translation, rotation)

    def assess_pose(
        self,
        role: SampleRole | str,
        accepted: Sequence[SynchronizedObservation],
    ) -> PoseAssessment:
        SampleRole.parse(role)
        window = self._pose_window(self.plan.sampling.stability_before_s)
        stable = (
            window.translation_drift_m <= self.plan.sampling.max_translation_drift_m
            and window.rotation_drift_deg <= self.plan.sampling.max_rotation_drift_deg
        )
        novel = True
        for observation in accepted:
            translation_m, rotation_deg = transform_error(
                observation.base_to_flange.matrix, window.transform.matrix
            )
            if (
                translation_m < self.plan.coverage.duplicate_translation_m
                and rotation_deg < self.plan.coverage.duplicate_rotation_deg
            ):
                novel = False
                break
        coverage = coverage_metrics(
            [observation.base_to_flange.matrix for observation in accepted]
            + [window.transform.matrix],
            **asdict(self.plan.coverage),
        )
        suggestions = list(coverage["suggestions"])
        if not stable:
            suggestions.insert(0, "保持机械臂静止后再采样")
        if not novel:
            suggestions.insert(0, "当前姿态与已有样本重复，请改变位置或朝向")
        return PoseAssessment(
            stable=stable,
            novel=novel,
            translation_drift_m=window.translation_drift_m,
            rotation_drift_deg=window.rotation_drift_deg,
            position_span_m=float(coverage["position_span_m"]),
            rotation_span_deg=float(coverage["rotation_span_deg"]),
            suggestions=tuple(suggestions),
        )

    def capture(
        self,
        role: SampleRole | str,
        accepted: Sequence[SynchronizedObservation],
    ) -> CaptureCandidate:
        normalized_role = SampleRole.parse(role)
        pose = self.assess_pose(normalized_role, accepted)
        if not pose.stable or not pose.novel:
            raise CaptureRejected("；".join(pose.suggestions[:2]))

        candidates: list[
            tuple[CameraFrame, TargetDetection, SynchronizedObservation | None, list[str]]
        ] = []
        for _ in range(self.plan.sampling.candidate_frames):
            before = self.rig.flange.read()
            frame = self.rig.camera.capture()
            after = self.rig.flange.read()
            detection = self.rig.detector.detect(frame)
            reasons = list(detection.quality.reasons)
            if not detection.quality.passed and not reasons:
                reasons.append("目标检测质量未通过")
            if detection.transform is None:
                reasons.append("目标检测未提供 camera <- target 变换")
            identity = detection.identity
            if identity is None or not identity.valid:
                reasons.append("目标检测未提供有效身份凭据")
            if before.stamp.host_received_ns > frame.stamp.host_started_ns:
                reasons.append("法兰前置反馈未包围相机采集区间")
            if after.stamp.host_started_ns < frame.stamp.host_received_ns:
                reasons.append("法兰后置反馈未包围相机采集区间")
            if frame.stamp.duration_s > self.plan.sampling.max_capture_interval_s:
                reasons.append("相机采集区间超过策略门槛")
            translation_m, rotation_deg = transform_error(
                before.transform.matrix, after.transform.matrix
            )
            if translation_m > self.plan.sampling.max_translation_drift_m:
                reasons.append("相机采集期间法兰平移漂移过大")
            if rotation_deg > self.plan.sampling.max_rotation_drift_deg:
                reasons.append("相机采集期间法兰旋转漂移过大")
            observation = None
            if detection.transform is not None:
                observation = SynchronizedObservation(
                    captured_at=_utc_now(),
                    intrinsics=frame.intrinsics,
                    base_to_flange=RigidTransform(
                        "base",
                        "flange",
                        mean_transform([before.transform.matrix, after.transform.matrix]),
                    ),
                    camera_to_target=detection.transform,
                    camera_stamp=frame.stamp,
                    flange_before_stamp=before.stamp,
                    flange_after_stamp=after.stamp,
                    translation_drift_m=translation_m,
                    rotation_drift_deg=rotation_deg,
                    detection_metrics=detection.quality.metrics,
                )
            candidates.append((frame, detection, observation, reasons))

        frame, detection, observation, reasons = max(
            candidates,
            key=lambda item: (not item[3], *item[1].quality.rank),
        )
        after_window = self._pose_window(self.plan.sampling.stability_after_s)
        if (
            after_window.translation_drift_m > self.plan.sampling.max_translation_drift_m
            or after_window.rotation_drift_deg > self.plan.sampling.max_rotation_drift_deg
        ):
            reasons.append("拍摄后机械臂未保持静止")
        if observation is not None:
            cross_translation, cross_rotation = transform_error(
                observation.base_to_flange.matrix, after_window.transform.matrix
            )
            if cross_translation > self.plan.sampling.max_translation_drift_m:
                reasons.append("候选帧到拍摄后窗口之间法兰平移漂移过大")
            if cross_rotation > self.plan.sampling.max_rotation_drift_deg:
                reasons.append("候选帧到拍摄后窗口之间法兰旋转漂移过大")
        return CaptureCandidate(
            candidate_id=uuid.uuid4().hex,
            role=normalized_role,
            frame=frame,
            detection=detection,
            observation=observation,
            pose=pose,
            reasons=tuple(dict.fromkeys(reasons)),
        )


__all__ = ["CaptureCoordinator", "CaptureRejected"]
