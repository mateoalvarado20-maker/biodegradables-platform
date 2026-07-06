"""Manifest de paquete VER-OS (`verops.yaml`) — parser y validación.

La EXISTENCIA del manifest con estas secciones es invariante del estándar; el
formato fino es convención hasta VER-OS v1.0. Validamos con Python puro (sin
dependencia de jsonschema) devolviendo TODOS los errores juntos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TRUST_TIERS = ("first_party", "partner", "community")
KINDS = ("department", "connector")
AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# ContratoOEvento@N, con '?' final para contratos consumidos opcionales
_VERSIONED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*@\d+\??$")


class ManifestError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("manifest inválido: " + "; ".join(errors))


@dataclass(frozen=True)
class Manifest:
    verops: str
    name: str
    version: str
    publisher: str
    kind: str
    trust_tier: str
    capabilities: dict[str, dict]
    provides: tuple[str, ...]
    consumes: tuple[str, ...]
    emits: tuple[str, ...]
    subscribes: tuple[str, ...]
    autonomy_max: str
    autonomy_default: str
    compliance: dict = field(default_factory=dict)

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities


def _norm_capabilities(raw, errors: list[str]) -> dict[str, dict]:
    """Acepta lista de strings o dicts de una clave: `- connectors: [...]` / `- notify`."""
    caps: dict[str, dict] = {}
    if raw is None:
        return caps
    if not isinstance(raw, list):
        errors.append("capabilities debe ser una lista")
        return caps
    for item in raw:
        if isinstance(item, str):
            caps[item] = {}
        elif isinstance(item, dict) and len(item) == 1:
            key, val = next(iter(item.items()))
            caps[str(key)] = val if isinstance(val, dict) else {"value": val}
        else:
            errors.append(f"capability inválida: {item!r}")
    return caps


def _norm_versioned(raw, label: str, errors: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        errors.append(f"{label} debe ser una lista")
        return ()
    out = []
    for item in raw:
        if not isinstance(item, str) or not _VERSIONED_RE.match(item):
            errors.append(f"{label}: {item!r} no cumple 'Nombre@N'")
        else:
            out.append(item)
    return tuple(out)


def parse_manifest(data: dict) -> Manifest:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ManifestError(["el manifest debe ser un mapping YAML"])

    verops = str(data.get("verops", ""))
    if verops != "0.1":
        errors.append(f"verops debe ser '0.1', llegó {verops!r}")

    pkg = data.get("package") or {}
    name = str(pkg.get("name", ""))
    version = str(pkg.get("version", ""))
    publisher = str(pkg.get("publisher", ""))
    kind = str(pkg.get("kind", ""))
    if not _NAME_RE.match(name):
        errors.append(f"package.name inválido: {name!r}")
    if not _SEMVER_RE.match(version):
        errors.append(f"package.version debe ser semver: {version!r}")
    if not publisher:
        errors.append("package.publisher requerido")
    if kind not in KINDS:
        errors.append(f"package.kind debe ser uno de {KINDS}, llegó {kind!r}")

    trust_tier = str(data.get("trust_tier", ""))
    if trust_tier not in TRUST_TIERS:
        errors.append(f"trust_tier debe ser uno de {TRUST_TIERS}, llegó {trust_tier!r}")

    capabilities = _norm_capabilities(data.get("capabilities"), errors)

    contracts = data.get("contracts") or {}
    provides = _norm_versioned(contracts.get("provides"), "contracts.provides", errors)
    consumes = _norm_versioned(contracts.get("consumes"), "contracts.consumes", errors)

    events = data.get("events") or {}
    emits = _norm_versioned(events.get("emits"), "events.emits", errors)
    subscribes = _norm_versioned(events.get("subscribes"), "events.subscribes", errors)

    autonomy = data.get("autonomy") or {}
    autonomy_max = str(autonomy.get("max_level", "L0"))
    autonomy_default = str(autonomy.get("default", "L0"))
    for label, lvl in (("autonomy.max_level", autonomy_max), ("autonomy.default", autonomy_default)):
        if lvl not in AUTONOMY_LEVELS:
            errors.append(f"{label} debe ser uno de {AUTONOMY_LEVELS}, llegó {lvl!r}")
    if (
        autonomy_max in AUTONOMY_LEVELS
        and autonomy_default in AUTONOMY_LEVELS
        and AUTONOMY_LEVELS.index(autonomy_default) > AUTONOMY_LEVELS.index(autonomy_max)
    ):
        errors.append(f"autonomy.default ({autonomy_default}) no puede superar max_level ({autonomy_max})")

    compliance = data.get("compliance") or {}
    if not isinstance(compliance, dict):
        errors.append("compliance debe ser un mapping")
        compliance = {}

    if errors:
        raise ManifestError(errors)

    return Manifest(
        verops=verops,
        name=name,
        version=version,
        publisher=publisher,
        kind=kind,
        trust_tier=trust_tier,
        capabilities=capabilities,
        provides=provides,
        consumes=consumes,
        emits=emits,
        subscribes=subscribes,
        autonomy_max=autonomy_max,
        autonomy_default=autonomy_default,
        compliance=compliance,
    )


def load_manifest(path: str | Path) -> Manifest:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_manifest(data)
