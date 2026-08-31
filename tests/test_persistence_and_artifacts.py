from __future__ import annotations

import json
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pytest
from conftest import TARGET_PARAMETERS, synthetic_samples

from handeye_toolkit import cli
from handeye_toolkit.adapters.filesystem import FileRunRepository
from handeye_toolkit.algorithms.solver import OpenCvHandeyeSolver
from handeye_toolkit.artifacts import (
    ARTIFACT_MEMBERS,
    CalibrationArtifactExporter,
    HtmlReportRenderer,
    load_verified_artifact,
    recompute_verified_artifact,
)
from handeye_toolkit.domain import (
    AcquisitionDescriptor,
    CalibrationMode,
    ComponentDescriptor,
    SampleRecord,
)


def acquisition() -> AcquisitionDescriptor:
    return AcquisitionDescriptor(
        camera=ComponentDescriptor(
            "fake-camera",
            "camera-placeholder",
            {"camera": "camera"},
            {},
        ),
        flange=ComponentDescriptor(
            "fake-flange",
            "channel-placeholder",
            {"base": "base", "flange": "flange"},
            {"allow_robot_control": False},
        ),
        target=ComponentDescriptor(
            "charuco",
            "target-placeholder",
            {"target": "target"},
            TARGET_PARAMETERS,
        ),
    )


def create_artifact(tmp_path: Path) -> tuple[Path, Path, bytes]:
    plan, generated, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=43)
    repository = FileRunRepository()
    run_path, record = repository.create(
        plan=plan,
        acquisition=acquisition(),
        output_root=tmp_path / "runs",
    )
    stored: list[tuple[SampleRecord, object]] = []
    pixels = np.zeros((4, 5, 3), dtype=np.uint8)
    for sample, observation in generated:
        indexed = repository.add_sample(
            run_path,
            record,
            observation=observation,
            color_bgr=pixels,
            overlay_bgr=pixels,
            role=sample.role.value,
        )
        stored.append((indexed, observation))
    record.confirmations["target"] = {
        "fingerprint": "a" * 64,
        "confirmed_at": "2026-08-30T00:00:00.000Z",
    }
    repository.save(run_path, record)
    result = OpenCvHandeyeSolver().solve(
        run_id=record.run_id,
        plan=plan,
        samples=stored,  # type: ignore[arg-type]
        target_confirmed=True,
    )
    result_path = repository.write_result(run_path, result)
    renderer = HtmlReportRenderer()
    renderer.render_local(
        run_path=run_path,
        record=record,
        result=result,
        observations=stored,  # type: ignore[arg-type]
    )
    bundle = CalibrationArtifactExporter(renderer).export(
        run_path=run_path,
        record=record,
        result=result,
        observations=stored,  # type: ignore[arg-type]
    )
    return bundle, run_path, result_path.read_bytes()


def rewrite_zip(source: Path, destination: Path, mutator) -> None:
    with zipfile.ZipFile(source) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    mutator(members)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, payload in members:
            archive.writestr(info, payload)


def test_session_is_strict_atomic_and_hash_verified(tmp_path: Path) -> None:
    plan, generated, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=47)
    repository = FileRunRepository()
    run_path, record = repository.create(
        plan=plan,
        acquisition=acquisition(),
        output_root=tmp_path,
    )
    sample, observation = generated[0]
    indexed = repository.add_sample(
        run_path,
        record,
        observation=observation,
        color_bgr=np.zeros((2, 2, 3), dtype=np.uint8),
        overlay_bgr=np.zeros((2, 2, 3), dtype=np.uint8),
        role=sample.role.value,
    )
    assert repository.verify(run_path, record) == []
    assert repository.load_observation(run_path, indexed).as_dict() == observation.as_dict()

    evidence = run_path / "samples" / indexed.sample_id / "observation.json"
    evidence.write_text("{}\n", encoding="utf-8")
    assert any("SHA-256" in error for error in repository.verify(run_path, record))
    with pytest.raises(ValueError, match="哈希"):
        repository.load_observation(run_path, indexed)

    session_path = run_path / "session.json"
    document = json.loads(session_path.read_text(encoding="utf-8"))
    document["unexpected"] = True
    session_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="session 字段"):
        repository.load(run_path)


