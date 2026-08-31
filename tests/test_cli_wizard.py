from __future__ import annotations

from pathlib import Path

import pytest
from conftest import product_document

from handeye_toolkit import cli
from handeye_toolkit.app.config import validate_product_config, write_product_config
from handeye_toolkit.cli_wizard import (
    _config_summary,
    _run_calibration,
    choose_ui,
    collect_product_config,
    run_wizard,
)


def answers(values: list[str]):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def test_menu_has_four_operations_and_returns_to_menu() -> None:
    messages: list[str] = []
    input_fn = answers(["x", "q"])

    assert run_wizard(input_fn=input_fn, print_fn=messages.append) == 0
    assert any("1) 新建标定" in message for message in messages)
    assert any("2) 恢复任务" in message for message in messages)
    assert any("3) 配置管理" in message for message in messages)
    assert any("4) 校验交付包" in message for message in messages)
    assert any("请输入 1、2、3、4 或 q" in message for message in messages)


def test_collect_product_config_uses_defaults_and_strict_values() -> None:
    messages: list[str] = []
    config = collect_product_config(
        input_fn=answers(
            ["", "camera-001", "", "", "can0", "", "", "", "", ""]
        ),
        print_fn=messages.append,
    )

    assert config.as_dict() == {
        "mode": "eye-to-hand",
        "policy": "standard",
        "camera": {"serial_number": "camera-001"},
        "piper": {
            "model": "piper",
            "firmware_profile": "v188",
            "can_channel": "can0",
        },
        "target": {
            "squares": [12, 9],
            "square_size_mm": 15.0,
            "marker_size_mm": 11.25,
            "dictionary": "DICT_5X5_1000",
        },
    }
    assert any("配置摘要" in message for message in messages)


def test_config_summary_supports_generic_camera_descriptor() -> None:
    document = product_document()
    document["camera"] = {
        "adapter": "realsense-d435",
        "source_id": "<camera-serial>",
        "settings": {"warmup_frames": 0},
    }
    summary = _config_summary(validate_product_config(document))
    assert "相机适配器：realsense-d435" in summary


def test_collect_product_config_rejects_invalid_marker_size_then_retries() -> None:
    messages: list[str] = []
    config = collect_product_config(
        input_fn=answers(
            [
                "",
                "camera-001",
                "",
                "",
                "can0",
                "",
                "",
                "15",
                "15",
                "15",
                "10",
                "",
            ]
        ),
        print_fn=messages.append,
    )

    assert config.square_size_mm == 15.0
    assert config.marker_size_mm == 10.0
    assert any("Marker 边长必须小于方格边长" in message for message in messages)


def test_config_writer_round_trips_and_requires_force(tmp_path: Path) -> None:
    config = validate_product_config(product_document())
    path = tmp_path / "handeye.yaml"
    assert write_product_config(config, path) == path.resolve()
    assert "schema_version" not in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_product_config(config, path)


def test_ui_selection_warns_without_display_and_rejects_forced_gui(monkeypatch) -> None:
    monkeypatch.setattr("handeye_toolkit.cli_wizard._display_available", lambda: False)
    messages: list[str] = []

    assert choose_ui(None, input_fn=lambda _prompt: "", print_fn=messages.append) == "cli"
    assert any("当前环境受限" in message for message in messages)
    with pytest.raises(RuntimeError, match="GUI 不可用"):
        choose_ui("gui", input_fn=lambda _prompt: "", print_fn=messages.append)


