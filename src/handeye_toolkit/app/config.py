"""Piper 手眼标定产品自身的严格 YAML 配置。"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from ..domain import AcquisitionDescriptor, CalibrationMode, ComponentDescriptor, JsonValue
from .policy import STANDARD_PROFILE, resolve_plan

DEFAULT_CONFIG_PATH = Path("configs/handeye.yaml")
SUPPORTED_CAMERA_ADAPTERS = {"dabai", "opencv-rgb", "realsense-d435"}


def _exact(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} 字段不符合 schema：缺少 {sorted(expected - actual)}；"
            f"多出 {sorted(actual - expected)}"
        )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是对象")
    return dict(value)


def _text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text or any(character.isspace() for character in text):
        raise ValueError(f"{label} 必须是无空白的非空字符串")
    return text


def _positive(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是正有限数值")
    result = float(value)  # type: ignore[arg-type]
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{label} 必须是正有限数值")
    return result


@dataclass(frozen=True, slots=True)
class ProductConfig:
    mode: CalibrationMode
    policy: str
    camera_serial_number: str
    piper_model: str
    piper_firmware_profile: str
    piper_can_channel: str
    squares: tuple[int, int]
    square_size_mm: float
    marker_size_mm: float
    dictionary: str
    camera_adapter: str = "dabai"
    camera_settings: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", CalibrationMode.parse(self.mode))
        if self.policy != STANDARD_PROFILE:
            raise ValueError(f"policy 必须为 {STANDARD_PROFILE}")
        object.__setattr__(
            self,
            "camera_serial_number",
            _text(self.camera_serial_number, "camera_serial_number"),
        )
        adapter = _text(self.camera_adapter, "camera_adapter")
        if adapter not in SUPPORTED_CAMERA_ADAPTERS:
            raise ValueError(f"camera_adapter 无效：{adapter}")
        component = ComponentDescriptor(
            adapter,
            self.camera_serial_number,
            {"camera": "camera"},
            self.camera_settings,
        )
        if adapter == "realsense-d435":
            from ..adapters.realsense import create_realsense_d435_camera

            create_realsense_d435_camera(component)
        elif adapter == "opencv-rgb":
            from ..adapters.opencv_rgb import create_opencv_rgb_camera

            create_opencv_rgb_camera(component)
        object.__setattr__(self, "camera_adapter", adapter)
        object.__setattr__(self, "camera_settings", component.settings)
        object.__setattr__(
            self,
            "piper_can_channel",
            _text(self.piper_can_channel, "piper_can_channel"),
        )
        if self.piper_model not in {"piper", "piper_h", "piper_l", "piper_x"}:
            raise ValueError("piper_model 无效")
        if self.piper_firmware_profile not in {"default", "v183", "v188", "v189"}:
            raise ValueError("piper_firmware_profile 无效")
        if len(self.squares) != 2 or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 2
            for item in self.squares
        ):
            raise ValueError("squares 必须是两个不小于 2 的整数")
        object.__setattr__(self, "squares", (int(self.squares[0]), int(self.squares[1])))
        square_size = _positive(self.square_size_mm, "square_size_mm")
        marker_size = _positive(self.marker_size_mm, "marker_size_mm")
        if marker_size >= square_size:
            raise ValueError("marker_size_mm 必须小于 square_size_mm")
        object.__setattr__(self, "square_size_mm", square_size)
        object.__setattr__(self, "marker_size_mm", marker_size)
        object.__setattr__(self, "dictionary", _text(self.dictionary, "dictionary"))

    @property
    def target_parameters(self) -> dict[str, JsonValue]:
        return {
            "squares_x": self.squares[0],
            "squares_y": self.squares[1],
            "square_length_m": self.square_size_mm / 1000.0,
            "marker_length_m": self.marker_size_mm / 1000.0,
            "dictionary": self.dictionary,
            "start_id": 0,
        }

    @property
    def camera_source_id(self) -> str:
        """返回通用相机来源 ID；保留旧字段名以兼容已有调用方。"""

        return self.camera_serial_number

    @property
    def plan(self):
        return resolve_plan(
            profile=self.policy,
            mode=self.mode,
            target_parameters=self.target_parameters,
        )

    @property
    def acquisition(self) -> AcquisitionDescriptor:
        camera_settings = dict(self.camera_settings)
        if self.camera_adapter == "dabai":
            camera_settings.setdefault("stream_profile", "default-color")
        return AcquisitionDescriptor(
            camera=ComponentDescriptor(
                self.camera_adapter,
                self.camera_serial_number,
                {"camera": "camera"},
                camera_settings,
            ),
            flange=ComponentDescriptor(
                "piper-readonly",
                self.piper_can_channel,
                {"base": "base", "flange": "flange"},
                {
                    "model": self.piper_model,
                    "firmware_profile": self.piper_firmware_profile,
                    "can_interface": "socketcan",
                    "allow_robot_control": False,
                },
            ),
            target=ComponentDescriptor(
                "charuco",
                "configured-target",
                {"target": "target"},
                self.target_parameters,
            ),
        )

    def as_dict(self) -> dict[str, JsonValue]:
        if self.camera_adapter == "dabai" and not self.camera_settings:
            camera: dict[str, JsonValue] = {"serial_number": self.camera_serial_number}
        else:
            settings = ComponentDescriptor(
                self.camera_adapter,
                self.camera_serial_number,
                {"camera": "camera"},
                self.camera_settings,
            ).as_dict()["settings"]
            camera = {
                "adapter": self.camera_adapter,
                "source_id": self.camera_serial_number,
                "settings": settings,
            }
        return {
            "mode": self.mode.value,
            "policy": self.policy,
            "camera": camera,
            "piper": {
                "model": self.piper_model,
                "firmware_profile": self.piper_firmware_profile,
                "can_channel": self.piper_can_channel,
            },
            "target": {
                "squares": list(self.squares),
                "square_size_mm": self.square_size_mm,
                "marker_size_mm": self.marker_size_mm,
                "dictionary": self.dictionary,
            },
        }


def validate_product_config(value: Mapping[str, object]) -> ProductConfig:
    document = dict(value)
    _exact(
        document,
        {"mode", "policy", "camera", "piper", "target"},
        "config",
    )
    mode = CalibrationMode.parse(str(document["mode"]))
    policy = _text(document["policy"], "config.policy")
    if policy != STANDARD_PROFILE:
        raise ValueError(f"config.policy 当前仅支持 {STANDARD_PROFILE}")

    camera = _mapping(document["camera"], "config.camera")
    if set(camera) == {"serial_number"}:
        camera_adapter = "dabai"
        camera_source_id = _text(camera["serial_number"], "config.camera.serial_number")
        camera_settings: dict[str, JsonValue] = {}
    else:
        _exact(camera, {"adapter", "source_id", "settings"}, "config.camera")
        camera_adapter = _text(camera["adapter"], "config.camera.adapter")
        if camera_adapter not in SUPPORTED_CAMERA_ADAPTERS:
            raise ValueError(f"config.camera.adapter 无效：{camera_adapter}")
        camera_source_id = _text(camera["source_id"], "config.camera.source_id")
        raw_camera_settings = _mapping(camera["settings"], "config.camera.settings")
        camera_settings = cast(dict[str, JsonValue], raw_camera_settings)

    piper = _mapping(document["piper"], "config.piper")
    _exact(piper, {"model", "firmware_profile", "can_channel"}, "config.piper")
    model = _text(piper["model"], "config.piper.model")
    if model not in {"piper", "piper_h", "piper_l", "piper_x"}:
        raise ValueError("config.piper.model 无效")
    firmware = _text(piper["firmware_profile"], "config.piper.firmware_profile")
    if firmware not in {"default", "v183", "v188", "v189"}:
        raise ValueError("config.piper.firmware_profile 无效")
    channel = _text(piper["can_channel"], "config.piper.can_channel")

    target = _mapping(document["target"], "config.target")
    _exact(target, {"squares", "square_size_mm", "marker_size_mm", "dictionary"}, "config.target")
    squares = target["squares"]
    if (
        not isinstance(squares, Sequence)
        or isinstance(squares, (str, bytes))
        or len(squares) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 2 for item in squares)
    ):
        raise ValueError("config.target.squares 必须是两个不小于 2 的整数")
    square_size = _positive(target["square_size_mm"], "config.target.square_size_mm")
    marker_size = _positive(target["marker_size_mm"], "config.target.marker_size_mm")
    if marker_size >= square_size:
        raise ValueError("config.target.marker_size_mm 必须小于 square_size_mm")
    dictionary = _text(target["dictionary"], "config.target.dictionary")

    result = ProductConfig(
        mode,
        policy,
        camera_source_id,
        model,
        firmware,
        channel,
        (int(squares[0]), int(squares[1])),
        square_size,
        marker_size,
        dictionary,
        camera_adapter,
        camera_settings,
    )
    result.plan
    return result


def load_product_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ProductConfig:
    selected = Path(path).expanduser().resolve()
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("读取 YAML 配置需要安装 PyYAML") from exc
    try:
        with selected.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except OSError as exc:
        raise OSError(f"无法读取配置：{selected}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("配置 YAML 顶层必须是对象")
    return validate_product_config(value)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_product_config(
    config: ProductConfig | Mapping[str, object],
    output_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    force: bool = False,
) -> Path:
    """将经过严格校验的产品配置写入 YAML 文件。"""

    normalized = config if isinstance(config, ProductConfig) else validate_product_config(config)
    selected = Path(output_path).expanduser().resolve()
    if selected.exists() and not force:
        raise FileExistsError(f"配置已存在，拒绝覆盖：{selected}")
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("写入 YAML 配置需要安装 PyYAML") from exc
    content = yaml.safe_dump(
        normalized.as_dict(),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    _atomic_text(selected, content)
    return selected


def write_config_template(
    output_path: str | Path = DEFAULT_CONFIG_PATH, *, force: bool = False
) -> Path:
    selected = Path(output_path).expanduser().resolve()
    if selected.exists() and not force:
        raise FileExistsError(f"配置已存在，拒绝覆盖：{selected}")
    content = files("handeye_toolkit.data").joinpath("handeye.yaml").read_text(encoding="utf-8")
    _atomic_text(selected, content)
    return selected


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProductConfig",
    "SUPPORTED_CAMERA_ADAPTERS",
    "load_product_config",
    "validate_product_config",
    "write_product_config",
    "write_config_template",
]
