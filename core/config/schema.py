"""Esquema declarativo de la configuración de un tenant (Pydantic v2).

Este módulo NO contiene ningún valor de ningún cliente: solo la FORMA que debe
tener `tenants/<slug>/config.yaml`. Los valores de Biodegradables viven en
`tenants/biodegradables/config.yaml`. Validar un paquete de tenant = cargar su
YAML contra estos modelos (ver `core/config/loader.py` y `ops/validate_tenant.py`).

Parte de las Acciones 1+3 del plan multiempresa (F0/F1). Andamiaje aditivo: NO se
importa todavía desde los bots/agentes en producción.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    """'16:30' -> (16, 30). None o '' -> None."""
    if not value:
        return None
    hh, mm = str(value).split(":")
    return (int(hh), int(mm))


class _Strict(BaseModel):
    # extra='forbid' => un typo en el YAML falla con un mensaje claro en vez de
    # ignorarse en silencio. Es clave para "onboarding sin tocar código": el
    # validador dice exactamente qué campo sobra o falta.
    model_config = ConfigDict(extra="forbid")


class Recipients(_Strict):
    commercial_report: list[str] = Field(default_factory=list)
    commercial_report_cc: list[str] = Field(default_factory=list)
    logistics_report: list[str] = Field(default_factory=list)
    calendar_sync_users: list[str] = Field(default_factory=list)


class Thresholds(_Strict):
    cumpl_verde: float = 1.00
    cumpl_amarillo: float = 0.85
    ayer_verde: float = 1.00
    ayer_amarillo: float = 0.80
    mora_verde: float = 0.05
    mora_amarillo: float = 0.10


class Commercial(_Strict):
    meta_factor: float = 1.20
    py_override: dict[str, float] = Field(default_factory=dict)  # "YYYY-MM" -> monto
    thresholds: Thresholds = Field(default_factory=Thresholds)


class CheckinGroup(_Strict):
    users: list[str] = Field(default_factory=list)
    weekday_time: str | None = None   # "HH:MM" en hora local del tenant
    saturday_time: str | None = None  # "HH:MM" (opcional)


class Checkin(_Strict):
    oficina: CheckinGroup = Field(default_factory=CheckinGroup)
    sucursales: CheckinGroup = Field(default_factory=CheckinGroup)


class Branding(_Strict):
    brand_color: str | None = None
    logo_url: str | None = None


class Company(_Strict):
    """Identidad de negocio que aparece en prompts y reportes."""

    sector: str = ""               # "distribución de productos biodegradables"
    sucursales_desc: str = ""      # "Quito (UIO) y Guayaquil (GYE)"
    sucursal_names: dict[str, str] = Field(default_factory=dict)  # {"UIO": "Quito"}


class Person(_Strict):
    """Una persona del equipo del tenant (directorio de identidad/roles)."""

    email: str
    name: str
    role: str = "colaborador"      # gerente_general|gerente_comercial|analista|asistente|chofer
    sucursal: str | None = None    # "UIO"|"GYE"|None
    asistente_num: int | None = None
    supervisor: bool = False
    rotativo_sabado: bool = False


class TenantConfig(_Strict):
    """Configuración completa de UNA empresa. La forma de `config.yaml`."""

    slug: str
    display_name: str
    locale: str = "es-EC"
    timezone: str = "America/Guayaquil"
    recipients: Recipients = Field(default_factory=Recipients)
    branding: Branding = Field(default_factory=Branding)
    company: Company = Field(default_factory=Company)
    people: list[Person] = Field(default_factory=list)
    commercial: Commercial = Field(default_factory=Commercial)
    checkin: Checkin = Field(default_factory=Checkin)
    holidays: dict[int, list[date]] = Field(default_factory=dict)

    @field_validator("holidays", mode="before")
    @classmethod
    def _parse_holidays(cls, v):
        """Acepta años como int o str y fechas como ISO-string o date nativo."""
        if not v:
            return {}
        out: dict[int, list[date]] = {}
        for year, days in v.items():
            parsed: list[date] = []
            for d in days or []:
                parsed.append(d if isinstance(d, date) else date.fromisoformat(str(d)))
            out[int(year)] = parsed
        return out

    def py_override_map(self) -> dict[tuple[int, int], float]:
        """{'2026-05': 38000.0} -> {(2026, 5): 38000.0} (= core_config.PY_OVERRIDE)."""
        out: dict[tuple[int, int], float] = {}
        for key, val in self.commercial.py_override.items():
            y, m = str(key).split("-")
            out[(int(y), int(m))] = float(val)
        return out