def test_sample_index_rolls_back_when_session_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, generated, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=53)
    repository = FileRunRepository()
    run_path, record = repository.create(
        plan=plan,
        acquisition=acquisition(),
        output_root=tmp_path,
    )

    def fail_save(_path: Path, _record) -> None:
        raise OSError("模拟提交失败")

    monkeypatch.setattr(repository, "save", fail_save)
    sample, observation = generated[0]
    with pytest.raises(OSError, match="模拟提交失败"):
        repository.add_sample(
            run_path,
            record,
            observation=observation,
            color_bgr=np.zeros((2, 2, 3), dtype=np.uint8),
            overlay_bgr=np.zeros((2, 2, 3), dtype=np.uint8),
            role=sample.role.value,
        )
    assert record.samples == []
    samples_root = run_path / "samples"
    assert not samples_root.exists() or list(samples_root.iterdir()) == []


def test_run_lock_is_exclusive(tmp_path: Path) -> None:
    plan, _, _ = synthetic_samples(CalibrationMode.EYE_TO_HAND, seed=59)
    repository = FileRunRepository()
    run_path, _ = repository.create(
        plan=plan,
        acquisition=acquisition(),
        output_root=tmp_path,
    )
    with repository.lock(run_path):
        with pytest.raises(RuntimeError, match="另一个进程"):
            with repository.lock(run_path):
                pass


def test_artifact_is_minimal_sanitized_and_recomputable(tmp_path: Path) -> None:
    bundle, run_path, result_bytes = create_artifact(tmp_path)
    verified = load_verified_artifact(bundle)

    assert "schema_version" not in verified.manifest
    assert verified.manifest["producer"] == {"name": "handeye-toolkit"}
    assert tuple(verified.manifest["files"]) == (
        "evidence.json",
        "report.html",
        "result.json",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert tuple(archive.namelist()) == ARTIFACT_MEMBERS
        assert archive.read("result.json") == result_bytes
        evidence = json.loads(archive.read("evidence.json"))
        result = json.loads(archive.read("result.json"))
        assert "schema_version" not in evidence
        assert "schema_version" not in result
        assert result["tool"] == {"name": "handeye-toolkit"}
        first = evidence["observations"][0]
        assert set(first) == {"id", "role", "base_to_flange", "camera_to_target"}
        combined = b"".join(archive.read(name) for name in archive.namelist())
    assert b"color.png" not in combined
    assert b"overlay.png" not in combined
    assert b"camera-placeholder" not in combined
    assert b"channel-placeholder" not in combined
    assert str(run_path).encode() not in combined
    assert "-v" not in bundle.name

    recomputed = recompute_verified_artifact(verified)
    assert np.allclose(recomputed.transform.matrix, verified.result.transform.matrix, atol=1e-8)


def test_artifact_rejects_hash_tampering_and_duplicate_members(tmp_path: Path) -> None:
    bundle, _, _ = create_artifact(tmp_path)
    tampered = tmp_path / "tampered.zip"

    def change_report(members) -> None:
        for index, (info, payload) in enumerate(members):
            if info.filename == "report.html":
                members[index] = (info, payload + b"tampered")

    rewrite_zip(bundle, tampered, change_report)
    with pytest.raises(ValueError, match="大小|SHA-256"):
        load_verified_artifact(tampered)

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(bundle) as source, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as destination:
            for info in source.infolist():
                destination.writestr(info, source.read(info))
            destination.writestr("report.html", b"duplicate")
    with pytest.raises(ValueError, match="重复成员|成员列表"):
        load_verified_artifact(duplicate)


def test_artifact_validation_import_is_lightweight() -> None:
    code = """
import sys
import handeye_toolkit.artifacts
for name in ('cv2', 'scipy', 'pyorbbecsdk', 'pyAgxArm'):
    assert name not in sys.modules, (name, sorted(sys.modules))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_verify_cli_supports_machine_output_and_recomputation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, _, _ = create_artifact(tmp_path)
    assert cli.main(["verify", str(bundle), "--recompute", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "schema_version" not in payload
    assert payload["quality_passed"] is True
    assert payload["recomputed"] is True
