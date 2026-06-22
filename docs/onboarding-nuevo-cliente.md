# Onboarding de un cliente nuevo — cómo pedírselo a Claude

Esta guía responde: **"si quiero implementar el bot/agente para otra empresa, qué
hago y cómo te lo pido para que me pidas los datos y se adapte a esa empresa".**

La idea de fondo (ver `PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md`): un cliente nuevo
se da de alta con **configuración + conexión de integraciones**, NUNCA tocando la
lógica. Hoy ya existe el andamiaje (`core/`, `tenants/`, `ops/validate_tenant.py`);
las fases que faltan (conectores, secretos, despliegue por instancia) se construyen
encima sin cambiar lo de los demás clientes.

---

## 1. Cómo pedírmelo (el mensaje que me mandás)

No necesitás saber qué datos hacen falta: **yo te los voy a ir pidiendo**. Basta con
arrancar así (entre más completes, menos te pregunto):

> **"Quiero dar de alta un cliente nuevo: `<nombre de la empresa>`.
> Es una `<rubro>` en `<país/ciudad>`. Usa `<ERP>` y `<CRM>` (o "no sé / ninguno").
> Quiero que tenga estos módulos: `<lista, o "decime el menú">`.
> Pedime todo lo que falte."**

Ejemplo real:

> "Quiero dar de alta a **Acme Distribución**, una distribuidora en Lima, Perú.
> Usa **Contifico** y **no tiene CRM**. Quiero reporte comercial diario, bot de
> actividades y cobranzas. Pedime lo que falte."

Con eso yo: (a) creo `tenants/acme/` desde la plantilla, (b) te hago las preguntas
del checklist de abajo **una por una**, (c) lleno el `config.yaml`, (d) corro el
validador hasta que pase, y (e) te dejo la lista de pasos manuales (registrar el bot
en el M365 del cliente, cargar secretos) que requieren tu acción.

---

## 2. Qué datos te voy a pedir (checklist de intake)

Esto es lo que necesito para configurar un cliente. **No lo llenes por adelantado si
no querés**; lo vamos armando en la conversación.

### A. Identidad
- Nombre comercial y `slug` corto (ej. `acme`).
- País, ciudad(es) de operación, zona horaria, idioma.

### B. Módulos que contrata (el menú — encendés lo que quiera)
| Módulo | Qué hace |
|---|---|
| `commercial_report` | Reporte comercial diario (ventas vs meta) |
| `logistics_report` | Reporte de logística / envíos |
| `monthly_recap` | Recap mensual + proyección |
| `news_brief` | Brief diario de noticias del sector |
| `data_bot` | Bot de Teams para consultar KPIs en lenguaje natural |
| `activities_bot` | Check-in diario + tracker de actividades del equipo |
| `team_tracker` | Resúmenes consolidados del equipo |
| `collections` | Cobranzas auto-asignadas por cartera vencida |
| `prospecting_reply` | Agente que redacta respuestas a prospectos |
| `payment_reminders` | Recordatorios de pagos en calendario |
| `cms_wordpress` | Auditoría/gestión de su web |

### C. Integraciones (qué sistema usa para cada cosa)
- **ERP** (de dónde salen las ventas): Contifico u otro. → necesito el token (va a Key Vault, no al repo).
- **CRM** (leads/deals): HubSpot, otro, o ninguno.
- **Correo**: Microsoft Graph (su M365) o SMTP.
- **Prospección**: Apollo u otro (si usa `prospecting_reply`).
- **Calendario / CMS**: según módulos.

### D. Destinatarios y personas
- Quién recibe cada reporte (correos).
- Equipo que usa el bot de actividades + sus roles (supervisor, sucursal, etc.).

### E. Parámetros de negocio
- Meta de crecimiento (`meta_factor`), umbrales del semáforo.
- Feriados de SU país.
- Horarios de check-in.

### F. Marca y tono
- Color corporativo, logo.
- Catálogo/diferenciadores y reglas de tono para los agentes (su `company_context`).

### G. Microsoft 365 del cliente (para el bot de Teams)
- Su tenant de Azure AD (cada empresa registra su propio Azure Bot — decisión
  congelada §13bis del documento de arquitectura).
- Admin que dé el *consent* de permisos Graph (te paso el link para enviárselo).

---

## 3. Qué pasa por detrás (resumen técnico)

1. `tenants/<slug>/config.yaml` ← todos los valores de negocio (B–F). Ningún dato del
   cliente vive en el código.
2. Secretos (tokens de C) → Key Vault, referenciados por nombre. **Nunca en el repo.**
3. `python ops/validate_tenant.py <slug>` valida que no falte nada (mensaje claro si sí).
4. (Fases siguientes) conectores por interfaz enchufan su ERP/CRM; el bot se
   despliega como una instancia aislada con `TENANT_SLUG=<slug>`.

**Regla de oro:** si dar de alta a un cliente me obligara a tocar `core/` o
`modules/`, eso es un bug de diseño que corregimos — no una versión nueva del sistema.

---

## 4. Estado hoy (qué ya está listo y qué falta)

- ✅ **Listo (Acciones 1-4):** esquema de config por tenant, cargador con validación,
  `tenants/_template/`, `tenants/biodegradables/` (espejo verificado de `core_config`),
  validador CLI, check de pureza del núcleo.
- ⏳ **Falta para onboarding completo:** capa de conectores (Acción 5), prompts por
  cliente + mailer unificado (Acción 6), Key Vault + containerización (Acciones 8-9).
  Hasta entonces, un cliente nuevo se puede **configurar y validar**, pero el
  cableado al runtime se hace en las fases siguientes.
