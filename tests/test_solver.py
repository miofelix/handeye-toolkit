from __future__ import annotations

import numpy as np
import pytest
from conftest import DUMMY_HASHES, synchronized_observation, synthetic_samples, transform

from handeye_toolkit.algorithms.solver import OpenCvHandeyeSolver
from handeye_toolkit.domain import CalibrationMode, RigidTransform, SampleRecord, SampleRole
from handeye_toolkit.domain.geometry import transform_error


@pytest.mark.parametrize("mode", list(CalibrationMode))
def test_solver_recovers_both_modes_with_noise(mode: CalibrationMode) -> None:
    plan, samples, expected = synthetic_samples(
        mode,
        translation_noise_m=0.0002,
        rotation_noise_deg=0.05,
    )
    result = OpenCvHandeyeSolver().solve(
        run_id="run_solver_noise",
        plan=plan,
        samples=samples,
        target_confirmed=True,
    )

    translation_m, rotation_deg = transform_error(expected, result.transform.matrix)
    assert result.quality.passed
    assert translation_m < 0.001
    assert rotation_deg < 0.2
    expected_parent = "base" if mode is CalibrationMode.EYE_TO_HAND else "flange"
    assert (result.transform.parent_frame, result.transform.child_frame) == (
        expected_parent,
        "camera",
    )


def test_validation_data_gates_quality_but_never_selects_the_solution() -> None:
    plan, samples, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=23)
    solver = OpenCvHandeyeSolver()
    accepted = solver.solve(
        run_id="run_validation_clean",
        plan=plan,
        samples=samples,
        target_confirmed=True,
    )

    corrupted = list(samples)
    for index in range(plan.sampling.calibration_target, len(corrupted)):
        sample, observation = corrupted[index]
        bad_target = observation.camera_to_target.matrix @ transform(
            [0.3, -0.2, 0.1],
            [0.03, -0.02, 0.01],
        )
        corrupted[index] = (
            sample,
            synchronized_observation(
                observation.base_to_flange.matrix,
                bad_target,
                index,
            ),
        )
    rejected = solver.solve(
        run_id="run_validation_bad",
        plan=plan,
        samples=corrupted,
        target_confirmed=True,
    )

    assert np.allclose(accepted.transform.matrix, rejected.transform.matrix, atol=1e-10)
    assert not rejected.quality.passed
    assert any("验证" in reason for reason in rejected.quality.reasons)


def test_robust_refinement_reports_calibration_outlier() -> None:
    plan, samples, expected = synthetic_samples(
        CalibrationMode.EYE_IN_HAND,
        seed=29,
        translation_noise_m=0.0002,
        rotation_noise_deg=0.05,
        calibration_outlier=True,
    )
    result = OpenCvHandeyeSolver().solve(
        run_id="run_solver_outlier",
        plan=plan,
        samples=samples,
        target_confirmed=True,
    )
    translation_m, rotation_deg = transform_error(expected, result.transform.matrix)

    assert result.quality.passed
    assert result.quality.method.startswith("Robust/")
    assert translation_m < 0.003
    assert rotation_deg < 0.5
    outliers = result.diagnostics["outliers"]
    assert isinstance(outliers, tuple)
    assert any(item["sample_id"] == "calibration_0004" for item in outliers)


def test_degenerate_calibration_poses_are_rejected_before_opencv() -> None:
    plan, samples, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND)
    fixed_pose = transform([0.0, 0.0, 0.0], [0.3, 0.0, 0.4])
    degenerate = []
    for index, (sample, observation) in enumerate(samples):
        degenerate.append(
            (
                sample,
                synchronized_observation(
                    fixed_pose,
                    observation.camera_to_target.matrix,
                    index,
                ),
            )
        )
    with pytest.raises(ValueError, match="覆盖不足"):
        OpenCvHandeyeSolver().solve(
            run_id="run_solver_degenerate",
            plan=plan,
            samples=degenerate,
            target_confirmed=True,
        )


def test_target_confirmation_is_an_export_quality_gate() -> None:
    plan, samples, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=31)
    result = OpenCvHandeyeSolver().solve(
        run_id="run_unconfirmed_target",
        plan=plan,
        samples=samples,
        target_confirmed=False,
    )
    assert not result.quality.passed
    assert "目标身份尚未确认" in result.quality.reasons


def test_excluded_samples_are_not_consumed() -> None:
    plan, samples, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=37)
    sample, observation = samples[0]
    samples[0] = (
        SampleRecord(
            sample.sample_id,
            SampleRole.CALIBRATION,
            False,
            "人工复核排除",
            DUMMY_HASHES,
        ),
        observation,
    )
    with pytest.raises(ValueError, match="标定样本不足"):
        OpenCvHandeyeSolver().solve(
            run_id="run_excluded",
            plan=plan,
            samples=samples,
            target_confirmed=True,
        )


def test_result_rejects_mode_transform_mismatch() -> None:
    plan, samples, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=41)
    result = OpenCvHandeyeSolver().solve(
        run_id="run_result_frames",
        plan=plan,
        samples=samples,
        target_confirmed=True,
    )
    with pytest.raises(ValueError, match="坐标合同"):
        type(result)(
            created_at=result.created_at,
            run_id=result.run_id,
            mode=result.mode,
            transform=RigidTransform("flange", "camera", result.transform.matrix),
            quality=result.quality,
            diagnostics=result.diagnostics,
        )
