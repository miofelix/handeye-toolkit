"""不加载数值算法或硬件 SDK 的安全 ZIP 验证器。"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

from .constants import (
    ARTIFACT_MEMBERS,
    HASHED_MEMBERS,
    MAX_ARCHIVE_BYTES,
    MAX_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from .models import VerifiedArtifact
from .validation import parse_json_object, validate_evidence, validate_manifest, validate_result


def load_verified_artifact(path: str | Path) -> VerifiedArtifact:
    selected = Path(path).expanduser().resolve()
    if not selected.is_file():
        raise FileNotFoundError(f"制品不存在：{selected}")
    if selected.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP 文件超过允许大小")
    try:
        archive = zipfile.ZipFile(selected)
    except zipfile.BadZipFile as exc:
        raise ValueError("制品不是有效 ZIP") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("ZIP 包含重复成员")
        if set(names) != set(ARTIFACT_MEMBERS) or len(names) != len(ARTIFACT_MEMBERS):
            raise ValueError("ZIP 成员集合不符合制品合同")
        total = 0
        by_name = {info.filename: info for info in infos}
        for info in infos:
            if (
                info.is_dir()
                or info.filename.startswith(("/", "\\"))
                or ".." in Path(info.filename).parts
                or info.flag_bits & 1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise ValueError(f"ZIP 成员路径或属性无效：{info.filename}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"ZIP 成员超过允许大小：{info.filename}")
            total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("ZIP 解压后总大小超过允许值")
        manifest = validate_manifest(
            parse_json_object(archive.read(by_name["manifest.json"]), "manifest.json")
        )
        payloads: dict[str, bytes] = {}
        for name in HASHED_MEMBERS:
            payload = archive.read(by_name[name])
            entry = manifest["files"][name]
            if len(payload) != entry["size"]:
                raise ValueError(f"{name} 大小与 manifest 不一致")
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError(f"{name} SHA-256 与 manifest 不一致")
            payloads[name] = payload
    result = validate_result(parse_json_object(payloads["result.json"], "result.json"))
    plan, observations = validate_evidence(
        parse_json_object(payloads["evidence.json"], "evidence.json"), result
    )
    return VerifiedArtifact(selected, dict(manifest), result, plan, observations)


def recompute_verified_artifact(
    artifact: VerifiedArtifact,
    *,
    translation_tolerance_m: float = 1e-5,
    rotation_tolerance_deg: float = 1e-3,
):
    from ..algorithms.solver import OpenCvHandeyeSolver
    from ..domain import SampleRecord
    from ..domain.geometry import transform_error

    placeholder_hashes = {
        "color.png": "0" * 64,
        "overlay.png": "0" * 64,
        "observation.json": "0" * 64,
    }
    samples = [
        (
            SampleRecord(
                item.sample_id,
                item.role,
                True,
                None,
                placeholder_hashes,
            ),
            item,
        )
        for item in artifact.observations
    ]
    recomputed = OpenCvHandeyeSolver().solve(
        run_id=artifact.result.run_id,
        plan=artifact.plan,
        samples=samples,
        target_confirmed=True,
    )
    translation_m, rotation_deg = transform_error(
        artifact.result.transform.matrix, recomputed.transform.matrix
    )
    if translation_m > translation_tolerance_m or rotation_deg > rotation_tolerance_deg:
        raise ValueError(
            "制品离线复算结果不一致："
            f"{translation_m * 1000:.6f} mm / {rotation_deg:.6f}°"
        )
    return recomputed


__all__ = ["load_verified_artifact", "recompute_verified_artifact"]
