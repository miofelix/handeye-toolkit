"""调用方中立的组件注册与采集装置组合 API。"""

from .builtins import create_builtin_registry
from .registry import ComponentRegistry
from .rig import ComponentRigFactory

__all__ = ["ComponentRegistry", "ComponentRigFactory", "create_builtin_registry"]
