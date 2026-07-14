# Runbook Operativo MVP — VER-IA Marketing

**Objetivo:** que cualquier persona del equipo opere VER-IA sin depender del
desarrollador. Obligatorio antes de conectar TikTok y antes del primer cliente
(decisión del board 2026-07-13).

**Qué es el sistema en una frase:** cada mañana a las 07:30 la PC genera un
plan de contenido, lo produce (guion→voz→video/carrusel), lo pasa por control
de calidad, y te manda un correo con las piezas que esperan TU aprobación.

---

## 1. Mapa operativo (qué corre dónde)

| Componente | Dónde | Cómo se llama |
|---|---|---|
| Corrida diaria 07:30 | PC de Mateo, Task Scheduler | Tarea `VERIA-Marketing-Daily` → `C:\Users\Mateo\tools\run_marketing_daily_prod.bat` |
| Código de producción | Worktree fijado a master | `C:\Users\Mateo\.worktrees\prod-master` |
| Estado (cola, journal, métricas) | SQLite | `C:\Users\Mateo\.ver-os\prod-biodegradables\` |
| Videos/carruseles producidos | Disco | `C:\Users\Mateo\.ver-os\prod-biodegradables\out\` |
| Logs diarios | Disco | `C:\Users\Mateo\logs\marketing-AAAAMMDD.log` |
| Notificaciones | Correo (Graph) | resumen diario + alertas a `daily.notify_to` de `tenants/biodegradables/marketing.yaml` |

**Todos los comandos de abajo se ejecutan en PowerShell desde:**
```powershell
cd C:\Users\Mateo\.worktrees\prod-master
```

## 2. Operación diaria normal (lo que ves cada día)

1. **07:30** — la corrida arranca sola. No hay que hacer nada.
2. **~08:00** — llega el correo "[VER-IA Marketing] Corrida diaria AAAA-MM-DD"
   con la tabla de piezas pendientes de aprobación.
3. **Revisar el contenido:** los archivos están en
   `C:\Users\Mateo\.ver-os\prod-biodegradables\out\<package_id>.mp4` (videos)
   o `...-slideNN.png` (carruseles).
4. **Decidir** — dos superficies equivalentes (misma auditoría):
   - **Teams (recomendada):** junto con el correo llega una tarjeta por pieza
     al chat del bot de datos, con botones **✅ Aprobar / ❌ Rechazar** (el
     motivo es obligatorio al rechazar — escribilo en el campo de la tarjeta).
     La decisión se aplica sola al inicio de la siguiente corrida; para
     aplicarla YA: `python -m marketing.daily_run aplicar`.
   - **CLI (siempre disponible):**
     ```powershell
     python -m marketing.daily_run pendientes                 # lista lo que espera
     python -m marketing.daily_run aprobar pkg-XXXX           # → lista para publicar
     python -m marketing.daily_run rechazar pkg-XXXX el motivo aquí
     ```
   Rechazar SIEMPRE lleva motivo (queda auditado). Si alguien ya decidió una
   pieza (doble tap, o CLI + Teams), la segunda decisión se rechaza con un
   mensaje claro — nunca se pisa.

## 3. Supervisión (sin abrir código)

```powershell
python -m marketing.daily_run status        # estado del día, pieza por pieza
python -m marketing.daily_run hor           # KPI Hands-Off Rate del mes
python -m marketing.daily_run preflight     # ¿el entorno está listo para correr?
python llm_usage.py status                  # gasto de IA del mes, por agente
```
El `status` dice: `completa` (todo bien), `en curso / interrumpida` (ver §5),
o `sin corrida` (hoy no ha corrido — ver §5.1).

## 4. Deploy / actualización (tras un merge a master que toque marketing/ u org/)

```powershell
git -C C:\Users\Mateo\.worktrees\prod-master fetch origin master
git -C C:\Users\Mateo\.worktrees\prod-master checkout origin/master --detach
python -m marketing.daily_run preflight     # valida TODO; instala npm ci solo si hace falta
```
El preflight **auto-instala** las dependencias de render si faltan (lección del
2026-07-13) — no hay pasos de memoria. Si preflight dice OK, quedó desplegado.

## 4b. Conectar la cuenta de TikTok (una sola vez, cuando existan credenciales)

Prerrequisito (M3.0d 👤): la app de VER-IA registrada en TikTok for
Developers y sus `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET` cargados como
app settings del bot (guía: `docs/tiktok-app-review.md`). Luego:

```powershell
python -m marketing.tiktok_connect biodegradables
```

Imprime y abre la URL de autorización; el dueño de la cuenta inicia sesión
en TikTok y acepta; el comando confirma "✅ Cuenta conectada". Los tokens
quedan cifrados en el bot y se renuevan solos. Verificación en cualquier
momento: el mismo comando dice "ya tiene su cuenta conectada".

**IMPORTANTE:** conectar la cuenta NO habilita publicar. La publicación
sigue bloqueada por 3 capas (flag del tenant + capacidad del kernel +
backend) hasta la activación formal de M3.1 con acta del board.

## 5. Incidentes comunes

### 5.1 No llegó el correo de la mañana
1. `python -m marketing.daily_run status`
2. - `sin corrida` → la tarea no disparó (¿PC apagada/suspendida a las 07:30?).
     Ejecutar a mano: `python -m marketing.daily_run run` y **declarar la
     intervención** (§6).
   - `en curso / interrumpida` → dejarla terminar o relanzar `run` (es
     idempotente: retoma donde quedó, jamás duplica).
   - `completa` pero sin correo → revisar el log del día por `notify falló`;
     el evento `ops.notify_failed` queda registrado. Suele ser red/credenciales
     Graph (env vars `MICROSOFT_APP_*`).

### 5.2 Correo de ALERTA "corrida incompleta"
El sistema ya reintentó (3 intentos por etapa). Ver el `ultimo_error` en
`status`. Relanzar `run` cuando la causa esté resuelta — retoma solo lo
pendiente. Declarar la intervención (§6).

### 5.3 El log del día contiene solo `^C` / la tarea marca resultado 0xC000013A
La consola del Task Scheduler fue abortada (típico: PC suspendida en el
disparo, o el disparo manual `/run` desde una sesión de Claude — ese SIEMPRE
muere, no es señal de nada). Si la corrida real no ocurrió: ejecutar a mano
(§5.1) y declarar intervención.

### 5.4 "remotion render falló ... transitorio" en el log
Normal en esta PC justo tras descargar clips (antivirus escaneando). El
reintento automático de 10 s casi siempre lo resuelve solo — si el día terminó
`completa`, no hay nada que hacer. Si falló 3 veces: relanzar `run` más tarde.

### 5.5 Pieza rechazada por "duración hablada X.Xs fuera del estándar"
Correcto y esperado a veces: la voz habla más lento que la estimación del
borrador. NO tocar la constante (decisión del board: recalibrar solo con
muestra suficiente — los casos se acumulan solos en el journal).

### 5.6 "preflight BLOQUEADO"
El correo/salida dice exactamente qué falta (env var, disco, npm, tenant).
Resolver lo listado y volver a correr. El preflight nunca deja gastar con el
entorno roto.

### 5.7 Presupuesto excedido (BudgetExceeded en el log)
El departamento alcanzó su tope mensual (`daily.budget_usd_month` en
`marketing.yaml`). Es un freno DURO deliberado. Decisión de negocio: subir el
presupuesto (PR al yaml) o esperar al mes siguiente.

### 5.8 `aplicar` reporta "pkg-XXXX no está en la cola"
Llegó una decisión de Teams para una pieza que la cola local no conoce
(típico: el state de `~/.ver-os/` se reconstruyó, o la pieza es de otra
máquina). No se pierde nada ni se confirma en falso — el error se repite en
cada corrida hasta resolverlo. Si la decisión ya no aplica, cerrarla a mano:
```powershell
# (requiere VERIA_BOT_BASE_URL y ADMIN_API_TOKEN en el entorno)
Invoke-WebRequest -Uri "$env:VERIA_BOT_BASE_URL/admin/marketing/l0-applied" -Method POST `
  -Headers @{"X-Admin-Token"=$env:ADMIN_API_TOKEN; "Content-Type"="application/json"} `
  -Body '{"package_ids": ["pkg-XXXX"]}'
