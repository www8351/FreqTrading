"""SMC strategy package."""

from .config import SmcConfig
from .strategy import SmcEngine

SMC_MAGIC = 20260621

__all__ = ["SmcConfig", "SmcEngine", "SMC_MAGIC"]
