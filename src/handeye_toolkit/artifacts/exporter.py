"""从任务导出可复算、无硬件身份的交付制品。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..domain import CalibrationResult, RunRecord, SampleRecord, SynchronizedObservation
from .bundle import load_verified_artifact
from .constants import (
    ARTIFACT_MEMBERS,
    ARTIFACT_TYPE,
    MEMBER_MEDIA_TYPES,
)
from .report import HtmlReportRenderer


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


class CalibrationArtifactExporter:
    def __init__(self, renderer: HtmlReportRenderer | None = None) -> None:
        self.renderer = renderer or HtmlReportRenderer()

    def export(
        self,
        *,
        run_path: Path,
        record: RunRecord,
        result: CalibrationResult,
        observations: Sequence[tuple[SampleRecord, SynchronizedObservation]],
        output_path: str | Path | None = None,
    ) -> Path:
        if result.run_id != record.run_id or not result.quality.passed:
            raise RuntimeError("任务与结果不一致，或结果未通过质量门禁")
        result_path = run_path / "result.json"
        if not result_path.is_file():
            raise RuntimeError("本地 result.json 缺失")
        result_bytes = result_path.read_bytes()
        if result_bytes != _json_bytes(result.as_dict(include_diagnostics=True)):
            raise RuntimeError("本地 result.json 与内存结果不一致")
        included = [(sample, observation) for sample, observation in observations if sample.included]
        evidence = {
            "mode": result.mode.value,
            "plan": record.plan.as_dict(),
            "observations": [
                {
                    "id": sample.sample_id,
                    "role": sample.role.value,
                    "base_to_flange": observation.base_to_flange.as_dict(),
                    "camera_to_target": observation.camera_to_target.as_dict(),
                }
                for sample, observation in included
            ],
        }
        evidence_bytes = _json_bytes(evidence)
        report_bytes = self.renderer.render_sanitized(
            result=result,
            plan=record.plan,
            observations=included,
        )
        payloads = {
            "result.json": result_bytes,
            "evidence.json": evidence_bytes,
            "report.html": report_bytes,
        }
        manifest = {
            "artifact_type": ARTIFACT_TYPE,
            "created_at": _utc_now(),
            "producer": {"name": "handeye-toolkit"},
            "files": {
                name: {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "media_type": MEMBER_MEDIA_TYPES[name],
                }
                for name, payload in payloads.items()
            },
        }
        all_payloads = {"manifest.json": _json_bytes(manifest), **payloads}
        selected = Path(
            output_path or run_path / f"handeye-artifact_{record.run_id}.zip"
        ).expanduser().resolve()
        selected.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{selected.name}.", suffix=".tmp", dir=selected.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member in ARTIFACT_MEMBERS:
                    archive.writestr(member, all_payloads[member])
            load_verified_artifact(temporary)
            os.replace(temporary, selected)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return selected


__all__ = ["CalibrationArtifactExporter"]
