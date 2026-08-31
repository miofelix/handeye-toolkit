"""不可变内置策略档案的加载与展开。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from typing import Any

from ..domain import (
    CalibrationMode,
    CalibrationPlan,
    CoveragePolicy,
    DetectionPolicy,
    SamplingPolicy,
    SolverPolicy,
    TargetDescriptor,
)

STANDARD_PROFILE = "standard"


def _profile_document(profile: str) -> dict[str, Any]:
    if profile != STANDARD_PROFILE:
        raise ValueError(f"不支持的策略档案：{profile}；当前仅支持 {STANDARD_PROFILE}")
    resource = files("handeye_toolkit.data.policies").joinpath("standard.json")
    document = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("内置策略档案必须是 JSON 对象")
    expected = {"id", "sampling", "coverage", "detection", "solver"}
    if set(document) != expected or document.get("id") != profile:
        raise RuntimeError("内置策略档案合同无效")
    if any(
        not isinstance(document[name], dict)
        for name in ("sampling", "coverage", "detection", "solver")
    ):
        raise RuntimeError("内置策略档案子项必须是 JSON 对象")
    return document


def resolve_plan(
    *,
    profile: str,
    mode: CalibrationMode | str,
    target_parameters: Mapping[str, object],
) -> CalibrationPlan:
    document = _profile_document(profile)
    parameters = dict(target_parameters)
    return CalibrationPlan(
        profile=profile,
        mode=CalibrationMode.parse(mode),
        target=TargetDescriptor("charuco", parameters),  # type: ignore[arg-type]
        sampling=SamplingPolicy(**document["sampling"]),
        coverage=CoveragePolicy(**document["coverage"]),
        detection=DetectionPolicy(**document["detection"]),
        solver=SolverPolicy(**document["solver"]),
    )


__all__ = ["STANDARD_PROFILE", "resolve_plan"]
