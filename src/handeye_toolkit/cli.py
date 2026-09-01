"""职责明确、可脚本化的中文命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Input = Callable[[str], str]
Printer = Callable[[str], None]


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self.add_argument("-h", "--help", action="help", help="显示帮助信息并退出")

    def format_help(self) -> str:
        return (
            super()
            .format_help()
            .replace("usage:", "用法：")
            .replace("options:", "选项：")
            .replace("positional arguments:", "位置参数：")
        )


def _add_ui(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ui",
        dest="command_ui",
        choices=("auto", "cli", "gui"),
        default=argparse.SUPPRESS,
        help="交互界面；默认根据桌面环境自动选择",
    )
    parser.add_argument(
        "--artifact-output",
        dest="command_artifact_output",
        default=argparse.SUPPRESS,
        help="交付包输出路径；默认保存在任务目录",
    )


def _add_root_wizard_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="新建任务使用的配置路径；未指定时进入配置向导",
    )
    group.add_argument(
        "--resume",
        dest="resume_path",
        default=argparse.SUPPRESS,
        help="直接恢复已有任务目录或 session.json",
    )
    parser.add_argument(
        "--ui",
        choices=("auto", "cli", "gui"),
        default=argparse.SUPPRESS,
        help="交互界面；省略时有桌面会询问，否则使用终端向导",
    )
    parser.add_argument(
        "--can-channel",
        default=argparse.SUPPRESS,
        help="本次新建任务临时覆盖 Piper CAN 通道（不回写配置）",
    )
    parser.add_argument(
        "--output-root",
        default=argparse.SUPPRESS,
        help="任务根目录（默认：runs）",
    )
    parser.add_argument(
        "--artifact-output",
        default=argparse.SUPPRESS,
        help="交付包输出路径；默认保存在任务目录",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        prog="handeye",
        description="多相机/Piper 手眼标定工具；Piper 仅用于读取状态和法兰反馈。",
    )
    _add_root_wizard_arguments(parser)
    commands = parser.add_subparsers(dest="command", metavar="命令")

    setup = commands.add_parser("setup", help="生成配置模板")
    setup.add_argument("--output", default="configs/handeye.yaml", help="模板保存路径")
    setup.add_argument("--force", action="store_true", help="明确覆盖已有模板")

    resume = commands.add_parser("resume", help="按任务快照恢复，不重新读取 YAML")
    resume.add_argument("run_dir", metavar="任务目录", help="已有任务目录")
    _add_ui(resume)

    verify = commands.add_parser("verify", help="校验交付包")
    verify.add_argument("artifact", metavar="交付包", help="交付包 ZIP 路径")
    verify.add_argument("--recompute", action="store_true", help="使用脱敏 evidence 离线复算")
    verify.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def _select_ui(requested: str) -> str:
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if sys.platform == "darwin":
        has_display = True
    if requested == "gui":
        if not has_display:
            raise RuntimeError("当前环境没有可用桌面，GUI 不可用；请使用 --ui cli")
        try:
            import tkinter  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("GUI 依赖不可用；请使用 --ui cli") from exc
        return "gui"
    if requested == "cli":
        return requested
    if has_display:
        try:
            import tkinter  # noqa: F401

            return "gui"
        except ImportError:
            pass
    return "cli"


def run_cli(
    controller,
    *,
    artifact_output: str | Path | None = None,
    input_fn: Input = input,
    print_fn: Printer = print,
) -> int:
    snapshot = controller.snapshot
    print_fn(f"任务记录：{snapshot.run_path}")
    if snapshot.quality_passed is True:
        artifact = controller.export(artifact_output)
        print_fn(f"交付包已生成：{artifact}")
        return 0
    if snapshot.quality_passed is False:
        print_fn("上次结果未通过质量门禁，将重新进入标定采集阶段。")
        controller.run.reopen()

    print_fn(
        "安全边界：程序只读取相机图像和 Piper 状态/法兰反馈；"
        "不会发送运动、使能、失能、复位、急停或夹爪命令。"
    )
    if input_fn("确认机械臂由现场人员手动移动，并接受上述边界？[y/N]：").strip().lower() != "y":
        print_fn("安全确认未完成，任务未连接硬件。")
        return 1
    controller.acknowledge_and_connect()
    target_confirmed = False

    while (role := controller.current_role()) is not None:
        snapshot = controller.snapshot
        current = (
            snapshot.calibration_count
            if role.value == "calibration"
            else snapshot.validation_count
        )
        target = (
            snapshot.calibration_target
            if role.value == "calibration"
            else snapshot.validation_target
        )
        role_label = "标定" if role.value == "calibration" else "验证"
        print_fn(f"\n{role_label}样本：{current}/{target}")
        answer = input_fn("手动摆好并保持静止后回车检查姿态；输入 q 结束：").strip().lower()
        if answer == "q":
            print_fn("已退出，当前任务记录和已采集证据已保留。")
            return 0
        assessment = controller.assess_pose()
        print_fn(
            f"稳定性：{assessment.translation_drift_m * 1000:.3f} mm / "
            f"{assessment.rotation_drift_deg:.3f}°；"
            f"覆盖：{assessment.position_span_m:.3f} m / {assessment.rotation_span_deg:.2f}°"
        )
        if not assessment.stable or not assessment.novel:
            print_fn("；".join(assessment.suggestions))
            continue
        input_fn("姿态可用，回车采集候选图像：")
        try:
            candidate = controller.capture()
        except Exception as exc:
            from .application import CaptureRejected

            if isinstance(exc, CaptureRejected):
                print_fn(f"候选未生成：{exc}")
                continue
            raise
        metrics = "；".join(
            f"{name}={value}" for name, value in candidate.detection.quality.metrics.items()
        )
        print_fn("检测指标：" + (metrics or "无额外指标"))
        if candidate.reasons:
            print_fn("候选未通过：" + "；".join(candidate.reasons))
            controller.reject(candidate.candidate_id, "候选未通过自动质量检查")
            continue
        if not target_confirmed:
            identity = candidate.detection.identity
            if identity is None:
                controller.reject(candidate.candidate_id, "缺少目标身份证据")
                print_fn("缺少目标身份证据，候选未保存。")
                continue
            answer = input_fn(
                f"目标核对：期望 {identity.expected}，检测 {identity.observed}。"
                "确认与现场实物一致？[y/N]："
            )
            if answer.strip().lower() != "y":
                controller.reject(candidate.candidate_id, "用户未确认目标身份")
                continue
            target_confirmed = True
        if input_fn("保存该候选？[y/N]：").strip().lower() == "y":
            sample = controller.accept(
                candidate.candidate_id,
                confirm_target=target_confirmed,
            )
            print_fn(f"已保存：{sample.sample_id}")
        else:
            controller.reject(candidate.candidate_id, "CLI 用户拒绝候选")

    result = controller.solve()
    print_fn(
        f"计算完成：{result.quality.method}；验证 RMS "
        f"{result.quality.validation_rms['translation_m'] * 1000:.3f} mm / "
        f"{result.quality.validation_rms['rotation_deg']:.3f}°"
    )
    if not result.quality.passed:
        print_fn("质量门禁未通过：" + "；".join(result.quality.reasons))
        print_fn(f"本地报告：{controller.run.path / 'report.local.html'}")
        return 1
    artifact = controller.export(artifact_output)
    print_fn(f"本地报告：{controller.run.path / 'report.local.html'}")
    print_fn(f"交付包已生成：{artifact}")
    return 0


def _run_product(args: argparse.Namespace) -> int:
    from .app.bootstrap import resume_product_run
    from .app.controller import CalibrationController

    run = resume_product_run(args.run_dir)
    controller = CalibrationController(run)
    try:
        if _select_ui(getattr(args, "command_ui", "auto")) == "gui":
            from .gui import run_gui

            return run_gui(controller)
        return run_cli(
            controller,
            artifact_output=getattr(args, "command_artifact_output", None),
        )
    finally:
        controller.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command is None:
            return run_wizard(
                config_path=getattr(args, "config", None),
                resume_path=getattr(args, "resume_path", None),
                can_channel=getattr(args, "can_channel", None),
                ui=getattr(args, "ui", None),
                output_root=getattr(args, "output_root", "runs"),
                artifact_output=getattr(args, "artifact_output", None),
            )
        root_options = {
            name
            for name in ("config", "resume_path", "can_channel", "ui", "output_root", "artifact_output")
            if hasattr(args, name)
        }
        if root_options and args.command in {"setup", "resume", "verify"}:
            raise ValueError("根级向导参数只能直接用于 handeye，不能与显式子命令组合")
        if args.command == "setup":
            from .app.config import write_config_template

            path = write_config_template(args.output, force=args.force)
            print(f"配置模板已生成：{path}")
            return 0
        if args.command == "verify":
            from .artifacts import load_verified_artifact, recompute_verified_artifact

            artifact = load_verified_artifact(args.artifact)
            recomputed = recompute_verified_artifact(artifact) if args.recompute else None
            payload = {
                "artifact": str(artifact.bundle_path),
                "mode": artifact.result.mode.value,
                "run_id": artifact.result.run_id,
                "quality_passed": artifact.result.quality.passed,
                "recomputed": recomputed is not None,
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(
                    f"制品校验通过：{payload['mode']}"
                    + (" · 离线复算一致" if recomputed is not None else "")
                )
            return 0
        return _run_product(args)
    except (KeyboardInterrupt, EOFError):
        print("操作已中断。", file=sys.stderr)
        return 130
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"操作未完成：{exc}", file=sys.stderr)
        return 1


def run_wizard(**kwargs: Any) -> int:
    """延迟加载并运行统一命令行向导。"""

    from .cli_wizard import run_wizard as implementation

    return implementation(**kwargs)


__all__ = ["build_parser", "main", "run_cli", "run_wizard"]
