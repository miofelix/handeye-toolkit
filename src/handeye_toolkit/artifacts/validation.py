"""交付制品 JSON 的严格解析和语义校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from ..domain import CalibrationPlan, CalibrationResult, RigidTransform, SampleRole
from .constants import (
    ARTIFACT_TYPE,
    HASHED_MEMBERS,
    MEMBER_MEDIA_TYPES,
)
from .models import EvidenceObservation

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_json_object(payload: bytes, label: str) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} 包含重复字段 {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} 不是严格 UTF-8 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} 顶层必须是对象")
    return value


def _exact(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是对象")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} 字段不符合 schema：缺少 {sorted(expected - actual)}；"
            f"多出 {sorted(actual - expected)}"
        )
    return value


def validate_manifest(value: dict[str, Any]) -> Mapping[str, Any]:
    manifest = _exact(
        value,
        {"artifact_type", "created_at", "producer", "files"},
        "manifest.json",
    )
    if manifest["artifact_type"] != ARTIFACT_TYPE:
        raise ValueError("manifest.artifact_type 无效")
    if not isinstance(manifest["created_at"], str) or not manifest["created_at"].strip():
        raise ValueError("manifest.created_at 无效")
    producer = _exact(manifest["producer"], {"name"}, "manifest.producer")
    if producer["name"] != "handeye-toolkit":
        raise ValueError("manifest.producer 无效")
    files = _exact(manifest["files"], set(HASHED_MEMBERS), "manifest.files")
    for name, raw in files.items():
        entry = _exact(raw, {"sha256", "size", "media_type"}, f"manifest.files.{name}")
        if not isinstance(entry["sha256"], str) or SHA256_RE.fullmatch(entry["sha256"]) is None:
            raise ValueError(f"manifest.files.{name}.sha256 无效")
        if isinstance(entry["size"], bool) or not isinstance(entry["size"], int) or entry["size"] < 0:
            raise ValueError(f"manifest.files.{name}.size 无效")
        if entry["media_type"] != MEMBER_MEDIA_TYPES[name]:
            raise ValueError(f"manifest.files.{name}.media_type 无效")
    return manifest


def validate_result(value: dict[str, Any]) -> CalibrationResult:
    result = CalibrationResult.from_mapping(value)
    if not result.quality.passed:
        raise ValueError("交付结果未通过质量门禁")
    return result


def validate_evidence(
    value: dict[str, Any], result: CalibrationResult
) -> tuple[CalibrationPlan, tuple[EvidenceObservation, ...]]:
    evidence = _exact(
        value,
        {"mode", "plan", "observations"},
        "evidence.json",
    )
    if evidence["mode"] != result.mode.value:
        raise ValueError("evidence.mode 与 result.mode 不一致")
    if not isinstance(evidence["plan"], Mapping):
        raise ValueError("evidence.plan 必须是对象")
    plan = CalibrationPlan.from_mapping(evidence["plan"])
    if plan.mode is not result.mode:
        raise ValueError("evidence.plan.mode 与 result.mode 不一致")
    raw_observations = evidence["observations"]
    if not isinstance(raw_observations, list):
        raise ValueError("evidence.observations 必须是数组")
    observations: list[EvidenceObservation] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_observations):
        item = _exact(
            raw,
            {"id", "role", "base_to_flange", "camera_to_target"},
            f"evidence.observations[{index}]",
        )
        sample_id = str(item["id"])
        if not sample_id or sample_id in ids:
            raise ValueError("evidence.observations 包含空或重复 ID")
        ids.add(sample_id)
        role = SampleRole.parse(str(item["role"]))
        if not isinstance(item["base_to_flange"], Mapping) or not isinstance(
            item["camera_to_target"], Mapping
        ):
            raise ValueError("evidence observation 位姿必须是对象")
        observations.append(
            EvidenceObservation(
                sample_id,
                role,
                RigidTransform.from_mapping(item["base_to_flange"]),
                RigidTransform.from_mapping(item["camera_to_target"]),
            )
        )
    counts = {
        role.value: sum(1 for item in observations if item.role is role)
        for role in (SampleRole.CALIBRATION, SampleRole.VALIDATION)
    }
    if counts != dict(result.quality.sample_counts):
        raise ValueError("evidence 样本数量与 result 不一致")
    return plan, tuple(observations)


__all__ = [
    "parse_json_object",
    "validate_evidence",
    "validate_manifest",
    "validate_result",
]
