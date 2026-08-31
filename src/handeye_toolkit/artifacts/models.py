"""经过完整性和 schema 校验的交付制品。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ..domain import (
    CalibrationPlan,
    CalibrationResult,
    RigidTransform,
    SampleRole,
)


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    sample_id: str
    role: SampleRole
    base_to_flange: RigidTransform
    camera_to_target: RigidTransform

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.sample_id) is None:
            raise ValueError("evidence observation ID 无效")
        object.__setattr__(self, "role", SampleRole.parse(self.role))
        if (
            self.base_to_flange.parent_frame,
            self.base_to_flange.child_frame,
        ) != ("base", "flange"):
            raise ValueError("evidence base_to_flange 坐标合同无效")
        if (
            self.camera_to_target.parent_frame,
            self.camera_to_target.child_frame,
        ) != ("camera", "target"):
            raise ValueError("evidence camera_to_target 坐标合同无效")


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    bundle_path: Path
    manifest: Mapping[str, object]
    result: CalibrationResult
    plan: CalibrationPlan
    observations: tuple[EvidenceObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", MappingProxyType(dict(self.manifest)))

    @property
    def matrix(self):
        return self.result.transform.matrix


__all__ = ["EvidenceObservation", "VerifiedArtifact"]
