# Revisión técnica M3.0a–c — Infraestructura de publicación TikTok

**Fecha:** 2026-07-14 · **Autor:** CTO (Claude) · **Para:** board (Daniel)
**Estado:** implementado y testeado; NADA publica ni puede publicar.
**Propósito:** validar que lo construido es exactamente la arquitectura
aprobada por el board el 2026-07-14, ANTES de ejecutar M3.0d (registro de la
app en TikTok for Developers).

## 1. Qué se construyó (mapa contra la arquitectura aprobada)

| Aprobado | Implementado en | Notas |
|---|---|---|
| Puerto `Publisher` independiente de TikTok | `marketing/publisher.py` | Protocolo mínimo (`init_publish` + `fetch_status`); backends: `NullPublisher` (default), `FakeTikTokPublisher` (simulacro); el de TikTok real llega en M3.1 detrás del MISMO puerto |
| Kill-switch de 3 capas | yaml + kernel + código | (1) `publishing.enabled: false` en `marketing.yaml`; (2) capacidad `publish` declarada en el manifest pero NO otorgada por bootstrap — `CapabilityError` aunque prendan el flag; (3) `NullPublisher` por defecto lanza `PublishingDisabled` y REVIERTE la pieza a `scheduled` (no la consume) |
| Un único registro de app VER-IA | `tiktok_connector.py` | `TIKTOK_CLIENT_KEY/SECRET` a nivel app (App Service → Key Vault en SaaS); N cuentas de clientes sobre la misma app |
| Multi-tenant desde el inicio | `tiktok_connector.py` | TODO keyed por `tenant_id` (PartitionKey en la tabla `tiktoktokens`); el tenant viaja dentro del `state` OAuth (`<tenant>.<nonce>`); test de aislamiento incluido |
| Tokens aislados y cifrados por cliente | `tiktok_connector.py` | AES-GCM con `TIKTOK_TOKEN_KEY` (32 bytes); **fail-closed**: sin llave NO se guardan tokens (jamás texto claro — test lo verifica leyendo el archivo crudo); refresh ROTATIVO persistido en cada renovación |
| OAuth en la nube, PC sin secretos | `admin_api.py` | `connect-start` (admin) genera URL+PKCE; `/oauth/tiktok/callback` (público, state de un solo uso con TTL 15 min) canjea y guarda; la PC solo podrá pedir un access_token vigente por endpoint autenticado (M3.1) — nunca ve refresh_token ni llave |
| Ciclo de publicación crash-safe | `marketing/publisher.py` | `scheduled → publishing (publish_id persistido apenas responde el init) → published`; tras crash: CON publish_id se reconcilia consultando estado (jamás re-init); SIN publish_id es AMBIGUO → `publish_failed` + evento, **nunca se re-postea solo** (un post duplicado en la cuenta del cliente es peor que uno faltante) |
| Simulacro E2E (M3.0c) | `tests/test_publisher.py` | pieza aprobada → scheduled → "publicada" por el fake → PostRef + polling → `published` + journal/eventos → métricas al MetricsStore (el circuito que M3.2 llenará con datos reales) |

## 2. Evidencia

- **25 tests nuevos** (`test_tiktok_connector.py` + `test_publisher.py`), suite
  completa **567 en verde**, gates CI OK (ruff, higiene async, drift, build).
- Kill-switch probado capa por capa: flag apagado → el backend NI SE CONSULTA;
  capacidad no otorgada → `CapabilityError`; NullPublisher → pieza revertida a
  `scheduled` + evento `ops.publishing_disabled`.
- Anti-doble-post probado: reintento tras éxito → **un solo init**; crash
  post-init → reconcilia sin re-init; crash pre-init → `publish_failed`
  marcado AMBIGUO, sin re-posteo.
- OAuth probado de punta a punta con HTTP fake: el verifier canjeado
  corresponde criptográficamente al challenge de la URL (PKCE real), state de
  un solo uso, TTL 15 min, revocación (`invalid_grant` → reconectar), refresh
  vencido (>365 d) → reconectar, rotación persistida.
- El test de rotación destapó y corrigió un bug real (expiración evaluada con
  el reloj del sistema en vez del inyectado — habría renovado en cada llamada).

## 3. Decisiones tomadas al implementar (para ratificar)

1. **`FILE_UPLOAD` será la vía de subida en M3.1** (los MP4 viven en la PC;
   `PULL_FROM_URL` exige URL pública con dominio verificado). El init de ambas
   variantes ya está en el conector; la subida por chunks es de M3.1.
2. **El callback OAuth vive en el bot** (única superficie HTTPS pública) — la
   misma arquitectura del puente L0. Es público por necesidad; su seguridad es
   el state cifrado de un solo uso + PKCE, y no devuelve secretos.
3. **Piezas `publish_failed` no se auto-recuperan** — decisión deliberada
   (regla: ante ambigüedad, un humano; jamás doble post). El estado queda
   visible en `status` y en eventos.
4. **Scopes mínimos** `user.info.basic` + `video.publish`; `video.list` recién
   en M3.2 (pedir permisos que no se usan es deuda de auditoría).

## 4. Deuda registrada en esta fase

| Deuda | Se paga en |
|---|---|
| Variante exacta del PKCE (S256 base64url) validar contra la app real — la doc de TikTok ha oscilado | M3.1, primer OAuth real |
| `TIKTOK_TOKEN_KEY` como app setting → migrar a Key Vault junto con el resto de secrets | Fase SaaS (F4.5b/Key Vault) |
| Subida por chunks (FILE_UPLOAD) + límite ~15 posts/día/cuenta a respetar en el publisher | M3.1 |
| Métricas reales Display API (`video.list`) | M3.2 |

## 5. Qué necesito de Daniel para M3.0d (y el formato exacto)

1. **Ya mismo (para registrar la app):** el Redirect URI es
   `https://biodegradables-bot-app-cvgnasgec8eqatdg.centralus-01.azurewebsites.net/oauth/tiktok/callback`
   — copiarlo tal cual en la app de TikTok for Developers. Productos a
   agregar: **Login Kit** + **Content Posting API** (con Direct Post), scopes
   `user.info.basic` y `video.publish`.
2. **Al terminar el registro:** `client_key` y `client_secret` — **NUNCA por
   chat/correo**: cargarlos directo en Azure Portal → App Service
   `biodegradables-bot-app` → Environment variables, como
   `TIKTOK_CLIENT_KEY` y `TIKTOK_CLIENT_SECRET` (o avisar y los carga Mateo
   por `az` sin imprimirlos). `TIKTOK_TOKEN_KEY` la genera el CTO en el
   deploy (32 bytes aleatorios, tampoco viaja por chat).
3. **La cuenta de prueba** (oficial de Biodegradables): solo hace falta poder
   iniciar sesión en ella cuando corramos el `connect-start` — el vínculo se
   autoriza en la pantalla de TikTok, sin compartir contraseña con nadie.

## 6. Riesgo residual

Con flag apagado + capacidad no otorgada + NullPublisher + sin credenciales de
app + sin tokens, publicar exige CINCO cambios deliberados y auditables. El
riesgo real de esta fase es de trámite (tiempos de la auditoría de TikTok),
no técnico.
