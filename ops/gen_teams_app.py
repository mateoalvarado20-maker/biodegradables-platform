"""gen_teams_app — genera los paquetes de app de Teams de un tenant (F5.1).

Parte del aprovisionamiento automatizado: en vez de copiar/editar manifests
JSON a mano por cliente (como se hizo con Biodegradables y Andex), este
tool los genera desde `tenants/<slug>/config.yaml` — nombre, marca, color
e iconos salen de la config del tenant; los App IDs de los 2 bots se pasan
por argumento (salen del aprovisionamiento en el M365 del cliente).

Uso:
    python ops/gen_teams_app.py <slug> \
        --data-app-id <GUID> --activities-app-id <GUID> \
        [--out tenants/<slug>/teams/dist] [--version 1.0.0]

Produce por bot: manifest.json + color.png (192x192, color de marca) +
outline.png (32x32) → <slug>_teams_data.zip y <slug>_teams_activities.zip,
listos para "Upload custom app" en Teams o el admin center del cliente.

Sin App IDs (aún no aprovisionado) deja placeholders REEMPLAZAR_* y avisa.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config.loader import load_tenant_config  # noqa: E402

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

BOTS = {
    "data": {
        "sufijo_corto": "Data Bot",
        "descripcion_corta": "Asistente de datos comerciales — ventas, cartera y marketing.",
        "descripcion_larga": (
            "Asistente conversacional para gerencia: consulta ventas, cartera, "
            "cobranzas y KPIs de marketing en lenguaje natural, con datos en "
            "vivo del ERP y CRM de la empresa."
        ),
        "commands": [
            {"title": "help", "description": "Ver comandos disponibles"},
            {"title": "refresh", "description": "Refrescar contexto de datos"},
        ],
    },
    "activities": {
        "sufijo_corto": "Actividades",
        "descripcion_corta": "Check-in diario de actividades y tareas del equipo.",
        "descripcion_larga": (
            "Asistente de actividades del equipo: check-in diario con Adaptive "
            "Cards, tareas y recordatorios, cierre de caja y resúmenes "
            "automáticos a supervisión."
        ),
        "commands": [
            {"title": "help", "description": "Ver comandos disponibles"},
            {"title": "tareas", "description": "Ver mis tareas y su estado"},
        ],
    },
}


def _hex_a_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "#2E7D32").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _oscurecer(rgb: tuple[int, int, int], factor: float = 0.7) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in rgb)  # type: ignore[return-value]


def _icono_color(letra: str, rgb: tuple[int, int, int]) -> bytes:
    """color.png 192x192: fondo redondeado del color de marca + inicial."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 184, 184], radius=36, fill=rgb + (255,))
    d.rounded_rectangle([8, 8, 184, 184], radius=36,
                        outline=_oscurecer(rgb) + (255,), width=6)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 110)
    except Exception:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 110
            )
        except Exception:
            font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), letra, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((192 - w) / 2 - bbox[0], (192 - h) / 2 - bbox[1]), letra,
           fill=(255, 255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _icono_outline(letra: str) -> bytes:
    """outline.png 32x32: silueta blanca sobre transparente (requisito Teams)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, 31, 31], radius=7, outline=(255, 255, 255, 255),
                        width=2)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 18)
    except Exception:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18
            )
        except Exception:
            font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), letra, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((32 - w) / 2 - bbox[0], (32 - h) / 2 - bbox[1]), letra,
           fill=(255, 255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _manifest(cfg, kind: str, app_id: str, version: str) -> dict:
    meta = BOTS[kind]
    display = cfg.display_name
    website = cfg.company.website or "https://example.com"
    website = website.rstrip("/")
    short = f"{display} {meta['sufijo_corto']}"
    if len(short) > 30:  # límite de Teams para name.short
        short = short[:30].rstrip()
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": version,
        "id": app_id,
        "developer": {
            "name": display,
            "websiteUrl": website,
            "privacyUrl": f"{website}/privacy",
            "termsOfUseUrl": f"{website}/terms",
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "name": {"short": short, "full": f"{display} — {meta['sufijo_corto']}"},
        "description": {
            "short": meta["descripcion_corta"],
            "full": meta["descripcion_larga"],
        },
        "accentColor": cfg.branding.brand_color or "#2E7D32",
        "bots": [
            {
                "botId": app_id,
                "scopes": ["personal"],
                "supportsFiles": False,
                "isNotificationOnly": False,
                "commandLists": [
                    {"scopes": ["personal"], "commands": meta["commands"]}
                ],
            }
        ],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [],
    }


def generar(slug: str, data_app_id: str | None, activities_app_id: str | None,
            out_dir: Path, version: str = "1.0.0") -> list[Path]:
    cfg = load_tenant_config(slug)
    rgb = _hex_a_rgb(cfg.branding.brand_color or "#2E7D32")
    letra = (cfg.display_name or slug)[0].upper()
    color_png = _icono_color(letra, rgb)
    outline_png = _icono_outline(letra)

    app_ids = {"data": data_app_id, "activities": activities_app_id}
    out_dir.mkdir(parents=True, exist_ok=True)
    generados: list[Path] = []
    for kind, app_id in app_ids.items():
        if app_id and not GUID_RE.match(app_id):
            raise SystemExit(f"App ID inválido para {kind}: {app_id!r} (esperado GUID)")
        placeholder = f"REEMPLAZAR_CON_{kind.upper()}_BOT_APP_ID"
        manifest = _manifest(cfg, kind, app_id or placeholder, version)
        if not app_id:
            print(f"[WARN] {kind}: sin App ID — manifest con placeholder "
                  f"{placeholder} (regenerar tras aprovisionar el bot)")
        zpath = out_dir / f"{slug}_teams_{kind}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("color.png", color_png)
            zf.writestr("outline.png", outline_png)
        generados.append(zpath)
        print(f"OK: {zpath} ({cfg.display_name} — {kind}, "
              f"accent {manifest['accentColor']})")
    return generados


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("slug", help="tenant (carpeta en tenants/)")
    p.add_argument("--data-app-id", default=None)
    p.add_argument("--activities-app-id", default=None)
    p.add_argument("--out", default=None,
                   help="directorio de salida (default tenants/<slug>/teams/dist)")
    p.add_argument("--version", default="1.0.0")
    args = p.parse_args()
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent
        / "tenants" / args.slug / "teams" / "dist"
    )
    generar(args.slug, args.data_app_id, args.activities_app_id, out, args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