def test_forced_gui_failure_does_not_create_a_new_task(monkeypatch, tmp_path: Path) -> None:
    config = validate_product_config(product_document())
    created = False

    monkeypatch.setattr(
        "handeye_toolkit.cli_wizard._resolve_new_config",
        lambda *_args, **_kwargs: config,
    )

    def fail_if_created(**_kwargs: object) -> object:
        nonlocal created
        created = True
        raise AssertionError("不应在 UI 预检失败前创建任务")

    monkeypatch.setattr("handeye_toolkit.app.bootstrap.create_product_run", fail_if_created)

    def fail_ui(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("GUI 不可用")

    monkeypatch.setattr("handeye_toolkit.cli_wizard.choose_ui", fail_ui)

    with pytest.raises(RuntimeError, match="GUI 不可用"):
        _run_calibration(
            config_path=None,
            resume_path=None,
            can_channel=None,
            ui="gui",
            output_root=tmp_path,
            artifact_output=None,
            input_fn=lambda _prompt: "",
            print_fn=lambda _message: None,
        )
    assert not created


def test_root_menu_forwards_ui_and_paths_to_calibration(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_calibration(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("handeye_toolkit.cli_wizard._run_calibration", fake_calibration)
    assert run_wizard(
        ui="cli",
        output_root=tmp_path,
        artifact_output=tmp_path / "artifact.zip",
        input_fn=answers(["1", "q"]),
        print_fn=lambda _message: None,
    ) == 0
    assert calls[0]["ui"] == "cli"
    assert calls[0]["output_root"] == tmp_path
    assert calls[0]["artifact_output"] == tmp_path / "artifact.zip"


def test_temporary_channel_override_is_used_for_task_only(monkeypatch, tmp_path: Path) -> None:
    config = validate_product_config(product_document())
    captured: list[object] = []

    monkeypatch.setattr(
        "handeye_toolkit.cli_wizard._resolve_new_config",
        lambda *_args, **_kwargs: config,
    )

    class FakeRun:
        pass

    def fake_create_product_run(**kwargs: object) -> FakeRun:
        config = kwargs["config"]
        can_channel = kwargs["can_channel"]
        captured.append(config if can_channel is None else config.__class__(
            config.mode,
            config.policy,
            config.camera_serial_number,
            config.piper_model,
            config.piper_firmware_profile,
            can_channel,
            config.squares,
            config.square_size_mm,
            config.marker_size_mm,
            config.dictionary,
        ))
        return FakeRun()

    class FakeController:
        def __init__(self, run: FakeRun) -> None:
            self.run = run

        def close(self) -> None:
            pass

    monkeypatch.setattr("handeye_toolkit.app.bootstrap.create_product_run", fake_create_product_run)
    monkeypatch.setattr("handeye_toolkit.app.controller.CalibrationController", FakeController)
    monkeypatch.setattr("handeye_toolkit.cli_wizard.choose_ui", lambda *_args, **_kwargs: "cli")
    monkeypatch.setattr("handeye_toolkit.cli.run_cli", lambda *_args, **_kwargs: 0)

    assert _run_calibration(
        config_path=None,
        resume_path=None,
        can_channel="can17",
        ui="cli",
        output_root=tmp_path,
        artifact_output=None,
        input_fn=answers(["y"]),
        print_fn=lambda _message: None,
    ) == 0
    assert len(captured) == 1
    assert captured[0].piper_can_channel == "can17"
    assert config.piper_can_channel == "<can-channel>"


def test_main_bare_command_delegates_to_wizard(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "run_wizard", lambda **kwargs: calls.append(kwargs) or 0)

    assert cli.main(["--config", "site.yaml", "--ui", "cli"]) == 0
    assert calls[0]["config_path"] == "site.yaml"
    assert calls[0]["ui"] == "cli"


def test_explicit_resume_command_keeps_its_run_path_and_ui(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []

    monkeypatch.setattr(
        "handeye_toolkit.app.bootstrap.resume_product_run",
        lambda run_ref: calls.append(run_ref) or object(),
    )

    class FakeController:
        def __init__(self, run: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("handeye_toolkit.app.controller.CalibrationController", FakeController)
    monkeypatch.setattr("handeye_toolkit.cli.run_cli", lambda *_args, **_kwargs: 0)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    run_path = tmp_path / "run"
    assert cli.main(["resume", str(run_path), "--ui", "cli"]) == 0
    assert calls == [str(run_path)]
