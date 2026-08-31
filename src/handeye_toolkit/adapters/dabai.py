"""DaBai DC1 相机采集适配器。"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

import cv2
import numpy as np

from ..domain import CameraFrame, CameraIntrinsics, CaptureStamp


@dataclass(frozen=True, slots=True)
class DaBaiDeviceInfo:
    """不启动视频流即可读取的 DaBai/Orbbec 设备身份。"""

    sdk_index: int
    serial_number: str
    name: str
    uid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sdk_index": self.sdk_index,
            "serial_number": self.serial_number,
            "name": self.name,
            "uid": self.uid,
        }


@dataclass(frozen=True, slots=True)
class DaBaiUsbOccupant:
    """打开目标 DaBai USB 节点的外部进程。"""

    pid: int
    process_name: str
    device_path: str


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _usb_nodes_for_serial(
    serial_number: str,
    *,
    sysfs_root: Path = Path("/sys/bus/usb/devices"),
    usb_root: Path = Path("/dev/bus/usb"),
) -> tuple[Path, ...]:
    """从 sysfs 把相机序列号映射到当前 USB 字符设备节点。"""

    try:
        devices = tuple(sysfs_root.iterdir())
    except OSError:
        return ()
    nodes: list[Path] = []
    for device in devices:
        if _read_text(device / "serial") != serial_number:
            continue
        bus = _read_text(device / "busnum")
        number = _read_text(device / "devnum")
        try:
            node = usb_root / f"{int(str(bus)):03d}" / f"{int(str(number)):03d}"
        except (TypeError, ValueError):
            continue
        if node.exists():
            nodes.append(node.resolve())
    return tuple(dict.fromkeys(nodes))


def find_dabai_usb_occupants(
    serial_number: str,
    *,
    sysfs_root: str | Path = "/sys/bus/usb/devices",
    usb_root: str | Path = "/dev/bus/usb",
    proc_root: str | Path = "/proc",
    current_pid: int | None = None,
) -> tuple[DaBaiUsbOccupant, ...]:
    """在不打开相机的前提下查找占用目标 DaBai UVC 节点的进程。"""

    nodes = set(
        _usb_nodes_for_serial(
            str(serial_number),
            sysfs_root=Path(sysfs_root),
            usb_root=Path(usb_root),
        )
    )
    if not nodes:
        return ()
    own_pid = os.getpid() if current_pid is None else int(current_pid)
    try:
        processes = tuple(Path(proc_root).iterdir())
    except OSError:
        return ()
    occupants: dict[tuple[int, str], DaBaiUsbOccupant] = {}
    for process in processes:
        if not process.name.isdecimal() or int(process.name) == own_pid:
            continue
        try:
            descriptors = tuple((process / "fd").iterdir())
        except OSError:
            continue
        matched: Path | None = None
        for descriptor in descriptors:
            try:
                target = Path(os.readlink(descriptor)).resolve()
            except OSError:
                continue
            if target in nodes:
                matched = target
                break
        if matched is None:
            continue
        pid = int(process.name)
        executable = None
        try:
            executable = Path(os.readlink(process / "exe")).name
        except OSError:
            pass
        process_name = executable or _read_text(process / "comm") or "[未知进程]"
        occupant = DaBaiUsbOccupant(pid, process_name, str(matched))
        occupants[(pid, str(matched))] = occupant
    return tuple(occupants[key] for key in sorted(occupants))


def ensure_dabai_usb_available(serial_number: str) -> None:
    """在进入不稳定的 SDK 错误路径前拒绝已被占用的直连相机。"""

    occupants = find_dabai_usb_occupants(serial_number)
    if not occupants:
        return
    details = "；".join(
        f"PID {item.pid} ({item.process_name}) 打开 {item.device_path}" for item in occupants
    )
    raise RuntimeError(
        f"DaBai {serial_number} 的 USB 接口已被其他进程占用：{details}。"
        "pyorbbecsdk 直连不能与 ROS astra_camera 或其他采集程序同时使用；"
        "请先在启动它的终端停止对应相机节点，再重新运行手眼标定。"
    )


def _enum_value(namespace: Any, name: str, *, kind: str) -> Any:
    value = getattr(namespace, name.upper(), None)
    if value is None:
        raise ValueError(f"pyorbbecsdk 不支持配置的 {kind}: {name}")
    return value


def _format_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()
    text = str(value).upper()
    for candidate in (
        "MJPG",
        "MJPEG",
        "YUYV",
        "YUY2",
        "UYVY",
        "I420",
        "NV12",
        "NV21",
        "BGRA",
        "RGBA",
        "BGR",
        "RGB",
    ):
        if candidate in text:
            return candidate
    return text


def _reshape_color(data: np.ndarray, height: int, width: int, channels: int) -> np.ndarray:
    expected = height * width * channels
    if data.size != expected:
        raise RuntimeError(f"DaBai 彩色帧数据长度异常：期望 {expected}，实际 {data.size}")
    return data.reshape(height, width, channels)


def _orbbec_frame_to_bgr(frame: Any) -> np.ndarray:
    """将 Orbbec 常见彩色格式转换为连续的 BGR uint8 图像。"""

    width, height = int(frame.get_width()), int(frame.get_height())
    data = np.frombuffer(frame.get_data(), dtype=np.uint8)
    color_format = _format_name(frame.get_format())
    if color_format == "RGB":
        image = cv2.cvtColor(_reshape_color(data, height, width, 3), cv2.COLOR_RGB2BGR)
    elif color_format == "BGR":
        image = _reshape_color(data, height, width, 3)
    elif color_format in {"MJPG", "MJPEG"}:
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("DaBai MJPG 彩色帧解码失败")
    elif color_format in {"YUYV", "YUY2"}:
        image = cv2.cvtColor(_reshape_color(data, height, width, 2), cv2.COLOR_YUV2BGR_YUY2)
    elif color_format == "UYVY":
        image = cv2.cvtColor(_reshape_color(data, height, width, 2), cv2.COLOR_YUV2BGR_UYVY)
    elif color_format == "I420":
        image = cv2.cvtColor(data.reshape(height * 3 // 2, width), cv2.COLOR_YUV2BGR_I420)
    elif color_format in {"NV12", "NV21"}:
        conversion = cv2.COLOR_YUV2BGR_NV12 if color_format == "NV12" else cv2.COLOR_YUV2BGR_NV21
        image = cv2.cvtColor(data.reshape(height * 3 // 2, width), conversion)
    elif color_format in {"BGRA", "RGBA"}:
        conversion = cv2.COLOR_BGRA2BGR if color_format == "BGRA" else cv2.COLOR_RGBA2BGR
        image = cv2.cvtColor(_reshape_color(data, height, width, 4), conversion)
    else:
        raise RuntimeError(f"DaBai 彩色帧格式暂不支持：{color_format}")
    if image.shape[:2] != (height, width):
        raise RuntimeError(
            f"DaBai 彩色帧尺寸异常：声明 {(height, width)}，解码得到 {image.shape[:2]}"
        )
    return np.ascontiguousarray(image, dtype=np.uint8).copy()


def _orbbec_intrinsics(color_frame: Any, pipeline: Any) -> Any:
    profile = None
    for getter_name in ("get_stream_profile", "get_profile"):
        getter = getattr(color_frame, getter_name, None)
        if getter is not None:
            profile = getter()
            break
    if profile is not None:
        video_profile = (
            profile.as_video_stream_profile()
            if hasattr(profile, "as_video_stream_profile")
            else profile
        )
        for getter_name in ("get_intrinsic", "get_intrinsics"):
            getter = getattr(video_profile, getter_name, None)
            if getter is not None:
                return getter()
    get_camera_param = getattr(pipeline, "get_camera_param", None)
    if get_camera_param is not None:
        camera_param = get_camera_param()
        for attribute in ("rgb_intrinsic", "color_intrinsic"):
            intrinsics = getattr(camera_param, attribute, None)
            if intrinsics is not None:
                return intrinsics
    raise RuntimeError("无法从 pyorbbecsdk 读取 DaBai 彩色相机内参")


def _orbbec_profile_value(frame: Any, name: str) -> Any:
    """从 Orbbec 帧的 profile 读取可选的 fps/format 等元数据。"""

    for getter_name in ("get_stream_profile", "get_profile"):
        getter = getattr(frame, getter_name, None)
        if getter is None:
            continue
        try:
            profile = getter()
        except Exception:
            continue
        if hasattr(profile, "as_video_stream_profile"):
            try:
                profile = profile.as_video_stream_profile()
            except Exception:
                pass
        value_getter = getattr(profile, f"get_{name}", None)
        if value_getter is not None:
            try:
                return value_getter()
            except Exception:
                pass
        value = getattr(profile, name, None)
        if value is not None:
            return value() if callable(value) else value
    return None


def _orbbec_color_profile(color_frame: Any) -> Any:
    for getter_name in ("get_stream_profile", "get_profile"):
        getter = getattr(color_frame, getter_name, None)
        if getter is None:
            continue
        try:
            profile = getter()
            return (
                profile.as_video_stream_profile()
                if hasattr(profile, "as_video_stream_profile")
                else profile
            )
        except Exception:
            continue
    return None


def _orbbec_distortion(color_frame: Any, pipeline: Any) -> tuple[str | None, tuple[float, ...]]:
    """读取彩色流 Brown 畸变，并转换为 OpenCV ``k1,k2,p1,p2,k3...`` 顺序。"""

    distortion = None
    profile = _orbbec_color_profile(color_frame)
    if profile is not None:
        for getter_name in ("get_distortion", "get_distort"):
            getter = getattr(profile, getter_name, None)
            if getter is not None:
                try:
                    distortion = getter()
                    break
                except Exception:
                    pass
    if distortion is None:
        get_camera_param = getattr(pipeline, "get_camera_param", None)
        if get_camera_param is not None:
            try:
                params = get_camera_param()
                distortion = getattr(params, "rgb_distortion", None) or getattr(
                    params, "color_distortion", None
                )
            except Exception:
                distortion = None
    if distortion is None:
        return None, ()

    model_value = getattr(distortion, "model", None)
    model = str(getattr(model_value, "name", model_value)) if model_value is not None else "brown"
    fields = ("k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6")
    if all(hasattr(distortion, field) for field in fields[:4]):
        coefficients = tuple(float(getattr(distortion, field, 0.0)) for field in fields)
        return model, coefficients if any(coefficients[5:]) else coefficients[:5]

    raw = getattr(distortion, "coeffs", distortion)
    try:
        coefficients = tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return model, ()
    return model, coefficients


def _device_info_text(info: Any, *names: str) -> str | None:
    for name in names:
        if isinstance(info, Mapping):
            value = info.get(name)
        else:
            value = getattr(info, name, None)
        if value is None:
            continue
        try:
            value = value() if callable(value) else value
        except Exception:
            continue
        if isinstance(value, bytes):
            try:
                text = value.decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError:
                text = value.decode(errors="replace").strip()
        else:
            text = str(value).strip() if value is not None else ""
        if text:
            return text
    return None


def _enumerate_dabai_devices(context: Any) -> list[tuple[DaBaiDeviceInfo, Any]]:
    devices = context.query_devices()
    count_getter = getattr(devices, "get_count", None)
    if count_getter is None:
        raise RuntimeError("当前 pyorbbecsdk 无法读取 DaBai 设备数量")
    count = int(count_getter())
    result: list[tuple[DaBaiDeviceInfo, Any]] = []
    serial_indices: dict[str, int] = {}
    for index in range(count):
        try:
            device = devices.get_device_by_index(index)
            info = device.get_device_info()
        except Exception as exc:
            raise RuntimeError(f"读取 DaBai SDK index={index} 的设备信息失败") from exc
        serial = _device_info_text(info, "get_serial_number", "serial_number", "serial")
        if not serial:
            raise RuntimeError(f"DaBai SDK index={index} 未返回有效序列号")
        if serial in serial_indices:
            raise RuntimeError(
                f"DaBai 序列号 {serial} 在 SDK index={serial_indices[serial]} 和 {index} 重复"
            )
        serial_indices[serial] = index
        result.append(
            (
                DaBaiDeviceInfo(
                    sdk_index=index,
                    serial_number=serial,
                    name=_device_info_text(info, "get_name", "name") or "Orbbec",
                    uid=_device_info_text(info, "get_uid", "get_device_uid", "uid", "device_uid"),
                ),
                device,
            )
        )
    # 保持 SDK 原始顺序，使显示编号和记录的 SDK index 可在现场直接核对。
    return result


def discover_dabai_devices(ob_module: Any = None) -> list[DaBaiDeviceInfo]:
    """只读枚举在线 DaBai；不会创建 Pipeline 或启动视频流。"""

    ob = ob_module
    if ob is None:
        try:
            import pyorbbecsdk as imported_ob

            ob = imported_ob
        except ImportError as exc:  # pragma: no cover - hardware-only path
            raise RuntimeError("枚举 DaBai 需要安装 pyorbbecsdk") from exc
    if not hasattr(ob, "Context"):
        raise RuntimeError("当前 pyorbbecsdk 不支持 DaBai 设备枚举")
    try:
        context = ob.Context()
        return [info for info, _ in _enumerate_dabai_devices(context)]
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"DaBai 设备枚举失败：{exc}") from exc


class DaBaiCamera:
    """DaBai DC1 的 Orbbec SDK v1.3.2 直连采集器。"""

    def __init__(self, config: Mapping[str, Any] | None = None, *, ob_module: Any = None) -> None:
        self.config = dict(config or {})
        self.config["depth_format"] = "none"
        self.config["align_to_color"] = False
        self._ob = ob_module
        self._context = None
        self._device = None
        self._device_info: DaBaiDeviceInfo | None = None
        self._pipeline = None
        self._align_filter = None

    def _pipeline_for_device(self, ob: Any) -> Any:
        serial = self.config.get("serial_number") or self.config.get("serial")
        if not serial:
            return ob.Pipeline()
        if not hasattr(ob, "Context"):
            raise RuntimeError("当前 pyorbbecsdk 不支持按序列号选择 DaBai 设备")
        if self._context is None:
            self._context = ob.Context()
        device = None
        for info, candidate in _enumerate_dabai_devices(self._context):
            if info.serial_number == str(serial):
                self._device_info = info
                device = candidate
                break
        if device is None:
            raise RuntimeError(f"未找到所选 DaBai 设备：{serial}")
        self._device = device
        return ob.Pipeline(device)

    def _stream_profile(self, pipeline: Any, sensor: Any, prefix: str) -> Any:
        ob = self._ob
        assert ob is not None
        profiles = pipeline.get_stream_profile_list(sensor)
        width = self.config.get("width")
        height = self.config.get("height")
        fps = self.config.get("fps")
        format_name = self.config.get(f"{prefix}_format")
        dimensions = (width, height, fps)
        if all(value is None for value in dimensions):
            if format_name is not None:
                raise ValueError(f"设置 dabai.{prefix}_format 时必须同时填写 width、height 和 fps")
            return profiles.get_default_video_stream_profile()
        if any(value is None for value in dimensions):
            raise ValueError("dabai.width、height 和 fps 必须全部填写或全部为 null")
        if format_name is None:
            format_value = getattr(ob.OBFormat, "UNKNOWN", None)
            if format_value is None:
                raise RuntimeError(f"当前 pyorbbecsdk 要求显式配置 dabai.{prefix}_format")
        else:
            format_value = _enum_value(ob.OBFormat, str(format_name), kind=f"{prefix}_format")
        try:
            return profiles.get_video_stream_profile(
                int(cast(Any, width)),
                int(cast(Any, height)),
                format_value,
                int(cast(Any, fps)),
            )
        except Exception as exc:
            raise RuntimeError(
                f"DaBai 不支持请求的 {prefix} 流：{width}x{height}@{fps}, format={format_name or 'UNKNOWN'}"
            ) from exc

    def open(self) -> None:
        if self._pipeline is not None:
            return
        serial = self.config.get("serial_number") or self.config.get("serial")
        if serial:
            ensure_dabai_usb_available(str(serial))
        if self._ob is None:
            try:
                import pyorbbecsdk as ob
            except ImportError as exc:  # pragma: no cover - hardware-only path
                raise RuntimeError("DaBai DC1 采集需要安装 pyorbbecsdk") from exc
            self._ob = ob
        ob = self._ob
        pipeline = None
        pipeline_started = False
        try:
            pipeline = self._pipeline_for_device(ob)
            stream_config = ob.Config()
            color_profile = self._stream_profile(pipeline, ob.OBSensorType.COLOR_SENSOR, "color")
            stream_config.enable_stream(color_profile)
            align_filter = None
            if str(self.config.get("depth_format", "none")).lower() not in {"none", "", "null"}:
                depth_profile = self._stream_profile(
                    pipeline, ob.OBSensorType.DEPTH_SENSOR, "depth"
                )
                stream_config.enable_stream(depth_profile)
                if not bool(self.config.get("align_to_color", True)):
                    raise ValueError("DaBai 启用深度时必须对齐到彩色流")
                try:
                    align_filter = ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
                except TypeError:  # pragma: no cover
                    align_filter = ob.AlignFilter(ob.OBStreamType.COLOR_STREAM)
            pipeline.start(stream_config)
            pipeline_started = True
            self._pipeline = pipeline
            self._align_filter = align_filter
            for _ in range(max(0, int(self.config.get("warmup_frames", 30)))):
                self._wait_for_frames()
        except Exception:
            if pipeline_started and pipeline is not None:
                try:
                    pipeline.stop()
                except Exception:
                    pass
            self._pipeline = self._align_filter = None
            self._device = self._context = self._device_info = None
            raise

    def _wait_for_frames(self) -> Any:
        assert self._pipeline is not None
        timeout_ms = int(self.config.get("timeout_ms", 5000))
        frames = self._pipeline.wait_for_frames(timeout_ms)
        if frames is None:
            raise TimeoutError(f"DaBai 在 {timeout_ms} ms 内未返回 RGB-D 帧")
        if self._align_filter is not None:
            frames = self._align_filter.process(frames)
            if frames is None:
                raise RuntimeError("DaBai 彩色/深度对齐失败")
            if hasattr(frames, "as_frame_set"):
                frames = frames.as_frame_set()
        return frames

    def capture(self) -> CameraFrame:
        if self._pipeline is None:
            self.open()
        assert self._pipeline is not None
        started_ns = time.monotonic_ns()
        frames = self._wait_for_frames()
        depth_frame, color_frame = frames.get_depth_frame(), frames.get_color_frame()
        if color_frame is None:
            raise RuntimeError("DaBai 未返回有效的彩色帧")
        color_bgr = _orbbec_frame_to_bgr(color_frame)
        raw_depth = None
        scale_mm = None
        if depth_frame is not None:
            width, height = int(depth_frame.get_width()), int(depth_frame.get_height())
            raw_depth = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
            if raw_depth.size != width * height:
                raise RuntimeError(
                    f"DaBai 深度帧数据长度异常：期望 {width * height}，实际 {raw_depth.size}"
                )
            raw_depth = raw_depth.reshape(height, width).copy()
            if raw_depth.shape != color_bgr.shape[:2]:
                raise RuntimeError(
                    f"DaBai 对齐后彩色/深度尺寸不一致：color={color_bgr.shape[:2]}, depth={raw_depth.shape}"
                )
            scale_mm = float(depth_frame.get_depth_scale())
            if not np.isfinite(scale_mm) or scale_mm <= 0:
                raise RuntimeError(f"DaBai 返回了无效深度尺度：{scale_mm}")
        intrinsics = _orbbec_intrinsics(color_frame, self._pipeline)
        distortion_model, distortion_coefficients = _orbbec_distortion(color_frame, self._pipeline)
        metadata = {
            "name": self.config.get("name", "DaBai DC1"),
            "backend": "pyorbbecsdk",
            "frame_id": self.config.get("frame_id"),
            "serial_configured": bool(
                self.config.get("serial_number") or self.config.get("serial")
            ),
            "color_format": _format_name(color_frame.get_format()),
            "depth_format": (
                _format_name(depth_frame.get_format())
                if depth_frame is not None and hasattr(depth_frame, "get_format")
                else None
            ),
            "width": int(color_bgr.shape[1]),
            "height": int(color_bgr.shape[0]),
            "fps": _orbbec_profile_value(color_frame, "fps") or self.config.get("fps"),
            "aligned_to": "color" if depth_frame is not None else None,
            "depth_scale_mm_per_unit": scale_mm,
        }
        get_timestamp = getattr(color_frame, "get_timestamp", None)
        if callable(get_timestamp):
            try:
                metadata["device_timestamp"] = float(get_timestamp())
            except (TypeError, ValueError):
                metadata["device_timestamp"] = None
        get_sequence = getattr(color_frame, "get_frame_number", None)
        if not callable(get_sequence):
            get_sequence = getattr(color_frame, "get_index", None)
        if callable(get_sequence):
            try:
                metadata["sequence"] = int(get_sequence())
            except (TypeError, ValueError):
                metadata["sequence"] = None
        if self._device_info is not None:
            metadata.update(self._device_info.as_dict())
        received_ns = time.monotonic_ns()
        device_timestamp = metadata.get("device_timestamp")
        try:
            parsed_timestamp = None if device_timestamp is None else float(device_timestamp)
            if parsed_timestamp is not None and not math.isfinite(parsed_timestamp):
                parsed_timestamp = None
        except (TypeError, ValueError):
            parsed_timestamp = None
        sequence = metadata.get("sequence")
        try:
            parsed_sequence = None if sequence is None else int(sequence)
        except (TypeError, ValueError):
            parsed_sequence = None
        stream = {
            key: metadata.get(key)
            for key in ("backend", "color_format", "width", "height", "fps")
        }
        return CameraFrame(
            color_bgr=color_bgr,
            intrinsics=CameraIntrinsics(
                fx=float(intrinsics.fx),
                fy=float(intrinsics.fy),
                cx=float(intrinsics.cx),
                cy=float(intrinsics.cy),
                distortion_model=distortion_model,
                distortion_coefficients=distortion_coefficients,
            ),
            stamp=CaptureStamp(
                host_started_ns=started_ns,
                host_received_ns=received_ns,
                device_timestamp=parsed_timestamp,
                sequence=parsed_sequence,
            ),
            stream=stream,
        )

    def close(self) -> None:
        pipeline = self._pipeline
        self._pipeline = self._align_filter = None
        try:
            if pipeline is not None:
                pipeline.stop()
        finally:
            self._device = self._context = self._device_info = None

    def __enter__(self) -> "DaBaiCamera":
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


__all__ = [
    "DaBaiCamera",
    "DaBaiDeviceInfo",
    "DaBaiUsbOccupant",
    "discover_dabai_devices",
    "ensure_dabai_usb_available",
    "find_dabai_usb_occupants",
]
