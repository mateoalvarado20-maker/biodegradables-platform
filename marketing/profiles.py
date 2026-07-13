"""Carga de PlatformProfiles declarativos (marketing/profiles/<red>.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml

from marketing.models import PlatformProfile

_DIR = Path(__file__).parent / "profiles"


def available_platforms() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.yaml"))


def load_profile(platform: str) -> PlatformProfile:
    path = _DIR / f"{platform}.yaml"
    if not path.exists():
        raise KeyError(f"no hay perfil de plataforma para {platform!r} en {_DIR}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return PlatformProfile(**data)
