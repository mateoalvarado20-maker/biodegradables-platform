# Registro de la app en TikTok for Developers — guía campo por campo

**Para:** Daniel (M3.0d) · **Fecha:** 2026-07-14 · **Fuente técnica:**
`docs/revision-tecnica-m3-0.md`. Los valores de abajo se copian tal cual.

## Campos básicos

| Campo | Valor |
|---|---|
| App name | `VER-IA` |
| Category | Business / Productivity (la más cercana disponible) |
| Website URL | `https://ver-ia.com` |
| Terms of Service URL | `https://ver-ia.com/terms.html` |
| Privacy Policy URL | `https://ver-ia.com/privacy.html` |
| Platforms | **Web** únicamente (ver abajo) |
| Redirect URI | `https://biodegradables-bot-app-cvgnasgec8eqatdg.centralus-01.azurewebsites.net/oauth/tiktok/callback` |
| Products | **Login Kit** + **Content Posting API** (activar **Direct Post**) |
| Scopes | `user.info.basic`, `video.publish` |

## Description (copiar tal cual)

> VER-IA is a B2B software-as-a-service platform that provides businesses
> with AI agents for daily operations: business reporting, team coordination,
> and social media content production.
>
> Our customers explicitly connect their own TikTok business account through
> TikTok's official OAuth authorization flow. VER-IA's platform drafts
> short-form video content for the customer's account, runs it through an
> automated quality-control gate, and then holds every piece for explicit
> human review: the account owner approves or rejects each video before
> anything is published. The Content Posting API is used exclusively to
> publish content that the connected account's owner has individually
> approved, always within TikTok's posting limits.
>
> VER-IA does not scrape TikTok, does not automate browsers, does not
> interact with other users' content, and takes no action outside the scope
> explicitly granted by the account owner. Connections can be revoked by the
> account owner at any time, from TikTok's app permissions page or by
> contacting us; revoked tokens are deleted. OAuth tokens are stored
> encrypted (AES-256-GCM) with per-customer isolation, and we never receive
> or store TikTok passwords.

## Platforms — qué marcar y por qué

Marcar **solo "Web"**. Nuestro flujo OAuth es 100% server-side: la
autorización se abre en el navegador y TikTok redirige a nuestro endpoint
HTTPS (el Redirect URI de arriba). No hay app iOS/Android ni cliente desktop
que hable con TikTok — marcar plataformas que no existen alarga la revisión
(piden builds/stores para cada una) y puede causar rechazo.

## El video requerido — qué subir

Subir un **video demostrativo del flujo de VER-IA** (screencast), NO un video
de Biodegradables ni un video "de prueba". Razón: ese video es para el
REVISOR de TikTok — lo que evalúa es si el uso de la API coincide con lo
declarado; un comercial de producto no le muestra nada y un video de relleno
da señal de descuido. El screencast ideal (1-2 min, sin audio está bien,
puede grabarse con la barra de juegos de Windows Win+G):

1. La tarjeta de aprobación en Teams con un video generado (título, duración,
   botones Aprobar/Rechazar) → tocar **Aprobar**.
2. La pantalla de autorización OAuth de TikTok (el `connect-start` genera la
   URL) mostrando que el dueño de la cuenta consiente los permisos.
3. El estado del sistema mostrando la pieza aprobada lista para publicar
   (`python -m marketing.daily_run status` o la cola `scheduled`).
4. Un cierre con el sitio ver-ia.com visible.

Si piden específicamente "un video de ejemplo del contenido a publicar",
adjuntar además uno de los MP4 reales generados
(`~/.ver-os/prod-biodegradables/out/pkg-e8b66107e794.mp4` — ya aprobado por
L0): es contenido corporativo inocuo y representativo.

## Antes de enviar a revisión (checklist)

- [ ] Sitio publicado en `https://ver-ia.com` (ver `website/ver-ia/README-deploy.md`)
- [ ] `contact@ver-ia.com` y `privacy@ver-ia.com` existen (alias/forward vale)
- [ ] Redirect URI copiado EXACTO (el endpoint ya está vivo y responde)
- [ ] Login Kit + Content Posting API agregados, Direct Post activado
- [ ] Solo scopes `user.info.basic` y `video.publish`
- [ ] Video demostrativo subido

**Nota:** aunque la revisión tarde, podemos avanzar: con la app creada (aun
"unaudited") los posts salen forzados a privado (SELF_ONLY) — exactamente el
modo cuenta-de-prueba de M3.1. La auditoría solo hace falta para posts
públicos (M3.3).
