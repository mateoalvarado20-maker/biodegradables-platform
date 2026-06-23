"""Motor de prompts por capas (Acción 6).

Compone el system prompt de un agente a partir de:
  - core/prompts/<role>.base.md                base compartida, igual para todos, SIN vendors
  - tenants/<slug>/prompts/company_context.md  catálogo/tono del cliente (opcional)
  - tenants/<slug>/prompts/<role>.md           overrides del cliente (opcional)
y sustituye variables {{display_name}}, {{locale}}, {{timezone}}, ...

Los prompts son ARCHIVOS, no strings en .py → se editan sin desplegar código. Cada
base puede llevar frontmatter con `version:` para auditar qué prompt produjo una
respuesta. Andamiaje aditivo: los bots/agentes actuales NO lo usan todavía.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CORE_PROMPTS = Path(__file__).resolve().parent              # core/prompts
_TENANTS_DIR = Path(__file__).resolve().parents[2] / "tenants"
_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Separa un frontmatter simple (--- ... ---) del cuerpo. Devuelve (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2].lstrip("\n")


def substitute(text: str, variables: dict[str, Any]) -> str:
    """Reemplaza {{var}}. Falla claro si el texto usa una variable sin valor."""

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise KeyError(f"Variable de prompt sin valor: {{{{{key}}}}}")
        return str(variables[key])

    return _VAR.sub(repl, text)


def render(
    base: str,
    context: str = "",
    overlay: str = "",
    variables: dict[str, Any] | None = None,
) -> str:
    """Compone base + contexto + overlay (sin frontmatter) y sustituye variables."""
    _, base_body = parse_frontmatter(base)
    sections = [base_body]
    if context.strip():
        sections.append(context.strip())
    if overlay.strip():
        _, overlay_body = parse_frontmatter(overlay)
        sections.append(overlay_body.strip())
    composed = "\n\n".join(s.strip() for s in sections if s and s.strip())
    return substitute(composed, variables or {})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_system_prompt(
    role: str,
    ctx: Any,
    *,
    extra_vars: dict[str, Any] | None = None,
    core_prompts: Path | None = None,
    tenants_dir: Path | None = None,
) -> str:
    """Arma el system prompt de <role> para el tenant de `ctx` (un TenantContext)."""
    cp = core_prompts or _CORE_PROMPTS
    td = tenants_dir or _TENANTS_DIR
    base = _read(cp / f"{role}.base.md")
    if not base.strip():
        raise FileNotFoundError(f"No existe el prompt base del rol '{role}' en {cp}")
    tprompts = td / ctx.slug / "prompts"
    context = _read(tprompts / "company_context.md")
    overlay = _read(tprompts / f"{role}.md")
    variables: dict[str, Any] = {
        "display_name": ctx.display_name,
        "locale": ctx.locale,
        "timezone": ctx.timezone,
    }
    if extra_vars:
        variables.update(extra_vars)
    return render(base, context, overlay, variables)


def prompt_version(role: str, core_prompts: Path | None = None) -> str | None:
    """`version` del frontmatter del prompt base (para loguear junto al envío)."""
    cp = core_prompts or _CORE_PROMPTS
    meta, _ = parse_frontmatter(_read(cp / f"{role}.base.md"))
    return meta.get("version")
