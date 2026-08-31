"""领域层统一使用的刚体变换与姿态覆盖运算。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np


def validate_matrix(value: Any, label: str = "transform") -> np.ndarray:
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
    return np.array(matrix, dtype=np.float64, copy=True)


def invert_matrix(value: Any) -> np.ndarray:
    matrix = validate_matrix(value)
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = matrix[:3, :3].T
    result[:3, 3] = -(result[:3, :3] @ matrix[:3, 3])
    return result


def _rotation_vector(rotation: np.ndarray) -> np.ndarray:
    trace = float(np.trace(rotation))
    angle = math.acos(float(np.clip((trace - 1.0) * 0.5, -1.0, 1.0)))
    if angle < 1e-10:
        return np.array(
            [
                rotation[2, 1] - rotation[1, 2],
                rotation[0, 2] - rotation[2, 0],
                rotation[1, 0] - rotation[0, 1],
            ],
            dtype=np.float64,
        ) * 0.5
    if math.pi - angle < 1e-7:
        values, vectors = np.linalg.eig(rotation)
        index = int(np.argmin(np.abs(values - 1.0)))
        axis = np.real(vectors[:, index]).astype(np.float64)
        norm = float(np.linalg.norm(axis))
        if norm < 1e-12:
            raise ValueError("无法从旋转矩阵恢复旋转轴")
        axis /= norm
        return axis * angle
    axis = np.array(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ],
        dtype=np.float64,
    ) / (2.0 * math.sin(angle))
    return axis * angle


def _rotation_matrix(vector: np.ndarray) -> np.ndarray:
    angle = float(np.linalg.norm(vector))
    if angle < 1e-12:
        skew = np.array(
            [[0.0, -vector[2], vector[1]], [vector[2], 0.0, -vector[0]], [-vector[1], vector[0], 0.0]],
            dtype=np.float64,
        )
        return np.eye(3, dtype=np.float64) + skew
    axis = vector / angle
    skew = np.array(
        [[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]],
        dtype=np.float64,
    )
    return np.eye(3, dtype=np.float64) + math.sin(angle) * skew + (1.0 - math.cos(angle)) * (skew @ skew)


def transform_error(reference: Any, estimate: Any) -> tuple[float, float]:
    delta = invert_matrix(reference) @ validate_matrix(estimate)
    translation_m = float(np.linalg.norm(delta[:3, 3]))
    rotation_deg = math.degrees(float(np.linalg.norm(_rotation_vector(delta[:3, :3]))))
    return translation_m, rotation_deg


def mean_transform(values: Sequence[Any]) -> np.ndarray:
    if not values:
        raise ValueError("至少需要一个刚体变换")
    matrices = [validate_matrix(value) for value in values]
    average = sum((matrix[:3, :3] for matrix in matrices), np.zeros((3, 3)))
    left, _, right = np.linalg.svd(average)
    correction = np.eye(3, dtype=np.float64)
    correction[2, 2] = 1.0 if np.linalg.det(left @ right) >= 0.0 else -1.0
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = left @ correction @ right
    result[:3, 3] = np.mean([matrix[:3, 3] for matrix in matrices], axis=0)
    return validate_matrix(result, "mean transform")


def transform_to_vector(value: Any) -> np.ndarray:
    matrix = validate_matrix(value)
    return np.r_[matrix[:3, 3], _rotation_vector(matrix[:3, :3])]


def transform_from_vector(vector: Sequence[float] | np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(-1)
    if value.shape != (6,) or not np.isfinite(value).all():
        raise ValueError("位姿向量必须包含 6 个有限数值")
    result = np.eye(4, dtype=np.float64)
    result[:3, 3] = value[:3]
    result[:3, :3] = _rotation_matrix(value[3:])
    return validate_matrix(result, "pose vector")


def coverage_metrics(
    transforms: Sequence[Any],
    *,
    min_position_span_m: float,
    min_rotation_span_deg: float,
    min_rotation_change_deg: float,
    nonparallel_axis_min_angle_deg: float,
    duplicate_translation_m: float,
    duplicate_rotation_deg: float,
) -> dict[str, Any]:
    matrices = [validate_matrix(value) for value in transforms]
    if not matrices:
        return {
            "position_span_m": 0.0,
            "rotation_span_deg": 0.0,
            "has_two_nonparallel_rotation_axes": False,
            "duplicate_pairs": [],
            "passed": False,
            "suggestions": ["增加法兰位置覆盖范围", "增加法兰姿态旋转幅度", "围绕至少两个不平行轴改变姿态"],
        }

    positions = np.asarray([matrix[:3, 3] for matrix in matrices], dtype=np.float64)
    position_span_m = float(np.linalg.norm(np.ptp(positions, axis=0)))
    reference = matrices[0][:3, :3]
    vectors = [_rotation_vector(reference.T @ matrix[:3, :3]) for matrix in matrices]
    angles = np.asarray([np.linalg.norm(vector) for vector in vectors], dtype=np.float64)
    rotation_span_deg = math.degrees(float(np.max(angles)))
    axes = [
        vector / np.linalg.norm(vector)
        for vector, angle in zip(vectors, angles, strict=True)
        if math.degrees(float(angle)) >= min_rotation_change_deg
    ]
    has_nonparallel = any(
        math.degrees(math.acos(float(np.clip(abs(np.dot(first, second)), -1.0, 1.0))))
        >= nonparallel_axis_min_angle_deg
        for index, first in enumerate(axes)
        for second in axes[index + 1 :]
    )
    duplicate_pairs: list[list[int]] = []
    for first in range(len(matrices)):
        for second in range(first + 1, len(matrices)):
            translation_m, rotation_deg = transform_error(matrices[first], matrices[second])
            if translation_m < duplicate_translation_m and rotation_deg < duplicate_rotation_deg:
                duplicate_pairs.append([first, second])

    suggestions: list[str] = []
    if position_span_m < min_position_span_m:
        suggestions.append("扩大法兰位置覆盖范围")
    if rotation_span_deg < min_rotation_span_deg:
        suggestions.append("增加法兰姿态旋转幅度")
    if not has_nonparallel:
        suggestions.append("围绕至少两个不平行轴改变姿态")
    return {
        "position_span_m": position_span_m,
        "rotation_span_deg": rotation_span_deg,
        "has_two_nonparallel_rotation_axes": has_nonparallel,
        "duplicate_pairs": duplicate_pairs,
        "passed": not suggestions,
        "suggestions": suggestions,
    }


__all__ = [
    "coverage_metrics",
    "invert_matrix",
    "mean_transform",
    "transform_error",
    "transform_from_vector",
    "transform_to_vector",
    "validate_matrix",
]
