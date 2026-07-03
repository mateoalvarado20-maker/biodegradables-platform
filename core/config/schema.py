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
    dashboard_url: str = ""        # link en el footer del reporte comercial


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


# Catálogo de módulos activables (F2.3). Debe espejar core_config.MODULES —
# la igualdad la fija tests/test_modules_config.py. Vive duplicado porque
# core/ no puede importar core_config (pureza del núcleo) ni al revés (azfunc
# no incluye core/).
KNOWN_MODULES = frozenset({
    "commercial", "logistics", "cobranzas", "activities",
    "chofer", "news_brief", "calendar", "marketing", "prospecting",
})


class JobSchedule(_Strict):
    """Horario de UN job del scheduler (F2.2, 2026-07-02).

    Las claves válidas del bloque `schedules:` son las de
    `core_config.JOB_SCHEDULES` (morning_sales, logistics_morning,
    auto_assign_cobranzas, task_confirmations, calendar_sync,
    daily_news_brief, apertura_caja_matinal, consolidated_daily,
    saturday_recap, monthly_sales_recap, monthly_activities_recap).
    Una clave desconocida falla al cargar (fail-closed, no typos silenciosos).
    """

    time: str                        # "HH:MM" en hora local del tenant
    days: str | None = None          # "mon-fri" | "mon-sat" | "mon" | "daily" | "mon,wed"
    day_of_month: int | None = Field(None, ge=1, le=28)  # jobs mensuales

    @field_validator("time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        hh, mm = parse_hhmm(v) or (None, None)
        if hh is None or not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"time inválido: {v!r} (esperado 'HH:MM')")
        return v


class Company(_Strict):
    """Identidad de negocio que aparece en prompts y reportes."""

    sector: str = ""               # "distribución de productos biodegradables"
    sucursales_desc: str = ""      # "Quito (UIO) y Guayaquil (GYE)"
    sucursal_names: dict[str, str] = Field(default_factory=dict)  # {"UIO": "Quito"}
    domain: str = ""               # "empresa.com" (filtra correos internos)
    website: str = ""              # URL pública (firma de correos outbound)
    outbound_signer: str = ""      # email del firmante de correos a prospectos


class ERP(_Strict):
    """Parámetros del ERP del tenant (F2.4)."""

    document_prefixes: dict[str, str] = Field(default_factory=dict)  # {"GYE": "001-001"}


class Logistics(_Strict):
    """Parámetros del módulo de logística (F2.4)."""

    # Lista de [keyword, provincia, ciudad] para parsear direcciones de
    # despacho. VACÍA = dataset default de Ecuador. Un tenant de otro país
    # la reemplaza completa.
    provincia_keywords: list[tuple[str, str, str]] = Field(default_factory=list)


class Caja(_Strict):
    """Fondo fijo de caja por sucursal (F2.4)."""

    fondo_default: float | None = None
    fondo_por_sucursal: dict[str, float] = Field(default_factory=dict)


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
    schedules: dict[str, JobSchedule] = Field(default_factory=dict)
    modules: dict[str, bool] = Field(default_factory=dict)
    erp: ERP = Field(default_factory=ERP)
    logistics: Logistics = Field(default_factory=Logistics)
    caja: Caja = Field(default_factory=Caja)
    holidays: dict[int, list[date]] = Field(default_factory=dict)

    @field_validator("modules")
    @classmethod
    def _known_modules(cls, v: dict[str, bool]) -> dict[str, bool]:
        """Un typo en el nombre de un módulo falla en validate_tenant, no en
        producción al arrancar."""
        unknown = set(v) - KNOWN_MODULES
        if unknown:
            raise ValueError(
                f"módulos desconocidos: {sorted(unknown)}; "
                f"válidos: {sorted(KNOWN_MODULES)}"
            )
        return v

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
