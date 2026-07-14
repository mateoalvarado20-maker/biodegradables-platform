# Sitio ver-ia.com — v1 (para la revisión de TikTok)

5 páginas estáticas (sin build, sin frameworks): `index.html`, `about.html`,
`contact.html`, `terms.html`, `privacy.html` + `styles.css`. Abrir
`index.html` con doble click para previsualizar.

## Publicarlo (elegir UNA opción, ~15 min)

**Opción A — Azure Static Web Apps (recomendada, gratis, en la suscripción
de VER-IA):**
1. Portal Azure (cuenta VER-IA) → Create resource → **Static Web App** →
   plan Free → región cercana → deployment source: **Other**.
2. Al crearse, en Overview → **Manage deployment token** (no hace falta).
   Más simple: pestaña del recurso → **Browse** no mostrará nada aún; subir
   los archivos con la CLI `swa` O usar la Opción B si no quieren CLI.
3. Custom domains → Add → `ver-ia.com` y `www.ver-ia.com` → seguir la
   validación DNS que indica el portal (un registro TXT + CNAME/ALIAS en el
   registrador del dominio). HTTPS es automático.

**Opción B — Cloudflare Pages (gratis, drag & drop, la más rápida):**
1. dash.cloudflare.com → Workers & Pages → Create → Pages →
   **Upload assets** → arrastrar los 6 archivos de esta carpeta.
2. Custom domains → agregar `ver-ia.com` (si el DNS del dominio ya está en
   Cloudflare es 1 click; si no, seguir el asistente). HTTPS automático.

## URLs que pide TikTok (después de publicar)

- Terms of Service URL: `https://ver-ia.com/terms.html`
- Privacy Policy URL:   `https://ver-ia.com/privacy.html`

(Si el hosting elegido sirve rutas sin `.html`, también valen `/terms` y
`/privacy` — usar la forma que abra en el navegador.)

## Pendientes del dominio (👤 Daniel)

- Crear los buzones o alias `contact@ver-ia.com` y `privacy@ver-ia.com`
  (pueden ser reenvíos al correo actual) ANTES de enviar la app a revisión —
  el sitio los publica y TikTok puede escribir ahí.
- Este sitio es deliberadamente mínimo: es la cara pública para la revisión
  de TikTok. La versión comercial evoluciona sobre esta misma base.
