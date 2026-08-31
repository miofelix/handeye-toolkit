from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from handeye_toolkit import cli
from handeye_toolkit.app.controller import CalibrationController
from handeye_toolkit.gui import CalibrationGui, run_gui

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "handeye_toolkit"


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def relative_import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module
    }


def test_bare_command_starts_unified_wizard_and_subcommands_are_explicit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_wizard(**kwargs: object) -> int:
        calls.append(kwargs)
        return 17

    monkeypatch.setattr(cli, "run_wizard", fake_wizard)
    assert cli.main([]) == 17
    assert calls == [
        {
            "config_path": None,
            "resume_path": None,
            "can_channel": None,
            "ui": None,
            "output_root": "runs",
            "artifact_output": None,
        }
    ]

    parser = cli.build_parser()
    assert parser.parse_args(["resume", "runs/run-placeholder"]).command == "resume"
    assert parser.parse_args(["--config", "handeye.yaml"]).config == "handeye.yaml"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["calibrate"])
    assert exc.value.code == 2


def test_setup_command_writes_template(tmp_path: Path, capsys) -> None:
    selected = tmp_path / "handeye.yaml"
    assert cli.main(["setup", "--output", str(selected)]) == 0
    assert "schema_version" not in selected.read_text(encoding="utf-8")
    assert "配置模板已生成" in capsys.readouterr().out
    assert cli.main(["setup", "--output", str(selected)]) == 1
    assert "拒绝覆盖" in capsys.readouterr().err


def test_module_entrypoint_is_lightweight() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "handeye_toolkit", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "新建任务" in completed.stdout or "setup" in completed.stdout
    assert "--version" not in completed.stdout


def test_cli_and_gui_take_the_same_application_controller() -> None:
    gui_signature = inspect.signature(CalibrationGui.__init__)
    launch_signature = inspect.signature(run_gui)
    assert gui_signature.parameters["controller"].annotation in {
        CalibrationController,
        "CalibrationController",
    }
    assert launch_signature.parameters["controller"].annotation in {
        CalibrationController,
        "CalibrationController",
    }
    assert "controller" in inspect.signature(cli.run_cli).parameters
    assert "bootstrap" not in relative_import_targets(PACKAGE / "gui.py")


def test_domain_ports_and_application_dependency_directions() -> None:
    standard_library = set(sys.stdlib_module_names)
    for path in (PACKAGE / "domain").glob("*.py"):
        assert imported_roots(path) <= standard_library | {"numpy"}

    for path in (PACKAGE / "ports").glob("*.py"):
        assert relative_import_targets(path) <= {"domain"}

    for path in (PACKAGE / "application").glob("*.py"):
        assert relative_import_targets(path) <= {"domain", "ports", "capture", "models", "run"}

    for path in (PACKAGE / "artifacts").glob("*.py"):
        assert not (
            relative_import_targets(path)
            & {"adapters", "app", "application"}
        )


def test_public_contract_imports_do_not_load_runtime_backends() -> None:
    code = """
import sys
import handeye_toolkit.domain
import handeye_toolkit.ports
import handeye_toolkit.application
import handeye_toolkit.composition
for name in ('cv2', 'scipy', 'pyorbbecsdk', 'pyAgxArm'):
    assert name not in sys.modules, name
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_piper_adapter_calls_only_read_only_robot_methods() -> None:
    path = PACKAGE / "adapters" / "piper.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    robot_calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
            and owner.attr == "_robot"
        ) or (isinstance(owner, ast.Name) and owner.id == "robot"):
            robot_calls.add(node.func.attr)
    assert robot_calls == {"connect", "get_arm_status", "get_flange_pose", "is_ok"}

    forbidden = {
        "enable",
        "disable",
        "reset",
        "emergency_stop",
        "move_joint",
        "move_pose",
        "set_gripper",
        "motion_ctrl",
    }
    call_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert call_names.isdisjoint(forbidden)
