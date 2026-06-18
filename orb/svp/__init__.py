"""Session Volume Profile (SVP) "Edge Rotation" strategy — standalone, parallel
to the ORB engine, off by default.

Public surface (expanded in strategy phase):
    VolumeProfile    incremental price->tick-volume histogram (POC/VAH/VAL/HVN/LVN)
    ProfileLevels    immutable snapshot of a profile's levels
    PriorProfile     yesterday's completed profile carried into today
    Shape            profile morphology enum (D/P/b/B/I)
    compute_lot      structural-stop dynamic position sizing
    SVP_MAGIC        distinct MT5 magic so the babysitter scopes to SVP tickets

Pure core: ``profile``/``levels``/``sizing``/``config``/``strategy`` are stdlib
only, no I/O, no asyncio (mirrors the ORB engine purity contract).
"""

from .config import SvpConfig
from .levels import PriorProfile, ProfileLevels, Shape
from .profile import VolumeProfile
from .sizing import compute_lot
from .strategy import SvpEngine

# Distinct from the 2026061x ORB family (XAU 10, US100 11, US500 12, XAG 13) so
# ``Mt5Broker.my_positions()`` and the babysitter only ever see SVP positions.
SVP_MAGIC = 20260620

__all__ = [
    "SvpEngine",
    "SvpConfig",
    "VolumeProfile",
    "ProfileLevels",
    "PriorProfile",
    "Shape",
    "compute_lot",
    "SVP_MAGIC",
]

__version__ = "0.1.0"
