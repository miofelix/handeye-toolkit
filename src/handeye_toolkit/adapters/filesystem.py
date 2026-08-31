"""任务仓储：严格解析、原子写入、样本哈希和独占锁。"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np

from ..domain import (
    AcquisitionDescriptor,
    CalibrationPlan,
    CalibrationResult,
    JsonValue,
    RunRecord,
    RunState,
    SampleRecord,
    SampleRole,
    SynchronizedObservation,
)

_SESSION_KEYS = {
    "run_id",
    "created_at",
    "updated_at",
    "state",
    "plan",
    "acquisition",
    "confirmations",
    "samples",
    "outputs",
    "last_error",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(path, _json_bytes(value))


def _atomic_image(path: Path, image: object) -> None:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"{path.name} 必须是 HxWx3 图像")
    ok, encoded = cv2.imencode(".png", np.ascontiguousarray(array, dtype=np.uint8))
    if not ok:
        raise RuntimeError(f"图像编码失败：{path.name}")
    _atomic_bytes(path, encoded.tobytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 包含重复字段 {key!r}")
            result[key] = value
        return result

    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=reject_duplicate,
                parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"无法严格读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _sample_from_mapping(value: Mapping[str, object]) -> SampleRecord:
    expected = {"id", "role", "included", "exclusion_reason", "hashes"}
    if set(value) != expected or not isinstance(value["hashes"], Mapping):
        raise ValueError("session.samples 字段无效")
    return SampleRecord(
        sample_id=str(value["id"]),
        role=SampleRole.parse(str(value["role"])),
        included=value["included"],  # type: ignore[arg-type]
        exclusion_reason=(
            None if value["exclusion_reason"] is None else str(value["exclusion_reason"])
        ),
        hashes={str(k): str(v) for k, v in value["hashes"].items()},
    )


def _record_as_dict(record: RunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "state": record.state.value,
        "plan": record.plan.as_dict(),
        "acquisition": record.acquisition.as_dict(),
        "confirmations": record.confirmations,
        "samples": [sample.as_dict() for sample in record.samples],
        "outputs": record.outputs,
        "last_error": record.last_error,
    }


def _record_from_dict(value: Mapping[str, object]) -> RunRecord:
    if set(value) != _SESSION_KEYS:
        raise ValueError(
            f"session 字段不符合 schema：缺少 {sorted(_SESSION_KEYS - set(value))}；"
            f"多出 {sorted(set(value) - _SESSION_KEYS)}"
        )
    objects: dict[str, Mapping[str, object]] = {}
    for name in ("plan", "acquisition", "confirmations", "outputs"):
        item = value[name]
        if not isinstance(item, Mapping):
            raise ValueError(f"session.{name} 必须是对象")
        objects[name] = cast(Mapping[str, object], item)
    if not isinstance(value["samples"], list):
        raise ValueError("session.samples 必须是数组")
    confirmations = dict(objects["confirmations"])
    if set(confirmations) != {"safety", "target"}:
        raise ValueError("session.confirmations 字段无效")
    safety_events = confirmations["safety"]
    target_confirmation = confirmations["target"]
    if not isinstance(safety_events, list) or any(
        not isinstance(item, Mapping)
        or set(item) != {"confirmed_at"}
        or not isinstance(item["confirmed_at"], str)
        or not item["confirmed_at"].strip()
        for item in safety_events
    ):
        raise ValueError("session.confirmations.safety 无效")
    if target_confirmation is not None and (
        not isinstance(target_confirmation, Mapping)
        or set(target_confirmation) != {"fingerprint", "confirmed_at"}
        or not all(
            isinstance(target_confirmation[name], str)
            and bool(target_confirmation[name].strip())
            for name in ("fingerprint", "confirmed_at")
        )
    ):
        raise ValueError("session.confirmations.target 无效")
    outputs = dict(objects["outputs"])
    if set(outputs) != {"result", "local_report", "artifact"}:
        raise ValueError("session.outputs 字段无效")
    if any(value is not None and not isinstance(value, str) for value in outputs.values()):
        raise ValueError("session.outputs 值必须是字符串或 null")
    if value["last_error"] is not None and not isinstance(value["last_error"], str):
        raise ValueError("session.last_error 必须是字符串或 null")
    samples: list[SampleRecord] = []
    for item in value["samples"]:
        if not isinstance(item, Mapping):
            raise ValueError("session.samples 成员必须是对象")
        samples.append(_sample_from_mapping(item))
    ids = [sample.sample_id for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("session.samples 包含重复 ID")
    return RunRecord(
        run_id=str(value["run_id"]),
        created_at=str(value["created_at"]),
        updated_at=str(value["updated_at"]),
        state=RunState(str(value["state"])),
        plan=CalibrationPlan.from_mapping(objects["plan"]),
        acquisition=AcquisitionDescriptor.from_mapping(objects["acquisition"]),
        confirmations=cast(dict[str, JsonValue], confirmations),
        samples=samples,
        outputs=cast(
            dict[str, str | None],
            {str(k): v for k, v in outputs.items()},
        ),
        last_error=None if value["last_error"] is None else str(value["last_error"]),
    )


class FileRunRepository:
    @contextlib.contextmanager
    def lock(self, path: Path) -> Iterator[None]:
        lock_path = path / ".run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"任务正在被另一个进程使用：{path}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def create(
        self,
        *,
        plan: CalibrationPlan,
        acquisition: AcquisitionDescriptor,
        output_root: str | Path,
    ) -> tuple[Path, RunRecord]:
        root = Path(output_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        run_id = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = root / run_id
        path.mkdir()
        now = utc_now()
        record = RunRecord(
            run_id=run_id,
            created_at=now,
            updated_at=now,
            state=RunState.READY,
            plan=plan,
            acquisition=acquisition,
            confirmations={"safety": [], "target": None},
            samples=[],
            outputs={"result": None, "local_report": None, "artifact": None},
        )
        self.save(path, record)
        return path, record

    def load(self, run_ref: str | Path) -> tuple[Path, RunRecord]:
        selected = Path(run_ref).expanduser().resolve()
        file = selected / "session.json" if selected.is_dir() else selected
        if not file.is_file():
            raise FileNotFoundError(f"任务记录不存在：{file}")
        record = _record_from_dict(_strict_json(file))
        if file.parent.name != record.run_id:
            raise ValueError("任务目录名与 run_id 不一致")
        return file.parent, record

    def save(self, path: Path, record: RunRecord) -> None:
        record.updated_at = utc_now()
        _atomic_json(path / "session.json", _record_as_dict(record))

    def add_sample(
        self,
        path: Path,
        record: RunRecord,
        *,
        observation: SynchronizedObservation,
        color_bgr: object,
        overlay_bgr: object,
        role: str,
    ) -> SampleRecord:
        normalized = SampleRole.parse(role)
        sequence = 1 + max(
            [
                int(sample.sample_id.rsplit("_", 1)[-1])
                for sample in record.samples
                if sample.role is normalized
            ]
            or [0]
        )
        sample_id = f"{normalized.value}_{sequence:04d}"
        samples_root = path / "samples"
        samples_root.mkdir(parents=True, exist_ok=True)
        temporary = samples_root / f".{sample_id}.{uuid.uuid4().hex}.tmp"
        destination = samples_root / sample_id
        temporary.mkdir()
        try:
            _atomic_image(temporary / "color.png", color_bgr)
            _atomic_image(temporary / "overlay.png", overlay_bgr)
            _atomic_json(temporary / "observation.json", observation.as_dict())
            hashes = {
                name: _sha256(temporary / name)
                for name in ("color.png", "overlay.png", "observation.json")
            }
            os.replace(temporary, destination)
        except BaseException:
            for child in temporary.glob("*"):
                child.unlink(missing_ok=True)
            temporary.rmdir() if temporary.exists() else None
            raise
        sample = SampleRecord(sample_id, normalized, True, None, hashes)
        previous_outputs = dict(record.outputs)
        previous_state = record.state
        previous_updated_at = record.updated_at
        record.samples.append(sample)
        record.outputs = {"result": None, "local_report": None, "artifact": None}
        if record.state in {RunState.SOLVED, RunState.EXPORTED}:
            record.state = RunState.COLLECTING_CALIBRATION
        try:
            self.save(path, record)
        except BaseException:
            record.samples.pop()
            record.outputs = previous_outputs
            record.state = previous_state
            record.updated_at = previous_updated_at
            for child in destination.iterdir():
                child.unlink(missing_ok=True)
            destination.rmdir()
            raise
        return sample

    def load_observation(self, path: Path, sample: SampleRecord) -> SynchronizedObservation:
        selected = path / "samples" / sample.sample_id / "observation.json"
        if selected.is_symlink() or not selected.is_file():
            raise ValueError(f"样本观测不存在：{sample.sample_id}")
        if _sha256(selected) != sample.hashes["observation.json"]:
            raise ValueError(f"样本观测哈希不匹配：{sample.sample_id}")
        value = _strict_json(selected)
        return SynchronizedObservation.from_mapping(value)

    def verify(self, path: Path, record: RunRecord) -> list[str]:
        errors: list[str] = []
        expected_directories = {sample.sample_id for sample in record.samples}
        samples_root = path / "samples"
        if samples_root.is_symlink():
            return ["samples 目录不得是符号链接"]
        if samples_root.is_dir():
            unexpected = {
                item.name
                for item in samples_root.iterdir()
                if item.name not in expected_directories
            } - expected_directories
            errors.extend(f"存在未登记样本目录：{name}" for name in sorted(unexpected))
        for sample in record.samples:
            directory = samples_root / sample.sample_id
            if directory.is_symlink() or not directory.is_dir():
                errors.append(f"{sample.sample_id}: 样本目录缺失或无效")
                continue
            unexpected_members = {
                item.name for item in directory.iterdir()
            } - set(sample.hashes)
            errors.extend(
                f"{sample.sample_id}: 存在未登记成员 {name}"
                for name in sorted(unexpected_members)
            )
            for name, expected in sample.hashes.items():
                file = directory / name
                if file.is_symlink() or not file.is_file():
                    errors.append(f"{sample.sample_id}: 缺少 {name}")
                elif _sha256(file) != expected:
                    errors.append(f"{sample.sample_id}: {name} 的 SHA-256 不匹配")
        return errors

    def write_result(self, path: Path, result: CalibrationResult) -> Path:
        selected = path / "result.json"
        _atomic_json(selected, result.as_dict(include_diagnostics=True))
        return selected

    def load_result(self, path: Path) -> CalibrationResult:
        return CalibrationResult.from_mapping(_strict_json(path / "result.json"))


__all__ = ["FileRunRepository", "utc_now"]
