"""Registro de contratos VER-OS (componente #7, invariante #3).

Cada contrato es un archivo JSON `Nombre@N.json` en este directorio con la
especificación de sus campos. Principio de mínimo necesario: los contratos
llevan IDs y agregados, nunca datos crudos del dominio ajeno.

Validación con Python puro (tipos + required + enum) — suficiente para v0.1;
si en v1.0 hace falta JSON Schema completo, este módulo es el único que cambia.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DIR = Path(__file__).parent
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*@\d+$")

_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class ContractError(ValueError):
    def __init__(self, contract: str, errors: list[str]):
        self.contract = contract
        self.errors = errors
        super().__init__(f"payload inválido para {contract}: " + "; ".join(errors))


def available_contracts() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*@*.json"))


def load_contract(name: str) -> dict:
    if not _NAME_RE.match(name):
        raise ValueError(f"nombre de contrato inválido: {name!r} (formato 'Nombre@N')")
    path = _DIR / f"{name}.json"
    if not path.exists():
        raise KeyError(f"contrato no registrado: {name}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_payload(name: str, payload: dict) -> None:
    """Lanza ContractError con TODOS los problemas; None si el payload es válido."""
    spec = load_contract(name)
    fields: dict = spec.get("fields", {})
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise ContractError(name, ["el payload debe ser un objeto"])
    for fname, fspec in fields.items():
        required = bool(fspec.get("required", False))
        if fname not in payload or payload[fname] is None:
            if required:
                errors.append(f"falta campo requerido {fname!r}")
            continue
        value = payload[fname]
        ftype = fspec.get("type", "string")
        pytype = _TYPES.get(ftype)
        if pytype and not isinstance(value, pytype):
            errors.append(f"{fname!r} debe ser {ftype}, llegó {type(value).__name__}")
            continue
        if isinstance(value, bool) and ftype in ("number", "integer"):
            errors.append(f"{fname!r} debe ser {ftype}, llegó bool")
            continue
        enum = fspec.get("enum")
        if enum and value not in enum:
            errors.append(f"{fname!r} debe ser uno de {enum}, llegó {value!r}")
    extra = set(payload) - set(fields)
    if extra and not spec.get("allow_extra", False):
        errors.append(f"campos no declarados en el contrato: {sorted(extra)}")
    if errors:
        raise ContractError(name, errors)
