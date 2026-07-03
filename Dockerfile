# syntax=docker/dockerfile:1
# ============================================================================
# Imagen ÚNICA de la plataforma VER-IA (F4.5, 2026-07-03).
#
# Principio: UN artefacto construido desde el árbol COMPLETO del repo.
# Nada de listas manuales de archivos — la clase de bug del 2026-07-03
# (llm_usage.py fuera del zip de deploy) es imposible por construcción aquí.
#
# Cada cliente = una instancia de esta misma imagen con SU configuración
# inyectada por env vars (decisión congelada: aislamiento por instancia,
# código compartido):
#   TENANT_SLUG=<slug>            → tenants/<slug>/config.yaml
#   STATE_DIR=<ruta persistente>  → montar volumen (App Service: /home/...)
#   + secrets del tenant (MICROSOFT_APP_*, CONTIFICO_API_TOKEN, etc.)
#
# Validación: el CI construye la imagen y hace un smoke de imports en cada PR.
# ============================================================================
FROM python:3.12-slim

WORKDIR /app

# Dependencias primero — capa cacheable entre builds.
COPY requirements_bot.txt .
RUN pip install --no-cache-dir -r requirements_bot.txt

# El árbol completo (exclusiones en .dockerignore: git, tests, logs, azfunc).
COPY . .

ENV PYTHONUNBUFFERED=1 \
    TENANT_CONFIG_SOURCE=yaml

EXPOSE 8000

# 1 worker es DELIBERADO: el scheduler (lease de instancia única) y safe_json
# (locks por proceso) asumen un solo proceso escritor. Escalar = más
# instancias-tenant, no más workers.
CMD ["gunicorn", "teams_bot:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "120", \
     "-w", "1"]
