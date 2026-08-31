"""Piper 手眼标定产品配置与组合入口。"""

from .config import (
    DEFAULT_CONFIG_PATH,
    SUPPORTED_CAMERA_ADAPTERS,
    ProductConfig,
    load_product_config,
    validate_product_config,
    write_config_template,
    write_product_config,
)
from .policy import STANDARD_PROFILE, resolve_plan

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProductConfig",
    "SUPPORTED_CAMERA_ADAPTERS",
    "STANDARD_PROFILE",
    "load_product_config",
    "resolve_plan",
    "validate_product_config",
    "write_product_config",
    "write_config_template",
]
