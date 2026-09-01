from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import DUMMY_HASHES, TARGET_PARAMETERS, product_document, transform

from handeye_toolkit.app import (
    STANDARD_PROFILE,
    CameraConfig,
    load_product_config,
    resolve_plan,
    validate_product_config,
    write_config_template,
    write_product_config,
)
from handeye_toolkit.domain import (
    CalibrationMode,
    CameraFrame,
    CameraIntrinsics,
    CaptureStamp,
    RigidTransform,
    SampleRecord,
    SampleRole,
    SynchronizedObservation,
    TargetDescriptor,
)
from handeye_toolkit.domain.geometry import transform_error


def test_config_is_strict_and_builds_product_boundaries() -> None:
    document = product_document()
    config = validate_product_config(document)

    assert config.as_dict() == document
    assert config.camera == CameraConfig("dabai", "<camera-source>")
    assert config.policy == STANDARD_PROFILE == "standard"
    assert config.plan.mode is CalibrationMode.EYE_TO_HAND
    assert config.acquisition.camera.adapter == "dabai"
    assert config.acquisition.flange.adapter == "piper-readonly"
    assert config.acquisition.flange.settings["allow_robot_control"] is False
    assert config.acquisition.target.adapter == "charuco"

    for mutation in (
        lambda value: value.update(policy="custom"),
        lambda value: value.update(extra=True),
    ):
        invalid = copy.deepcopy(document)
        mutation(invalid)
        with pytest.raises(ValueError):
            validate_product_config(invalid)


