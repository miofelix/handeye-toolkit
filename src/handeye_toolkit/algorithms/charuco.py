"""ChArUco 标定板检测、PnP 与图像质量检查。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, cast

import cv2
import numpy as np

from ..domain import (
    CameraFrame,
    DetectionPolicy,
    DetectionQuality,
    JsonValue,
    RigidTransform,
    TargetDetection,
    TargetIdentity,
)

DEFAULT_BOARD = {
    "squares_x": 12,
    "squares_y": 9,
    "square_length_m": 0.015,
    "marker_length_m": 0.01125,
    "dictionary": "DICT_5X5_1000",
    "start_id": 0,
}


class CharucoDetector:
    def __init__(
        self,
        parameters: Mapping[str, Any],
        policy: DetectionPolicy,
    ) -> None:
        self.config: dict[str, Any] = dict(DEFAULT_BOARD)
        self.config.update(dict(parameters))
        self.policy = policy
        canonical = json.dumps(dict(parameters), sort_keys=True, separators=(",", ":"))
        self._fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        square_length = float(self.config["square_length_m"])
        marker_length = float(self.config["marker_length_m"])
        if not 0.0 < marker_length < square_length:
            raise ValueError("marker_length_m 必须大于 0 且小于 square_length_m")
        if int(self.config["squares_x"]) < 2 or int(self.config["squares_y"]) < 2:
            raise ValueError("ChArUco 方格行列数必须至少为 2")
        dictionary_name = str(self.config["dictionary"])
        dictionary_id = getattr(cv2.aruco, dictionary_name, None)
        if dictionary_id is None:
            raise ValueError(f"OpenCV 不支持 ChArUco 字典 {dictionary_name}")
        self.dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        marker_count = int(self.config["squares_x"]) * int(self.config["squares_y"]) // 2
        marker_ids = np.arange(
            int(self.config["start_id"]),
            int(self.config["start_id"]) + marker_count,
            dtype=np.int32,
        )
        self.board = cv2.aruco.CharucoBoard(
            (int(self.config["squares_x"]), int(self.config["squares_y"])),
            square_length,
            marker_length,
            self.dictionary,
            marker_ids,
        )
        detector_params = cv2.aruco.DetectorParameters()
        detector_params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        charuco_params = cv2.aruco.CharucoParameters()
        self.detector = cv2.aruco.CharucoDetector(self.board, charuco_params, detector_params)

    @property
    def expected_marker_range(self) -> tuple[int, int]:
        ids = np.asarray(self.board.getIds(), dtype=np.int32).reshape(-1)
        return int(ids.min()), int(ids.max())

    def detect(self, frame: CameraFrame) -> TargetDetection:
        min_corners = self.policy.min_corners
        min_area_ratio = self.policy.min_area_ratio
        max_reprojection_rms_px = self.policy.max_reprojection_rms_px
        min_sharpness = self.policy.min_sharpness
        image = frame.color_bgr
        corners, ids, marker_corners, marker_ids = self.detector.detectBoard(image)
        corner_points = (
            np.asarray(corners, dtype=np.float32).reshape(-1, 2)
            if corners is not None
            else np.empty((0, 2), dtype=np.float32)
        )
        charuco_ids = (
            np.asarray(ids, dtype=np.int32).reshape(-1)
            if ids is not None
            else np.empty((0,), dtype=np.int32)
        )
        detected_marker_ids = (
            np.asarray(marker_ids, dtype=np.int32).reshape(-1)
            if marker_ids is not None
            else np.empty((0,), dtype=np.int32)
        )
        if len(corner_points):
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            cv2.cornerSubPix(
                gray,
                corner_points.reshape(-1, 1, 2),
                (3, 3),
                (-1, -1),
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
        sharpness = float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        area_ratio = 0.0
        if len(corner_points) >= 3:
            area_ratio = float(cv2.contourArea(cv2.convexHull(corner_points))) / float(
                image.shape[0] * image.shape[1]
            )

        target_to_camera = None
        reprojection_rms = None
        if len(corner_points) >= 4:
            object_points = np.asarray(self.board.getChessboardCorners(), dtype=np.float64)[
                charuco_ids
            ]
            distortion = np.asarray(frame.intrinsics.distortion_coefficients, dtype=np.float64)
            ok, rvec, tvec = cv2.solvePnP(
                object_points,
                corner_points.astype(np.float64),
                frame.intrinsics.matrix,
                distortion,
                flags=cv2.SOLVEPNP_ITERATIVE,
            )
            if ok:
                projected, _ = cv2.projectPoints(
                    object_points,
                    rvec,
                    tvec,
                    frame.intrinsics.matrix,
                    distortion,
                )
                residual = projected.reshape(-1, 2) - corner_points
                reprojection_rms = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
                rotation, _ = cv2.Rodrigues(rvec)
                transform = np.eye(4, dtype=np.float64)
                transform[:3, :3] = rotation
                transform[:3, 3] = np.asarray(tvec).reshape(3)
                target_to_camera = transform.tolist()

        reasons: list[str] = []
        if len(corner_points) < int(min_corners):
            reasons.append(f"ChArUco 角点不足：{len(corner_points)} < {min_corners}")
        if area_ratio < float(min_area_ratio):
            reasons.append(f"标定板面积过小：{area_ratio:.1%} < {min_area_ratio:.1%}")
        if reprojection_rms is None:
            reasons.append("PnP 求解失败")
        elif reprojection_rms > float(max_reprojection_rms_px):
            reasons.append(
                f"重投影 RMS 过大：{reprojection_rms:.3f} px > {max_reprojection_rms_px:.3f} px"
            )
        if sharpness < float(min_sharpness):
            reasons.append(f"图像清晰度过低：{sharpness:.1f} < {min_sharpness:.1f}")

        overlay = image.copy()
        if marker_corners is not None and marker_ids is not None:
            cv2.aruco.drawDetectedMarkers(overlay, marker_corners, marker_ids)
        if len(corner_points):
            cv2.aruco.drawDetectedCornersCharuco(
                overlay, corner_points.reshape(-1, 1, 2), charuco_ids.reshape(-1, 1)
            )
        expected_range = self.expected_marker_range
        marker_values = detected_marker_ids.astype(int).tolist()
        unexpected_ids = sorted(
            marker_id
            for marker_id in set(marker_values)
            if not expected_range[0] <= marker_id <= expected_range[1]
        )
        identity = None
        if marker_values:
            detected_range = (min(marker_values), max(marker_values))
            identity = TargetIdentity(
                expected=f"Marker ID {expected_range[0]}..{expected_range[1]}",
                observed=f"Marker ID {detected_range[0]}..{detected_range[1]}",
                valid=not unexpected_ids,
                fingerprint=self._fingerprint,
                details={
                    "expected_range": list(expected_range),
                    "observed_range": list(detected_range),
                    "unexpected_ids": unexpected_ids,
                },
            )
        quality = DetectionQuality(
            passed=not reasons,
            reasons=tuple(reasons),
            rank=(
                float(not reasons),
                float(len(corner_points)),
                float(area_ratio),
                -float(reprojection_rms if reprojection_rms is not None else 1e12),
                float(sharpness),
            ),
            metrics={
                "feature_count": len(corner_points),
                "marker_count": len(marker_values),
                "target_area_ratio": area_ratio,
                "reprojection_rms_px": reprojection_rms,
                "sharpness": sharpness,
            },
        )
        evidence: dict[str, JsonValue] = {
            "charuco_corners": cast(JsonValue, corner_points.astype(float).tolist()),
            "charuco_ids": cast(JsonValue, charuco_ids.astype(int).tolist()),
            "marker_ids": cast(JsonValue, marker_values),
        }

        return TargetDetection(
            transform=(
                None
                if target_to_camera is None
                else RigidTransform("camera", "target", target_to_camera)
            ),
            overlay_bgr=overlay,
            quality=quality,
            identity=identity,
            evidence=evidence,
        )


__all__ = ["CharucoDetector"]
