"""本发行版内置组件的显式注册入口。"""

from __future__ import annotations

from .registry import ComponentRegistry


def create_builtin_registry() -> ComponentRegistry:
    """创建仅含本发行版受支持组件的新注册表。"""

    # 保持公共组合 API 轻量；仅在请求内置实现时导入运行时后端。
    from ..adapters.dabai import create_dabai_camera
    from ..adapters.piper import create_piper_flange_source
    from ..algorithms.charuco import create_charuco_detector

    return (
        ComponentRegistry()
        .register_camera("dabai", create_dabai_camera)
        .register_flange("piper-readonly", create_piper_flange_source)
        .register_target("charuco", create_charuco_detector)
    )


__all__ = ["create_builtin_registry"]
