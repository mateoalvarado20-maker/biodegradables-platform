# Onboarding — nuevos desarrolladores

Objetivo: que una persona nueva contribuya con seguridad en su primera
semana SIN poder romper producción. El sistema envía reportes diarios al
gerente general — un bug aquí es visible para toda la empresa a las 8 AM.

## Día 1 — contexto (solo lectura)

1. Leé en este orden:
   - `CLAUDE.md` — qué hace el sistema y dónde corre cada cosa.
   - `docs/arquitectura.md` — runtimes, capas, dueños de estado, garantías.
   - `CONTRIBUTING.md` — las 10 reglas de oro + checklist de PR.
   - `AUDITORIA_TECNICA_2026-06-12.md` §1-3 — los errores que NO vamos a repetir.
2. Pedí acceso: repo (rol write, sin admin), y NADA más todavía. No
   necesitás credenciales de Azure ni API keys para empezar — los tests
   corren 100% con mocks.

## Día 2 — entorno verde (rito de iniciación)

```powershell
git clone <repo> ; cd <repo>
pip install -r requirements-dev.txt -r requirements_bot.txt
python -m pytest tests/ -q     # deben pasar TODOS
python -m ruff check .         # limpio
python tools/sync_azfunc.py --check
```
Si algo no pasa en tu máquina, ese es tu primer issue.

## Semana 1 — primeras contribuciones (zonas de bajo riesgo)

Buenas primeras tareas:
- Agregar un test que falte (mirá la tabla de garantías en arquitectura.md
  y buscá huecos).
- Mejorar un mensaje de log o de error.
- Un fix en heurísticas de parsing (`PROVINCIA_KEYWORDS`) con su test.

Zonas VEDADAS el primer mes (requieren pairing con Mateo):
- `safe_json.py`, `send_ledger.py` (infraestructura de integridad)
- Resolución de identidad en `teams_bot.py` (`_user_email` y alrededores)
- Cualquier cosa que cambie horarios o destinatarios de reportes

## Reglas operativas que te van a salvar

- **Nunca edites `azfunc/`** — se genera (`tools/sync_azfunc.py`). El CI
  rechaza el drift.
- **Nunca corras modos `morning`/`send` de los reportes** — mandan correos
  reales a gerencia. Usá `dry-morning` / `test-morning` / `--dry-run`.
- Los endpoints `/admin/trigger-*` del bot disparan envíos y cards REALES
  a los colaboradores. No los toques sin coordinar.
- `date.today()` está prohibido (TZ). `except: pass` está prohibido.
  El reviewer lo va a rechazar; mejor no escribirlo.
- ¿Datos de producción? El state vive en Azure (`/home/.claude-agent` del
  App Service y Azure Tables). Tu `~/.claude-agent` local es OTRO universo:
  lo que escribas ahí no afecta (ni refleja) producción.

## Mapa mental en 5 líneas

1. Dos bots de Teams en un App Service; el scheduler del bot manda todos
   los reportes de equipo + el comercial de las 8:00.
2. Un Function App con la logística de las 8:00 y el reply agent cada 15 min.
3. La PC de Mateo solo conserva el notificador de Apollo y CLIs manuales.
4. Todo el estado pasa por `safe_json`/Azure Tables; todo envío pasa por
   `send_ledger`; toda identidad pasa por el registro AAD.
5. La raíz del repo es la única fuente; `azfunc/` y `bot_deploy.zip` se
   generan con `tools/`.

## A quién preguntar

- Dueño técnico: Mateo Alvarado (`malvarado@biodegradablesecuador.com`).
- Negocio/prioridades: Daniel Sánchez (gerente), Gabriela Sánchez (comercial).
- Operación diaria: `docs/runbook-operativo.md`.
