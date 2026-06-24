# Guion de presentación — Demo comercial (Andex)

Cómo mostrar la plataforma a un prospecto usando el entorno DEMO (empresa
ficticia **Andex**, datos sintéticos). Todo corre local, sin tocar datos de
ningún cliente real. Ver `PROPUESTA_DEMO_COMERCIAL.md` para el diseño completo.

---

## 0. Preparación (5 min antes de la reunión)

En PowerShell:

```powershell
$env:DEMO_MODE = '1'
$env:TENANT_CONFIG_SOURCE = 'yaml'
$env:TENANT_SLUG = 'andex'
$env:DEMO_EMAIL_DOMAIN = 'andexdemo.com'
$env:STATE_DIR = "$env:USERPROFILE\.andex-demo"     # estado del equipo (dedicado)
$env:DEMO_OUT = "$env:USERPROFILE\.andex-demo\out"
# Opcional: fija "hoy" para que los números sean idénticos en cada ensayo
$env:DEMO_TODAY = '2026-06-24'
# Solo si vas a usar el Data Bot en vivo:
$env:ANTHROPIC_API_KEY = '<tu key>'

python demo_console.py all
```

Esto siembra el equipo y genera los 4 artefactos + `index.html`. Abrí el
`index.html` que imprime al final en el navegador y dejá las pestañas listas.

> **Garantía anti-fuga:** cada artefacto se escanea antes de escribirse; si
> apareciera cualquier dato del cliente real, `demo_console` aborta. Además
> `DEMO_MODE` redirige/bloquea todo correo (no se envía nada a direcciones reales).

**Personaliza en vivo (opcional, alto impacto):** cambiá `display_name` y los
nombres en `tenants/andex/config.yaml` por los del prospecto, volvé a correr
`python demo_console.py all`, y la demo sale con SU empresa. (1 archivo, sin código.)

---

## 1. Demo express — 10 minutos

| Min | Bloque | Qué mostrar / decir |
|---|---|---|
| 0–1 | **Encuadre** | "Andex es una distribuidora con 2 sucursales (Quito y Guayaquil). Mirá cómo la gerencia se entera de TODO sin perseguir a nadie ni abrir un Excel." |
| 1–3 | **Reporte comercial 8 AM** (`comercial.html`) | Abrí la pestaña. Señalá: ventas de ayer, **cumplimiento de meta con semáforo**, top vendedores, y la sección de **cartera/riesgo** (deudores vencidos). "Esto llega solo, todas las mañanas a las 8." |
| 3–5 | **Reporte de logística** (`logistica.html`) | Envíos del día por ciudad y provincia, dentro de ciudad vs otras provincias, transporte cobrado, y la **columna Estado** (OK/parcial/no despachado). "Bodega marca el despacho desde el teléfono; gerencia lo ve acá." |
| 5–7 | **Data Bot en vivo** (`databot`) | En la terminal: `python demo_console.py databot "¿cuánto vendimos ayer y cómo va el mes?"` y `... databot "¿quién es el top deudor de Guayaquil?"`. Responde al instante en lenguaje natural. |
| 7–9 | **Resumen del equipo** (`equipo.html`) | "A las 6:30 PM la gerencia recibe UN correo con lo que hizo cada colaborador: actividades, cobranzas, cierre de caja por sucursal." Mostrá la sección de seguimiento (lo rojo/ámbar). |
| 9–10 | **Cierre** | "Todo esto se configura por cliente en minutos —el mismo motor, sus datos. ¿Lo armamos con los datos de ustedes para la próxima reunión?" |

**Mensaje de valor:** reemplaza horas de Excel + perseguir gente por chat; la
gerencia pasa de "pedir reportes" a "recibirlos y decidir".

---

## 2. Demo completo — 30 minutos

1. **(0–3) El dolor.** Gerencia a ciegas: datos en el ERP, en correos, en Excel,
   en cabezas. Pregunta de enganche: "¿Cómo se enteran hoy de cuánto vendieron
   ayer o quién les debe?"

2. **(3–8) Cómo ENTRA la información — el equipo.** Mostrá el flujo del
   colaborador: el check-in diario (Adaptive Card en Teams — describilo o mostrá
   captura), cierre de caja por sucursal, cobranzas asignadas. "Cero fricción:
   responden un card, no llenan un Excel." → se refleja en `equipo.html`.

3. **(8–14) Cómo se PROCESА — los agentes.** `python demo_console.py databot`
   con 3-4 preguntas (ventas, cartera, proyección "¿cómo cerramos el mes?").
   Mencioná el brief de noticias y el forecasting. "Es Claude conectado a SU ERP
   y CRM, no un chatbot genérico."

4. **(14–22) Cómo LLEGA a gerencia — los reportes.** Recorré `comercial.html`,
   `logistica.html`, `equipo.html` y `recap.html` (recap mensual con proyección).
   Disparalos/regeneralos en vivo con `python demo_console.py all`. "Todos
   automáticos, a horarios fijos, con reintentos y anti-duplicado."

5. **(22–27) Confiabilidad y multiempresa.** Abrí `tenants/andex/config.yaml`:
   "Así onboardeamos a un cliente: un archivo de configuración. Cada cliente, su
   propio entorno aislado, sus datos, su marca." Mencioná: escritura atómica,
   ledger anti-duplicado de envíos, identidad sólo desde el registro corporativo.

6. **(27–30) Cierre comercial.** Catálogo de módulos (comercial / logística /
   cartera / bots / cobranzas), aislamiento por cliente como argumento de
   seguridad, y próximos pasos: "Armamos un demo con SUS datos de muestra."

---

## 3. Comandos de referencia

```powershell
python demo_console.py all          # siembra + renderiza todo + index.html
python demo_console.py comercial    # regenera solo el comercial
python demo_console.py logistica
python demo_console.py equipo
python demo_console.py recap
python demo_console.py seed         # re-siembra el estado del equipo
python demo_console.py databot "tu pregunta aquí"
```

## 4. Checklist anti-incidentes

- [ ] `DEMO_MODE=1` y `TENANT_SLUG=andex` seteados (si no, `demo_console` aborta).
- [ ] `STATE_DIR` apunta a una carpeta demo dedicada (no a `~/.claude-agent` real).
- [ ] Ensayá una vez con `DEMO_TODAY` fijo: los números no deben sorprenderte.
- [ ] Pestañas del navegador abiertas y ordenadas antes de compartir pantalla.
- [ ] Si usás el Data Bot, verificá `ANTHROPIC_API_KEY` con una pregunta de prueba.