def test_config_accepts_d435_and_calibrated_rgb_camera_descriptors(tmp_path: Path) -> None:
    d435_document = product_document()
    d435_document["camera"] = {
        "adapter": "realsense-d435",
        "source_id": "<camera-serial>",
        "settings": {"width": 640, "height": 480, "fps": 30, "warmup_frames": 0},
    }
    d435 = validate_product_config(d435_document)
    assert d435.as_dict() == d435_document
    assert d435.camera_source_id == "<camera-serial>"
    assert d435.camera.adapter == "realsense-d435"
    assert d435.acquisition.camera.adapter == "realsense-d435"

    rgb_document = product_document()
    rgb_document["camera"] = {
        "adapter": "opencv-rgb",
        "source_id": "0",
        "settings": {
            "width": 640,
            "height": 480,
            "fps": 30.0,
            "warmup_frames": 0,
            "backend": "any",
            "intrinsics": {
                "fx": 600.0,
                "fy": 601.0,
                "cx": 320.0,
                "cy": 240.0,
                "distortion_model": "brown-conrady",
                "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
        },
    }
    rgb = validate_product_config(rgb_document)
    assert rgb.as_dict() == rgb_document
    assert rgb.acquisition.camera.adapter == "opencv-rgb"
    saved = write_product_config(rgb, tmp_path / "rgb-camera.yaml")
    assert load_product_config(saved).as_dict() == rgb_document

    invalid_rgb = copy.deepcopy(rgb_document)
    invalid_camera = invalid_rgb["camera"]
    assert isinstance(invalid_camera, dict)
    invalid_settings = invalid_camera["settings"]
    assert isinstance(invalid_settings, dict)
    del invalid_settings["intrinsics"]
    with pytest.raises(ValueError, match="intrinsics"):
        validate_product_config(invalid_rgb)


def test_legacy_dabai_camera_shorthand_is_read_and_normalized() -> None:
    legacy = product_document()
    legacy["camera"] = {"serial_number": "<camera-serial>"}

    config = validate_product_config(legacy)

    assert config.as_dict()["camera"] == {
        "adapter": "dabai",
        "source_id": "<camera-serial>",
        "settings": {},
    }


def test_config_rejects_invalid_dimensions_and_unknown_nested_fields() -> None:
    document = product_document()
    document["target"] = {
        "squares": [12, 9],
        "square_size_mm": 10.0,
        "marker_size_mm": 10.0,
        "dictionary": "DICT_5X5_1000",
    }
    with pytest.raises(ValueError, match="marker_size_mm"):
        validate_product_config(document)

    document = product_document()
    camera = copy.deepcopy(document["camera"])
    assert isinstance(camera, dict)
    camera["index"] = 0
    document["camera"] = camera
    with pytest.raises(ValueError, match="config.camera"):
        validate_product_config(document)


def test_yaml_template_never_overwrites_without_force(tmp_path: Path) -> None:
    selected = tmp_path / "handeye.yaml"
    assert write_config_template(selected) == selected.resolve()
    text = selected.read_text(encoding="utf-8")
    assert "schema_version" not in text
    assert "policy: standard" in text
    assert '"<camera-adapter>"' in text
    assert '"<camera-source>"' in text
    assert '"<can-channel>"' in text
    with pytest.raises(ValueError, match="camera.adapter"):
        load_product_config(selected)

    with pytest.raises(FileExistsError):
        write_config_template(selected)
    write_config_template(selected, force=True)


def test_policy_round_trip_is_immutable() -> None:
    plan = resolve_plan(
        profile="standard",
        mode="eye-in-hand",
        target_parameters=TARGET_PARAMETERS,
    )
    assert plan.mode is CalibrationMode.EYE_IN_HAND
    assert plan.from_mapping(plan.as_dict()) == plan
    with pytest.raises(TypeError):
        plan.target.parameters["squares_x"] = 8  # type: ignore[index]
    nested = TargetDescriptor("target", {"nested": {"values": [1, 2]}})
    nested_value = nested.parameters["nested"]
    assert isinstance(nested_value, dict) is False
    with pytest.raises(TypeError):
        nested_value["values"] = []  # type: ignore[index]
    assert nested.as_dict()["parameters"] == {"nested": {"values": [1, 2]}}
    with pytest.raises(ValueError, match="不支持的策略档案"):
        resolve_plan(
            profile="custom",
            mode="eye-to-hand",
            target_parameters=TARGET_PARAMETERS,
        )


def test_rigid_transform_convention_and_array_immutability() -> None:
    matrix = transform([0.2, -0.1, 0.3], [0.4, 0.1, 0.7])
    rigid = RigidTransform("parent", "child", matrix)
    matrix[0, 3] = 99.0

    assert rigid.matrix[0, 3] != 99.0
    assert transform_error(np.eye(4), rigid.inverse().matrix @ rigid.matrix) == pytest.approx(
        (0.0, 0.0), abs=1e-8
    )
    with pytest.raises(ValueError):
        RigidTransform("same", "same", np.eye(4))
    with pytest.raises(ValueError):
        rigid.matrix[0, 0] = 0.0

    pixels = np.zeros((3, 4, 3), dtype=np.uint8)
    frame = CameraFrame(
        pixels,
        CameraIntrinsics(500.0, 500.0, 2.0, 1.5),
        CaptureStamp(10, 20),
    )
    pixels[:] = 255
    assert not frame.color_bgr.any()
    with pytest.raises(ValueError):
        frame.color_bgr[0, 0, 0] = 1


def test_capture_stamps_and_observation_frames_are_strict() -> None:
    stamp = CaptureStamp(100, 120, device_timestamp=4.5, sequence=2)
    assert stamp.midpoint_ns == 110
    assert stamp.duration_s == pytest.approx(2e-8)
    assert CaptureStamp.from_mapping(stamp.as_dict()) == stamp
    with pytest.raises(ValueError):
        CaptureStamp(120, 100)
    with pytest.raises(ValueError):
        CaptureStamp(0, 1, device_timestamp=float("nan"))

    with pytest.raises(ValueError, match="base_to_flange"):
        SynchronizedObservation(
            captured_at="2026-08-30T00:00:00.000Z",
            intrinsics=CameraIntrinsics(500.0, 500.0, 2.0, 1.5),
            base_to_flange=RigidTransform("world", "flange", np.eye(4)),
            camera_to_target=RigidTransform("camera", "target", np.eye(4)),
            camera_stamp=stamp,
            flange_before_stamp=stamp,
            flange_after_stamp=stamp,
            translation_drift_m=0.0,
            rotation_drift_deg=0.0,
            detection_metrics={},
        )


def test_sample_ids_and_json_contracts_reject_unsafe_values() -> None:
    with pytest.raises(ValueError, match="sample_id"):
        SampleRecord("../sample", SampleRole.CALIBRATION, True, None, DUMMY_HASHES)
    with pytest.raises(ValueError, match="排除样本"):
        SampleRecord("sample_0001", SampleRole.CALIBRATION, False, None, DUMMY_HASHES)

    # 领域对象输出始终是标准 JSON，不包含 NaN 或自定义对象。
    document = validate_product_config(product_document()).as_dict()
    json.dumps(document, allow_nan=False)
