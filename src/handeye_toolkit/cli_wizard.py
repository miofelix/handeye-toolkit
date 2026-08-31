"""统一命令行向导；只编排产品入口，不承载标定业务状态。"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

Input = Callable[[str], str]
Printer = Callable[[str], None]

_DEFAULT_CONFIG_VALUES = {
    "mode": "eye-to-hand",
    "model": "piper",
    "firmware_profile": "v188",
    "squares": (12, 9),
    "square_size_mm": 15.0,
    "marker_size_mm": 11.25,
    "dictionary": "DICT_5X5_1000",
}


def _prompt_text(
    label: str,
    *,
    default: str | None,
    input_fn: Input,
    print_fn: Printer,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input_fn(f"{label}{suffix}：").strip()
        selected = value or (default or "")
        if selected and not any(character.isspace() for character in selected):
            return selected
        print_fn(f"{label}不能为空，且不能包含空格。")


def _prompt_choice(
    label: str,
    choices: tuple[str, ...],
    *,
    default: str,
    input_fn: Input,
    print_fn: Printer,
) -> str:
    options = "/".join(choices)
    while True:
        value = input_fn(f"{label}（{options}）[{default}]：").strip().lower() or default
        if value in choices:
            return value
        print_fn(f"请输入以下选项之一：{options}。")


def _prompt_int(
    label: str,
    *,
    default: int,
    minimum: int,
    input_fn: Input,
    print_fn: Printer,
) -> int:
    while True:
        value = input_fn(f"{label} [{default}]：").strip() or str(default)
        try:
            selected = int(value)
        except ValueError:
            selected = minimum - 1
        if selected >= minimum:
            return selected
        print_fn(f"{label}必须是不小于 {minimum} 的整数。")


def _prompt_float(
    label: str,
    *,
    default: float,
    input_fn: Input,
    print_fn: Printer,
) -> float:
    while True:
        value = input_fn(f"{label} [{default:g}]：").strip() or str(default)
        try:
            selected = float(value)
        except ValueError:
            selected = 0.0
        if selected > 0.0 and math.isfinite(selected):
            return selected
        print_fn(f"{label}必须是正有限数值。")


def _prompt_path(
    label: str,
    *,
    default: str | Path | None,
    input_fn: Input,
    print_fn: Printer,
) -> Path | None:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input_fn(f"{label}{suffix}（输入 b 返回）：").strip()
        if value.lower() in {"b", "back", "q", "quit"}:
            return None
        if value or default is not None:
            return Path(value or str(default)).expanduser().resolve()
        print_fn(f"{label}不能为空。")


def _prompt_yes_no(label: str, *, default: bool, input_fn: Input) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input_fn(f"{label}{suffix}：").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _config_summary(config: Any, *, effective_channel: str | None = None) -> str:
    document = config.as_dict()
    camera = document["camera"]
    piper = document["piper"]
    target = document["target"]
    channel = str(piper["can_channel"])
    if effective_channel is not None and effective_channel != channel:
        channel = f"{channel}（本次任务覆盖为 {effective_channel}）"
    if "serial_number" in camera:
        camera_summary = f"DaBai 相机序列号：{camera['serial_number']}"
    else:
        camera_summary = f"相机适配器：{camera['adapter']}；来源：{camera['source_id']}"
    return "\n".join(
        (
            "配置摘要：",
            f"  模式：{document['mode']}；策略：{document['policy']}",
            f"  {camera_summary}",
            f"  Piper：{piper['model']}；固件：{piper['firmware_profile']}；CAN：{channel}",
            f"  ChArUco：{target['squares'][0]} x {target['squares'][1]} 方格；"
            f"方格 {target['square_size_mm']} mm；Marker {target['marker_size_mm']} mm；"
            f"字典 {target['dictionary']}",
        )
    )


def collect_product_config(
    existing: Any | None = None,
    *,
    can_channel: str | None = None,
    input_fn: Input = input,
    print_fn: Printer = print,
) -> Any:
    """交互收集并严格校验产品配置。"""

    from .app.config import validate_product_config

    previous = existing.as_dict() if existing is not None else {}
    camera = previous.get("camera", {})
    piper = previous.get("piper", {})
    target = previous.get("target", {})
    squares = target.get("squares", _DEFAULT_CONFIG_VALUES["squares"])
    if not isinstance(squares, (list, tuple)) or len(squares) != 2:
        squares = _DEFAULT_CONFIG_VALUES["squares"]

    print_fn("请填写配置。尖括号值必须替换为现场设备信息。")
    mode = _prompt_choice(
        "标定模式",
        ("eye-to-hand", "eye-in-hand"),
        default=str(previous.get("mode", _DEFAULT_CONFIG_VALUES["mode"])),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    serial = _prompt_text(
        "DaBai 相机序列号",
        default=camera.get("serial_number"),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    model = _prompt_choice(
        "Piper 型号",
        ("piper", "piper_h", "piper_l", "piper_x"),
        default=str(piper.get("model", _DEFAULT_CONFIG_VALUES["model"])),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    firmware = _prompt_choice(
        "Piper 固件档案",
        ("default", "v183", "v188", "v189"),
        default=str(
            piper.get("firmware_profile", _DEFAULT_CONFIG_VALUES["firmware_profile"])
        ),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    channel = _prompt_text(
        "Piper CAN 通道编号",
        default=can_channel or piper.get("can_channel"),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    print_fn("标定板参数必须与现场实物或证书一致。")
    squares_x = _prompt_int(
        "横向方格数",
        default=int(squares[0]),
        minimum=2,
        input_fn=input_fn,
        print_fn=print_fn,
    )
    squares_y = _prompt_int(
        "纵向方格数",
        default=int(squares[1]),
        minimum=2,
        input_fn=input_fn,
        print_fn=print_fn,
    )
    while True:
        square_size = _prompt_float(
            "方格边长（mm）",
            default=float(target.get("square_size_mm", _DEFAULT_CONFIG_VALUES["square_size_mm"])),
            input_fn=input_fn,
            print_fn=print_fn,
        )
        marker_size = _prompt_float(
            "Marker 边长（mm）",
            default=float(target.get("marker_size_mm", _DEFAULT_CONFIG_VALUES["marker_size_mm"])),
            input_fn=input_fn,
            print_fn=print_fn,
        )
        if marker_size < square_size:
            break
        print_fn("Marker 边长必须小于方格边长，请重新填写尺寸。")
    dictionary = _prompt_text(
        "ChArUco 字典",
        default=str(target.get("dictionary", _DEFAULT_CONFIG_VALUES["dictionary"])),
        input_fn=input_fn,
        print_fn=print_fn,
    )
    document = {
        "mode": mode,
        "policy": "standard",
        "camera": {"serial_number": serial},
        "piper": {
            "model": model,
            "firmware_profile": firmware,
            "can_channel": channel,
        },
        "target": {
            "squares": [squares_x, squares_y],
            "square_size_mm": square_size,
            "marker_size_mm": marker_size,
            "dictionary": dictionary,
        },
    }
    config = validate_product_config(document)
    print_fn(_config_summary(config))
    return config


def _save_config(
    config: Any,
    path: Path,
    *,
    input_fn: Input,
    print_fn: Printer,
) -> Path | None:
    from .app.config import write_product_config

    if path.exists() and not _prompt_yes_no(
        f"配置已存在，确认覆盖 {path}？", default=False, input_fn=input_fn
    ):
        print_fn("未覆盖已有配置。")
        return None
    saved = write_product_config(config, path, force=path.exists())
    print_fn(f"配置已保存：{saved}")
    return saved


def _resolve_new_config(
    config_path: str | Path | None,
    *,
    can_channel: str | None,
    input_fn: Input,
    print_fn: Printer,
) -> Any | None:
    from .app.config import DEFAULT_CONFIG_PATH, load_product_config

    if config_path is not None:
        selected = Path(config_path).expanduser().resolve()
        if not selected.is_file():
            raise FileNotFoundError(f"配置不存在：{selected}")
        config = load_product_config(selected)
        print_fn(_config_summary(config, effective_channel=can_channel))
        return config

    default_path = DEFAULT_CONFIG_PATH.expanduser().resolve()
    if not default_path.is_file():
        config = collect_product_config(can_channel=can_channel, input_fn=input_fn, print_fn=print_fn)
        _save_config(config, default_path, input_fn=input_fn, print_fn=print_fn)
        return config

    try:
        current = load_product_config(default_path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print_fn(f"默认配置无效：{exc}")
        while True:
            answer = input_fn("1) 换路径  2) 重新填写  3) 返回：").strip().lower()
            if answer in {"3", "b", "back", "q"}:
                return None
            if answer == "2":
                config = collect_product_config(
                    can_channel=can_channel, input_fn=input_fn, print_fn=print_fn
                )
                if _save_config(config, default_path, input_fn=input_fn, print_fn=print_fn):
                    return config
                return None
            if answer == "1":
                path = _prompt_path(
                    "配置路径", default=None, input_fn=input_fn, print_fn=print_fn
                )
                if path is None:
                    return None
                try:
                    config = load_product_config(path)
                except (OSError, RuntimeError, TypeError, ValueError) as path_exc:
                    print_fn(f"配置无法使用：{path_exc}")
                    continue
                print_fn(_config_summary(config, effective_channel=can_channel))
                return config
            print_fn("请输入 1、2 或 3。")

    print_fn(_config_summary(current, effective_channel=can_channel))
    while True:
        answer = input_fn("1) 使用  2) 换路径  3) 重新填写  4) 返回：").strip().lower()
        if answer in {"1", "use", ""}:
            return current
        if answer in {"4", "b", "back", "q"}:
            return None
        if answer == "2":
            path = _prompt_path(
                "配置路径", default=None, input_fn=input_fn, print_fn=print_fn
            )
            if path is None:
                return None
            try:
                config = load_product_config(path)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                print_fn(f"配置无法使用：{exc}")
                continue
            print_fn(_config_summary(config, effective_channel=can_channel))
            return config
        if answer == "3":
            config = collect_product_config(
                current, can_channel=can_channel, input_fn=input_fn, print_fn=print_fn
            )
            if _save_config(config, default_path, input_fn=input_fn, print_fn=print_fn):
                return config
            return None
        print_fn("请输入 1、2、3 或 4。")


def _display_available() -> bool:
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if sys.platform == "darwin":
        has_display = True
    if not has_display:
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def choose_ui(
    requested: str | None,
    *,
    input_fn: Input,
    print_fn: Printer,
) -> str:
    if requested == "cli":
        return "cli"
    available = _display_available()
    if requested == "gui":
        if not available:
            raise RuntimeError("当前环境没有可用桌面，GUI 不可用；请使用 --ui cli")
        return "gui"
    if requested == "auto":
        if available:
            return "gui"
        print_fn("当前环境受限，没有可用桌面，GUI 不可用，将使用终端向导。")
        return "cli"
    if not available:
        print_fn("当前环境受限，没有可用桌面，GUI 不可用，将使用终端向导。")
        return "cli"
    while True:
        answer = input_fn("请选择操作方式：1) 终端向导  2) 图形界面 [1]：").strip().lower()
        if answer in {"", "1", "cli", "c"}:
            return "cli"
        if answer in {"2", "gui", "g"}:
            return "gui"
        print_fn("请输入 1 或 2。")


def _discover_resumable_runs(output_root: str | Path, *, print_fn: Printer) -> list[tuple[Path, Any]]:
    from .adapters.filesystem import FileRunRepository
    from .domain import RunState

    root = Path(output_root).expanduser().resolve()
    if not root.is_dir():
        return []
    repository = FileRunRepository()
    found: list[tuple[Path, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_dir() or not (path / "session.json").is_file():
            continue
        try:
            loaded_path, record = repository.load(path)
        except (OSError, TypeError, ValueError) as exc:
            print_fn(f"忽略无效任务 {path}：{exc}")
            continue
        if record.state is not RunState.CLOSED:
            found.append((loaded_path, record))
    found.sort(key=lambda item: (item[1].updated_at, item[1].run_id), reverse=True)
    return found


def _run_summary(path: Path, record: Any) -> str:
    from .domain import SampleRole

    calibration = sum(
        1 for sample in record.samples if sample.role is SampleRole.CALIBRATION and sample.included
    )
    validation = sum(
        1 for sample in record.samples if sample.role is SampleRole.VALIDATION and sample.included
    )
    return (
        f"{record.run_id} · {record.plan.mode.value} · 状态 {record.state.value} · "
        f"标定 {calibration}/{record.plan.sampling.calibration_target} · "
        f"验证 {validation}/{record.plan.sampling.validation_target} · "
        f"更新于 {record.updated_at} · {path}"
    )


def _choose_resume_path(
    output_root: str | Path,
    *,
    input_fn: Input,
    print_fn: Printer,
) -> Path | None:
    from .adapters.filesystem import FileRunRepository
    from .domain import RunState

    candidates = _discover_resumable_runs(output_root, print_fn=print_fn)
    if candidates:
        print_fn("可恢复任务：")
        for index, (path, record) in enumerate(candidates, start=1):
            print_fn(f"  {index}) {_run_summary(path, record)}")
    else:
        print_fn(f"在 {Path(output_root).expanduser().resolve()} 中没有可恢复任务。")
    prompt = "选择任务编号、输入其他任务路径，或输入 b 返回："
    while True:
        answer = input_fn(prompt).strip()
        if answer.lower() in {"b", "back", "q", "quit"}:
            return None
        if answer.isdigit() and candidates:
            index = int(answer) - 1
            if 0 <= index < len(candidates):
                return candidates[index][0]
            print_fn("请输入列表中的任务编号，或输入任务路径。")
            continue
        selected = Path(answer).expanduser().resolve()
        try:
            path, record = FileRunRepository().load(selected)
        except (OSError, TypeError, ValueError) as exc:
            print_fn(f"任务无法恢复：{exc}")
            continue
        if record.state is RunState.CLOSED:
            print_fn("任务已关闭，不能恢复。")
            continue
        print_fn(f"已选择：{_run_summary(path, record)}")
        return path


def _run_calibration(
    *,
    config_path: str | Path | None,
    resume_path: str | Path | None,
    can_channel: str | None,
    ui: str | None,
    output_root: str | Path,
    artifact_output: str | Path | None,
    input_fn: Input,
    print_fn: Printer,
) -> int:
    if resume_path is not None and can_channel is not None:
        raise ValueError("恢复任务不能使用 --can-channel 覆盖")
    from .app.bootstrap import create_product_run, resume_product_run
    from .app.controller import CalibrationController

    if resume_path is not None:
        selected_ui = choose_ui(ui, input_fn=input_fn, print_fn=print_fn)
        run = resume_product_run(resume_path)
    else:
        config = _resolve_new_config(
            config_path,
            can_channel=can_channel,
            input_fn=input_fn,
            print_fn=print_fn,
        )
        if config is None:
            return 0
        if can_channel is not None:
            print_fn(_config_summary(config, effective_channel=can_channel))
            if not _prompt_yes_no(
                "确认本次任务使用覆盖后的 Piper CAN 通道？",
                default=False,
                input_fn=input_fn,
            ):
                print_fn("未确认临时通道，本次任务未创建。")
                return 0
        selected_ui = choose_ui(ui, input_fn=input_fn, print_fn=print_fn)
        run = create_product_run(
            config=config,
            can_channel=can_channel,
            output_root=output_root,
        )
    controller = CalibrationController(run)
    try:
        if selected_ui == "gui":
            from .gui import run_gui

            return run_gui(controller)
        from .cli import run_cli

        return run_cli(
            controller,
            artifact_output=artifact_output,
            input_fn=input_fn,
            print_fn=print_fn,
        )
    finally:
        controller.close()


def _run_config_menu(*, input_fn: Input, print_fn: Printer) -> int:
    from .app.config import DEFAULT_CONFIG_PATH, load_product_config

    path = _prompt_path(
        "配置保存路径", default=DEFAULT_CONFIG_PATH, input_fn=input_fn, print_fn=print_fn
    )
    if path is None:
        return 0
    existing = None
    if path.is_file():
        try:
            existing = load_product_config(path)
            print_fn(_config_summary(existing))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print_fn(f"已有配置无效，将使用内置默认值重新填写：{exc}")
    config = collect_product_config(existing, input_fn=input_fn, print_fn=print_fn)
    _save_config(config, path, input_fn=input_fn, print_fn=print_fn)
    return 0


def _run_verify_menu(*, input_fn: Input, print_fn: Printer) -> int:
    path = _prompt_path(
        "交付包 ZIP 路径", default=None, input_fn=input_fn, print_fn=print_fn
    )
    if path is None:
        return 0
    recompute = _prompt_yes_no("是否进行脱敏 evidence 离线复算？", default=False, input_fn=input_fn)
    from .artifacts import load_verified_artifact, recompute_verified_artifact

    artifact = load_verified_artifact(path)
    recomputed = recompute_verified_artifact(artifact) if recompute else None
    result = artifact.result
    print_fn(
        f"制品校验通过：{result.mode.value} · "
        f"run_id {result.run_id} · 质量门禁 {'通过' if result.quality.passed else '未通过'}"
        + (" · 离线复算一致" if recomputed is not None else "")
    )
    return 0


def _menu(
    *,
    ui: str | None,
    output_root: str | Path,
    artifact_output: str | Path | None,
    input_fn: Input,
    print_fn: Printer,
) -> int:
    while True:
        print_fn("\nHandeye Toolkit 统一向导")
        print_fn("1) 新建标定")
        print_fn("2) 恢复任务")
        print_fn("3) 配置管理")
        print_fn("4) 校验交付包")
        print_fn("q) 退出")
        answer = input_fn("请选择操作：").strip().lower()
        if answer in {"q", "quit", "exit"}:
            return 0
        try:
            if answer == "1":
                code = _run_calibration(
                    config_path=None,
                    resume_path=None,
                    can_channel=None,
                    ui=ui,
                    output_root=output_root,
                    artifact_output=artifact_output,
                    input_fn=input_fn,
                    print_fn=print_fn,
                )
            elif answer == "2":
                selected = _choose_resume_path(
                    output_root, input_fn=input_fn, print_fn=print_fn
                )
                if selected is None:
                    continue
                code = _run_calibration(
                    config_path=None,
                    resume_path=selected,
                    can_channel=None,
                    ui=ui,
                    output_root=output_root,
                    artifact_output=artifact_output,
                    input_fn=input_fn,
                    print_fn=print_fn,
                )
            elif answer == "3":
                code = _run_config_menu(input_fn=input_fn, print_fn=print_fn)
            elif answer == "4":
                code = _run_verify_menu(input_fn=input_fn, print_fn=print_fn)
            else:
                print_fn("请输入 1、2、3、4 或 q。")
                continue
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            print_fn(f"本次操作未完成：{exc}")
            continue
        if code != 0:
            print_fn(f"本次操作结束，返回码 {code}。")


def run_wizard(
    *,
    config_path: str | Path | None = None,
    resume_path: str | Path | None = None,
    can_channel: str | None = None,
    ui: str | None = None,
    output_root: str | Path = "runs",
    artifact_output: str | Path | None = None,
    input_fn: Input = input,
    print_fn: Printer = print,
) -> int:
    """运行统一向导；带操作预设时直接执行对应流程。"""

    if config_path is not None and resume_path is not None:
        raise ValueError("--config 与 --resume 不能同时使用")
    has_preset = config_path is not None or resume_path is not None or can_channel is not None
    try:
        if has_preset:
            return _run_calibration(
                config_path=config_path,
                resume_path=resume_path,
                can_channel=can_channel,
                ui=ui,
                output_root=output_root,
                artifact_output=artifact_output,
                input_fn=input_fn,
                print_fn=print_fn,
            )
        return _menu(
            ui=ui,
            output_root=output_root,
            artifact_output=artifact_output,
            input_fn=input_fn,
            print_fn=print_fn,
        )
    except (KeyboardInterrupt, EOFError):
        print_fn("操作已中断，当前任务记录已保留。")
        return 130


__all__ = ["choose_ui", "collect_product_config", "run_wizard"]
