"""调用方中立的 OpenCV/SciPy 手眼标定求解器。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from ..domain import (
    CalibrationMode,
    CalibrationPlan,
    CalibrationResult,
    PoseObservation,
    QualitySummary,
    RigidTransform,
    SampleRecord,
    SampleRole,
)
from ..domain.geometry import (
    coverage_metrics,
    invert_matrix,
    mean_transform,
    transform_error,
    transform_from_vector,
    transform_to_vector,
    validate_matrix,
)

METHODS = {
    "Tsai": cv2.CALIB_HAND_EYE_TSAI,
    "Park": cv2.CALIB_HAND_EYE_PARK,
    "Horaud": cv2.CALIB_HAND_EYE_HORAUD,
    "Andreff": cv2.CALIB_HAND_EYE_ANDREFF,
    "Daniilidis": cv2.CALIB_HAND_EYE_DANIILIDIS,
}


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    sample_id: str
    role: SampleRole
    base_to_flange: np.ndarray
    camera_to_target: np.ndarray


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _samples(
    values: Sequence[tuple[SampleRecord, PoseObservation]], role: SampleRole
) -> list[CalibrationSample]:
    return [
        CalibrationSample(
            record.sample_id,
            record.role,
            observation.base_to_flange.matrix,
            observation.camera_to_target.matrix,
        )
        for record, observation in values
        if record.included and record.role is role
    ]


def solve_opencv(
    samples: Sequence[CalibrationSample], method: int, mode: CalibrationMode
) -> np.ndarray:
    if len(samples) < 3:
        raise ValueError("OpenCV 手眼求解至少需要 3 个样本")
    robot = [
        sample.base_to_flange
        if mode is CalibrationMode.EYE_IN_HAND
        else invert_matrix(sample.base_to_flange)
        for sample in samples
    ]
    rotation, translation = cv2.calibrateHandEye(
        [item[:3, :3] for item in robot],
        [item[:3, 3] for item in robot],
        [item.camera_to_target[:3, :3] for item in samples],
        [item.camera_to_target[:3, 3] for item in samples],
        method=method,
    )
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = np.asarray(rotation, dtype=np.float64)
    result[:3, 3] = np.asarray(translation, dtype=np.float64).reshape(3)
    return validate_matrix(result, "OpenCV hand-eye result")


def _fixed_estimates(
    samples: Sequence[CalibrationSample], handeye: np.ndarray, mode: CalibrationMode
) -> list[np.ndarray]:
    if mode is CalibrationMode.EYE_IN_HAND:
        return [
            sample.base_to_flange @ handeye @ sample.camera_to_target for sample in samples
        ]
    return [
        invert_matrix(sample.base_to_flange) @ handeye @ sample.camera_to_target
        for sample in samples
    ]


def _evaluate(
    samples: Sequence[CalibrationSample],
    handeye: np.ndarray,
    fixed: np.ndarray,
    *,
    translation_gate_m: float,
    rotation_gate_deg: float,
    mode: CalibrationMode,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    translations: list[float] = []
    rotations: list[float] = []
    for sample, estimate in zip(samples, _fixed_estimates(samples, handeye, mode)):
        translation_m, rotation_deg = transform_error(fixed, estimate)
        translations.append(translation_m)
        rotations.append(rotation_deg)
        rows.append(
            {
                "sample_id": sample.sample_id,
                "translation_m": translation_m,
                "rotation_deg": rotation_deg,
            }
        )
    if not rows:
        return {
            "translation_rms_m": math.inf,
            "rotation_rms_deg": math.inf,
            "translation_p95_m": math.inf,
            "rotation_p95_deg": math.inf,
            "normalized_score": math.inf,
            "per_sample": [],
        }
    translation_values = np.asarray(translations, dtype=np.float64)
    rotation_values = np.asarray(rotations, dtype=np.float64)
    translation_rms = float(np.sqrt(np.mean(translation_values**2)))
    rotation_rms = float(np.sqrt(np.mean(rotation_values**2)))
    return {
        "translation_rms_m": translation_rms,
        "rotation_rms_deg": rotation_rms,
        "translation_p95_m": float(np.percentile(translation_values, 95)),
        "rotation_p95_deg": float(np.percentile(rotation_values, 95)),
        "normalized_score": translation_rms / translation_gate_m
        + rotation_rms / rotation_gate_deg,
        "per_sample": rows,
    }


def _joint_residual(
    vector: np.ndarray,
    samples: Sequence[CalibrationSample],
    translation_gate_m: float,
    rotation_gate_deg: float,
    mode: CalibrationMode,
) -> np.ndarray:
    handeye = transform_from_vector(vector[:6])
    fixed = transform_from_vector(vector[6:])
    residuals: list[float] = []
    for estimate in _fixed_estimates(samples, handeye, mode):
        delta = invert_matrix(fixed) @ estimate
        residuals.extend((delta[:3, 3] / translation_gate_m).tolist())
        residuals.extend(
            (
                Rotation.from_matrix(delta[:3, :3]).as_rotvec()
                / math.radians(rotation_gate_deg)
            ).tolist()
        )
    return np.asarray(residuals, dtype=np.float64)


def _robust_refine(
    samples: Sequence[CalibrationSample],
    initial_handeye: np.ndarray,
    *,
    translation_gate_m: float,
    rotation_gate_deg: float,
    mode: CalibrationMode,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    initial_fixed = mean_transform(_fixed_estimates(samples, initial_handeye, mode))
    initial = np.r_[transform_to_vector(initial_handeye), transform_to_vector(initial_fixed)]
    optimized = least_squares(
        _joint_residual,
        initial,
        args=(samples, translation_gate_m, rotation_gate_deg, mode),
        loss="soft_l1",
        f_scale=1.0,
        max_nfev=3000,
    )
    if not optimized.success or not np.isfinite(optimized.x).all():
        raise RuntimeError(f"SciPy 稳健联合优化失败：{optimized.message}")
    return (
        validate_matrix(transform_from_vector(optimized.x[:6]), "optimized hand-eye"),
        validate_matrix(transform_from_vector(optimized.x[6:]), "optimized fixed target"),
        {
            "cost": float(optimized.cost),
            "optimality": float(optimized.optimality),
            "nfev": int(optimized.nfev),
            "loss": "soft_l1",
        },
    )


def _outliers(rows: Sequence[dict[str, Any]], plan: CalibrationPlan) -> list[dict[str, Any]]:
    if len(rows) < 4:
        return []
    translations = np.asarray([float(row["translation_m"]) for row in rows])
    rotations = np.asarray([float(row["rotation_deg"]) for row in rows])
    translation_median = float(np.median(translations))
    rotation_median = float(np.median(rotations))
    translation_mad = float(np.median(np.abs(translations - translation_median)))
    rotation_mad = float(np.median(np.abs(rotations - rotation_median)))
    epsilon = float(np.finfo(np.float64).eps)
    translation_limit = max(
        translation_median + 3.0 * max(translation_mad, epsilon),
        3.0 * plan.solver.translation_gate_m,
    )
    rotation_limit = max(
        rotation_median + 3.0 * max(rotation_mad, epsilon),
        3.0 * plan.solver.rotation_gate_deg,
    )
    return [
        {
            **row,
            "translation_limit_m": translation_limit,
            "rotation_limit_deg": rotation_limit,
        }
        for row in rows
        if float(row["translation_m"]) > translation_limit
        or float(row["rotation_deg"]) > rotation_limit
    ]


def _bootstrap(
    samples: Sequence[CalibrationSample],
    *,
    method: int,
    reference: np.ndarray,
    plan: CalibrationPlan,
) -> dict[str, float]:
    iterations = plan.solver.bootstrap_iterations
    if iterations == 0:
        return {
            "translation_p95_m": 0.0,
            "rotation_p95_deg": 0.0,
            "successes": 0.0,
            "failures": 0.0,
        }
    rng = np.random.default_rng(plan.solver.random_seed)
    translations: list[float] = []
    rotations: list[float] = []
    failures = 0
    for _ in range(iterations):
        selected = [samples[index] for index in rng.integers(0, len(samples), len(samples))]
        try:
            estimate = solve_opencv(selected, method, plan.mode)
            translation_m, rotation_deg = transform_error(reference, estimate)
            translations.append(translation_m)
            rotations.append(rotation_deg)
        except (cv2.error, RuntimeError, ValueError, np.linalg.LinAlgError):
            failures += 1
    return {
        "translation_p95_m": (
            float(np.percentile(translations, 95)) if translations else math.inf
        ),
        "rotation_p95_deg": float(np.percentile(rotations, 95)) if rotations else math.inf,
        "successes": float(len(translations)),
        "failures": float(failures),
    }


class OpenCvHandeyeSolver:
    def solve(
        self,
        *,
        run_id: str,
        plan: CalibrationPlan,
        samples: Sequence[tuple[SampleRecord, PoseObservation]],
        target_confirmed: bool,
    ) -> CalibrationResult:
        calibration = _samples(samples, SampleRole.CALIBRATION)
        validation = _samples(samples, SampleRole.VALIDATION)
        if len(calibration) < plan.sampling.calibration_target:
            raise ValueError(
                f"标定样本不足：{len(calibration)}/{plan.sampling.calibration_target}"
            )
        if len(validation) < plan.sampling.validation_target:
            raise ValueError(
                f"验证样本不足：{len(validation)}/{plan.sampling.validation_target}"
            )

        coverage = coverage_metrics(
            [sample.base_to_flange for sample in calibration],
            **asdict(plan.coverage),
        )
        if not coverage["passed"]:
            raise ValueError("标定样本覆盖不足：" + "；".join(coverage["suggestions"]))

        method_diagnostics: dict[str, Any] = {}
        candidates: list[tuple[float, str, int, np.ndarray, np.ndarray, dict[str, Any]]] = []
        for name, method in METHODS.items():
            try:
                handeye = solve_opencv(calibration, method, plan.mode)
                fixed = mean_transform(_fixed_estimates(calibration, handeye, plan.mode))
                training = _evaluate(
                    calibration,
                    handeye,
                    fixed,
                    translation_gate_m=plan.solver.translation_gate_m,
                    rotation_gate_deg=plan.solver.rotation_gate_deg,
                    mode=plan.mode,
                )
                method_diagnostics[name] = {"status": "ok", "training": training}
                candidates.append(
                    (training["normalized_score"], name, method, handeye, fixed, training)
                )
            except (cv2.error, RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
                method_diagnostics[name] = {"status": "failed", "reason": str(exc)}
        if not candidates:
            raise RuntimeError("五种 OpenCV 手眼方法均未得到有效结果")
        _, selected_name, selected_method, handeye, fixed, training = min(
            candidates, key=lambda item: item[0]
        )
        optimization: dict[str, Any] | None = None
        try:
            optimized_handeye, optimized_fixed, optimization = _robust_refine(
                calibration,
                handeye,
                translation_gate_m=plan.solver.translation_gate_m,
                rotation_gate_deg=plan.solver.rotation_gate_deg,
                mode=plan.mode,
            )
            optimized_training = _evaluate(
                calibration,
                optimized_handeye,
                optimized_fixed,
                translation_gate_m=plan.solver.translation_gate_m,
                rotation_gate_deg=plan.solver.rotation_gate_deg,
                mode=plan.mode,
            )
            if optimized_training["normalized_score"] <= training["normalized_score"]:
                handeye, fixed, training = optimized_handeye, optimized_fixed, optimized_training
                selected_name = f"Robust/{selected_name}"
        except (RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
            optimization = {"status": "failed", "reason": str(exc)}

        validation_metrics = _evaluate(
            validation,
            handeye,
            fixed,
            translation_gate_m=plan.solver.translation_gate_m,
            rotation_gate_deg=plan.solver.rotation_gate_deg,
            mode=plan.mode,
        )
        reasons: list[str] = []
        if validation_metrics["translation_rms_m"] > plan.solver.translation_gate_m:
            reasons.append(
                "验证平移 RMS 超过门槛："
                f"{validation_metrics['translation_rms_m'] * 1000:.3f} mm"
            )
        if validation_metrics["rotation_rms_deg"] > plan.solver.rotation_gate_deg:
            reasons.append(
                f"验证旋转 RMS 超过门槛：{validation_metrics['rotation_rms_deg']:.3f}°"
            )
        if not target_confirmed:
            reasons.append("目标身份尚未确认")
        uncertainty = _bootstrap(
            calibration,
            method=selected_method,
            reference=handeye,
            plan=plan,
        )
        parent_frame = "base" if plan.mode is CalibrationMode.EYE_TO_HAND else "flange"
        result_transform = RigidTransform(parent_frame, "camera", handeye)
        quality = QualitySummary(
            passed=not reasons,
            reasons=tuple(reasons),
            method=selected_name,
            sample_counts={"calibration": len(calibration), "validation": len(validation)},
            validation_rms={
                "translation_m": validation_metrics["translation_rms_m"],
                "rotation_deg": validation_metrics["rotation_rms_deg"],
            },
            validation_p95={
                "translation_m": validation_metrics["translation_p95_m"],
                "rotation_deg": validation_metrics["rotation_p95_deg"],
            },
            coverage={
                "position_span_m": coverage["position_span_m"],
                "rotation_span_deg": coverage["rotation_span_deg"],
                "nonparallel_axes": coverage["has_two_nonparallel_rotation_axes"],
                "duplicate_count": len(coverage["duplicate_pairs"]),
            },
            uncertainty=uncertainty,
        )
        diagnostics = {
            "methods": method_diagnostics,
            "training": training,
            "validation": validation_metrics,
            "fixed_target_transform": fixed.tolist(),
            "optimization": optimization,
            "outliers": _outliers(training["per_sample"], plan),
        }
        return CalibrationResult(
            created_at=_utc_now(),
            run_id=run_id,
            mode=plan.mode,
            transform=result_transform,
            quality=quality,
            diagnostics=diagnostics,
        )


__all__ = ["METHODS", "CalibrationSample", "OpenCvHandeyeSolver", "solve_opencv"]
