from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from handeye_toolkit.app.policy import resolve_plan
from handeye_toolkit.domain import (
    CalibrationMode,
    CalibrationPlan,
    CameraIntrinsics,
    CaptureStamp,
    JsonValue,
    RigidTransform,
    SampleRecord,
    SampleRole,
    SynchronizedObservation,
)
from handeye_toolkit.domain.geometry import invert_matrix

TARGET_PARAMETERS: dict[str, JsonValue] = {
    "squares_x": 12,
    "squares_y": 9,
    "square_length_m": 0.015,
    "marker_length_m": 0.01125,
    "dictionary": "DICT_5X5_1000",
    "start_id": 0,
}

DUMMY_HASHES = {
    "color.png": "0" * 64,
    "overlay.png": "0" * 64,
    "observation.json": "0" * 64,
}


def transform(rotation_vector: Sequence[float], translation: Sequence[float]) -> np.ndarray:
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = Rotation.from_rotvec(rotation_vector).as_matrix()
    result[:3, 3] = translation
    return result


def product_document() -> dict[str, object]:
    return {
        "mode": "eye-to-hand",
        "policy": "standard",
        "camera": {
            "adapter": "dabai",
            "source_id": "<camera-source>",
            "settings": {},
        },
        "piper": {
            "model": "piper",
            "firmware_profile": "v188",
            "can_channel": "<can-channel>",
        },
        "target": {
            "squares": [12, 9],
            "square_size_mm": 15.0,
            "marker_size_mm": 11.25,
            "dictionary": "DICT_5X5_1000",
        },
    }


def standard_plan(mode: CalibrationMode | str) -> CalibrationPlan:
    return resolve_plan(
        profile="standard",
        mode=mode,
        target_parameters=TARGET_PARAMETERS,
    )


def synchronized_observation(
    base_to_flange: np.ndarray,
    camera_to_target: np.ndarray,
    index: int,
) -> SynchronizedObservation:
    origin = 1_000_000 + index * 1_000
    return SynchronizedObservation(
        captured_at=f"2026-08-30T00:00:{index % 60:02d}.000Z",
        intrinsics=CameraIntrinsics(600.0, 601.0, 320.0, 240.0, "brown-conrady", ()),
        base_to_flange=RigidTransform("base", "flange", base_to_flange),
        camera_to_target=RigidTransform("camera", "target", camera_to_target),
        camera_stamp=CaptureStamp(origin + 20, origin + 30, sequence=index),
        flange_before_stamp=CaptureStamp(origin, origin + 10, device_timestamp=float(index)),
        flange_after_stamp=CaptureStamp(
            origin + 40,
            origin + 50,
            device_timestamp=float(index) + 0.1,
        ),
        translation_drift_m=0.0,
        rotation_drift_deg=0.0,
        detection_metrics={"corner_count": 48},
    )


def synthetic_samples(
    mode: CalibrationMode,
    *,
    seed: int = 17,
    translation_noise_m: float = 0.0,
    rotation_noise_deg: float = 0.0,
    calibration_outlier: bool = False,
) -> tuple[
    CalibrationPlan,
    list[tuple[SampleRecord, SynchronizedObservation]],
    np.ndarray,
]:
    rng = np.random.default_rng(seed)
    plan = standard_plan(mode)
    handeye = transform([0.12, -0.08, 0.05], [0.04, -0.02, 0.09])
    fixed = (
        transform([-0.15, 0.10, 0.20], [0.50, 0.02, 0.30])
        if mode is CalibrationMode.EYE_TO_HAND
        else transform([0.20, -0.10, 0.15], [0.35, 0.05, 0.75])
    )
    result: list[tuple[SampleRecord, SynchronizedObservation]] = []
    total = plan.sampling.calibration_target + plan.sampling.validation_target
    for index in range(total):
        base_to_flange = transform(
            rng.normal(0.0, 0.5, 3),
            rng.uniform([0.20, -0.35, 0.20], [0.65, 0.35, 0.75]),
        )
        if mode is CalibrationMode.EYE_TO_HAND:
            camera_to_target = invert_matrix(handeye) @ base_to_flange @ fixed
        else:
            camera_to_target = invert_matrix(handeye) @ invert_matrix(base_to_flange) @ fixed
        noise = transform(
            np.deg2rad(rng.normal(0.0, rotation_noise_deg, 3)),
            rng.normal(0.0, translation_noise_m, 3),
        )
        camera_to_target = camera_to_target @ noise
        if calibration_outlier and index == 3:
            camera_to_target = camera_to_target @ transform(
                [0.5, -0.4, 0.3],
                [0.08, -0.05, 0.04],
            )
        role = (
            SampleRole.CALIBRATION
            if index < plan.sampling.calibration_target
            else SampleRole.VALIDATION
        )
        role_index = index + 1 if role is SampleRole.CALIBRATION else index - 19
        sample = SampleRecord(
            f"{role.value}_{role_index:04d}",
            role,
            True,
            None,
            DUMMY_HASHES,
        )
        result.append(
            (
                sample,
                synchronized_observation(base_to_flange, camera_to_target, index),
            )
        )
    return plan, result, handeye