```

## 6. Declarar una intervención manual (obligatorio)

Cada vez que un humano rescate algo (relanzar, arreglar, instalar):
```powershell
python -m marketing.daily_run intervencion "qué hiciste y por qué"
```
Esto baja el KPI HOR — así debe ser. **Un KPI honesto vale más que uno
maquillado** (board, 2026-07-13). Las aprobaciones L0 NO son intervenciones.

## 7. Recuperación ante desastres (PC nueva / estado perdido)

1. Clonar el repo y crear el worktree de producción (§4).
2. Restaurar/setear env vars de usuario: `ANTHROPIC_API_KEY`,
   `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `PEXELS_API_KEY`,
   `MICROSOFT_APP_ID/PASSWORD/TENANT_ID` (valores: gestor de secretos del
   equipo / Azure Portal).
3. Node portable: descargar LTS zip a `C:\Users\Mateo\tools\node-vXX-win-x64`
   (el preflight lo encuentra solo).
4. Recrear la tarea:
   ```powershell
   schtasks /create /tn "VERIA-Marketing-Daily" /tr "C:\Users\Mateo\tools\run_marketing_daily_prod.bat" /sc daily /st 07:30 /ru "Mateo" /rl LIMITED /f
   ```
5. `python -m marketing.daily_run preflight` hasta OK.
6. El estado histórico (cola/journal/métricas) vive en
   `C:\Users\Mateo\.ver-os\prod-biodegradables\` — incluirlo en el backup de la
   PC. Perderlo NO rompe el sistema (arranca de cero) pero pierde historial y KPIs.

## 8. Límites conocidos de esta versión

- La corrida vive en la PC de Mateo (SPOF conocido; migra a Azure en fase SaaS).
  Si la PC está apagada a las 07:30, ese día requiere corrida manual (§5.1).
- La tarjeta de Teams necesita en la PC las env vars `VERIA_BOT_BASE_URL`
  (URL del App Service del bot) y `ADMIN_API_TOKEN`. **Configuradas y
  validadas en producción el 2026-07-14** (E2E completo: tarjeta → decisión
  → sincronización → transición en la cola). Sin ellas el sistema NO se
  rompe: lo avisa en el log y la aprobación queda solo por CLI. Los
  aprobadores se editan en `tenants/biodegradables/marketing.yaml`
  (`daily.l0_approvers` — hoy: solo Mateo, decisión de Daniel 2026-07-14) —
  deben haber chateado al menos una vez con alguno de los bots de Teams
  para que exista la conversación proactiva.
- Una decisión de Teams se aplica al inicio de la SIGUIENTE corrida (o con
  `aplicar`) — no es instantánea; nada urge porque todavía no se publica.
- Nada se publica todavía en TikTok: "aprobada" = lista en cola `scheduled`.
- El disparo manual de la tarea desde sesiones de Claude no funciona (§5.3);
  desde una PowerShell humana normal, sí.

## 9. Escalación

1º este runbook → 2º `status` + log del día → 3º Mateo (operación local) →
4º CTO (Claude, con el log y el `status` pegados en el mensaje).
