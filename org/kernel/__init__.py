"""Kernel VER-OS: chasis genérico de departamento (ROADMAP.md F0)."""

from org.kernel.department import CapabilityError, Charter, Department
from org.kernel.events import Event, EventBus
from org.kernel.journal import DecisionJournal
from org.kernel.manifest import (
    AUTONOMY_LEVELS,
    Manifest,
    ManifestError,
    load_manifest,
    parse_manifest,
)
from org.kernel.metering import BudgetExceeded, Meter
from org.kernel.state import LIFECYCLE_EDGES, DeptState, InvalidTransition
from org.kernel.store import TenantStore

__all__ = [
    "AUTONOMY_LEVELS",
    "BudgetExceeded",
    "CapabilityError",
    "Charter",
    "DecisionJournal",
    "Department",
    "DeptState",
    "Event",
    "EventBus",
    "InvalidTransition",
    "LIFECYCLE_EDGES",
    "Manifest",
    "ManifestError",
    "Meter",
    "TenantStore",
    "load_manifest",
    "parse_manifest",
]
