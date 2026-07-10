"""Sonda de calibración del revisor — TEST DE REGRESIÓN de la rúbrica.

4 defectos conocidos (claim inventado, CTA intermedio, duplicación literal,
comparación con competidor) + 1 pieza limpia. El juez calibrado debe rechazar
los 4 con blockers accionables y aprobar la limpia (5/5).

CORRER OBLIGATORIAMENTE tras cualquier cambio en la rúbrica del gate (regla
permanente #15 del board: los resultados extraordinarios se intentan refutar).
Usa el API real (~$0.05): `python -m marketing.calibration_probe` desde la
raíz del repo, con ANTHROPIC_API_KEY en el entorno. Exit 0 = 5/5.

Historia: nació como refutación del salto FPY 10%→100% del run 3 (2026-07-10);
la sonda demostró que el juez seguía estricto y el salto era mejora real."""
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from marketing.brand import load_brand_context, load_hard_rules
from marketing.gate import review_copy
from marketing.models import ContentPackage, ExperimentLabels, Hypothesis, Scene
from marketing.profiles import load_profile
from org.kernel import Charter, Department, TenantStore, parse_manifest

manifest = parse_manifest({
    "verops": "0.1",
    "package": {"name": "marketing-brain", "version": "0.1.0", "publisher": "ver-ia", "kind": "department"},
    "trust_tier": "first_party",
    "capabilities": [{"llm": {}}],
    "contracts": {"provides": ["WeeklyDeptReport@1"]},
    "events": {"emits": ["content.copy_review@1"]},
    "autonomy": {"max_level": "L2", "default": "L0"},
    "compliance": {"pii": "none"},
})

BASE = dict(
    tenant_id="biodegradables",
    labels=ExperimentLabels(pillar="tips-food-service", hook_type="lista",
                            format="video", time_slot="18:00-21:00", cta_type="contacto"),
    hypothesis=Hypothesis(question="¿la sonda de calibración detecta defectos conocidos?",
                          metric="views", success_criteria="4/4 rechazos + 1/1 aprobación",
                          decision_if_true="juez calibrado", decision_if_false="recalibrar"),
    title="Empaques para tu negocio de comida",
    hook="Esto le pasa a casi todos los negocios de comida",
    caption_master="Elegir bien el empaque cambia la experiencia de tu cliente. En Biodegradables Ecuador te asesoramos sin costo para encontrar el empaque ideal para tu menú. Escríbenos.",
    cta="Escríbenos y te asesoramos sin costo",
    created_at="2026-07-09T12:00:00-05:00",
)

def esc(*textos):
    return [Scene(voice_text=t, broll_keywords=["packaging"]) for t in textos]

# ~65 palabras cada guion (duración válida) — el defecto es UNO y conocido
SONDAS = {
    "claim inventado (200 grados / 6 horas)": esc(
        "Esto le pasa a casi todos los negocios de comida que piden empaques sin probar",
        "Nuestros contenedores de bagazo resisten doscientos grados durante seis horas seguidas sin deformarse jamás",
        "Por eso los restaurantes que los usan nunca tienen reclamos de sus clientes en delivery",
        "En Biodegradables Ecuador tenemos más de cuatrocientos productos para tu negocio de comida",
        "Escríbenos y te asesoramos sin costo para elegir el empaque correcto",
    ),
    "CTA en escena intermedia": esc(
        "Esto le pasa a casi todos los negocios de comida que piden empaques sin asesoría",
        "Escríbenos ya mismo a nuestro correo y pide tu cotización antes de seguir viendo",
        "El empaque equivocado arruina la experiencia aunque tu comida sea la mejor de la ciudad",
        "Por eso damos asesoría técnica gratis antes de que hagas tu primer pedido grande",
        "Escríbenos y te asesoramos sin costo para elegir el empaque correcto",
    ),
    "duplicación real dentro del guion": esc(
        "El empaque equivocado arruina la experiencia aunque tu comida sea la mejor de la ciudad",
        "Pedir un lote grande sin probar muestras técnicas es el error más caro del food service",
        "El empaque equivocado arruina la experiencia aunque tu comida sea la mejor de la ciudad",
        "Por eso damos asesoría técnica gratis antes de que hagas tu primer pedido",
        "Escríbenos y te asesoramos sin costo para elegir el empaque correcto",
    ),
    "comparación con competidor": esc(
        "Esto le pasa a casi todos los negocios de comida que piden empaques sin asesoría",
        "A diferencia de EcoPack y las otras marcas del mercado nosotros sí cumplimos lo que prometemos",
        "Los demás proveedores te venden catálogo nosotros te damos asesoría técnica real y gratuita",
        "En Biodegradables Ecuador tenemos más de cuatrocientos productos en un solo lugar",
        "Escríbenos y te asesoramos sin costo para elegir el empaque correcto",
    ),
    "LIMPIA (debe aprobar)": esc(
        "Esto le pasa a casi todos los negocios de comida que piden empaques sin asesoría",
        "Eligen por precio y descubren tarde que el contenedor no aguanta su tipo de comida",
        "El resultado es plata perdida y clientes que no vuelven a pedir por delivery",
        "En Biodegradables Ecuador damos asesoría técnica gratis con más de cuatrocientos productos disponibles",
        "Escríbenos y te asesoramos sin costo para elegir el empaque correcto para tu menú",
    ),
}

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    store = TenantStore("biodegradables", base_dir=tmp)
    dept = Department(manifest, Charter(okrs=("sonda",), budget_usd_month=2.0,
                      approved_by="dsanchez@biodegradablesecuador.com", approved_at="2026-07-09",
                      hard_rules=load_hard_rules("biodegradables")), store,
                      granted_capabilities={"llm"})
    brand = load_brand_context("biodegradables")
    profile = load_profile("tiktok")

    ok = 0
    for i, (nombre, scenes) in enumerate(SONDAS.items()):
        pkg = ContentPackage(package_id=f"pkg-sonda-{i:02d}xx", scenes=scenes,
                             hashtags_master=["foodservice", "ecuador"], **BASE)
        v = review_copy(dept, pkg, profile, brand)
        esperado_rechazo = "LIMPIA" not in nombre
        correcto = v.approved != esperado_rechazo
        ok += int(correcto)
        estado = "✔ CORRECTO" if correcto else "✘ FALLÓ LA SONDA"
        print(f"{estado} · {nombre}: approved={v.approved} score={v.score}")
        for r in v.reasons[:2]:
            print(f"    blocker: {r[:110]}")
    print(f"\nSONDA: {ok}/5 correctas · costo ${dept.meter.month_usd():.4f}")
    store.close()
    sys.exit(0 if ok == 5 else 1)
