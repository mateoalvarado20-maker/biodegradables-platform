"""Tests de la capa de persistencia endurecida (Fase 1).

Cubren las garantías que la auditoría encontró ausentes (H1/H2/A2/A3):
escritura atómica, backup, cuarentena ante corrupción y RMW sin lost updates.
"""
from __future__ import annotations

import json
import threading

import pytest


# ---------- Recuperación ante fallos ----------

def test_load_missing_devuelve_default(state_env):
    sj = state_env.safe_json
    path = state_env.dir / "nuevo.json"
    assert sj.load_json(path, lambda: {"x": 1}) == {"x": 1}
    assert not path.exists()  # load no crea el archivo


def test_roundtrip_basico(state_env):
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    sj.save_json(path, {"a": [1, 2], "ñ": "tildes ok"})
    assert sj.load_json(path, dict) == {"a": [1, 2], "ñ": "tildes ok"}


def test_corrupcion_con_backup_se_restaura(state_env):
    """Un archivo truncado se restaura desde .bak — NO se pierde el estado."""
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    sj.save_json(path, {"v": 1})
    sj.save_json(path, {"v": 2})  # genera .bak con {"v": 1}
    # Simula crash a mitad de escritura: archivo truncado
    path.write_text('{"v": 2, "trunc', encoding="utf-8")

    data = sj.load_json(path, dict)
    assert data == {"v": 1}  # restaurado del backup
    # El corrupto quedó en cuarentena, no se descartó
    quarantined = list(state_env.dir.glob("s.json.corrupt-*"))
    assert len(quarantined) == 1
    assert "trunc" in quarantined[0].read_text(encoding="utf-8")
    # Y el archivo principal quedó reparado: el próximo load es normal
    assert sj.load_json(path, dict) == {"v": 1}


def test_corrupcion_sin_backup_cuarentena_y_default(state_env):
    """Sin backup: devuelve default PERO preserva el corrupto y avisa."""
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    path.write_text("garbage{{{", encoding="utf-8")

    alerts = []
    sj.on_corruption = lambda p, reason: alerts.append((p, reason))
    try:
        data = sj.load_json(path, lambda: {"users": {}})
    finally:
        sj.on_corruption = None

    assert data == {"users": {}}
    quarantined = list(state_env.dir.glob("s.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "garbage{{{"
    assert len(alerts) == 1  # el hook de alerta se invocó


def test_wipe_silencioso_eliminado(state_env):
    """El escenario exacto de la auditoría H2: corrupción + save posterior.

    Antes: load() devolvía {} y save() persistía el vacío = pérdida total.
    Ahora: el contenido previo sobrevive (vía .bak) o queda en cuarentena.
    """
    sj = state_env.safe_json
    path = state_env.dir / "activity.json"
    original = {"users": {"a@x.com": {"weeks": {"2026-W24": {"activities": {"apollo": {}}}}}}}
    sj.save_json(path, original)
    sj.save_json(path, original)  # asegura .bak poblado
    path.write_text('{"users": {"a@x.com"', encoding="utf-8")  # crash simulado

    data = sj.load_json(path, lambda: {"users": {}})
    sj.save_json(path, data)  # el "save posterior" que antes consumaba el wipe

    final = sj.load_json(path, lambda: {"users": {}})
    assert final == original  # nada se perdió


def test_save_no_serializable_no_toca_el_archivo(state_env):
    """Si el dump falla, el archivo previo queda intacto (dump antes de abrir)."""
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    sj.save_json(path, {"ok": True})
    with pytest.raises(TypeError):
        sj.save_json(path, {"bad": object()})
    assert sj.load_json(path, dict) == {"ok": True}
    assert not list(state_env.dir.glob(".s.json.tmp-*"))  # sin tmp huérfanos


def test_principal_ausente_se_restaura_de_backup(state_env):
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    sj.save_json(path, {"v": 1})
    sj.save_json(path, {"v": 2})
    path.unlink()  # borrado accidental / crash post-cuarentena
    # El .bak preserva la versión ANTERIOR al último save: se recupera v=1
    # (se pierde solo el último write, nunca todo el estado).
    assert sj.load_json(path, dict) == {"v": 1}


# ---------- Concurrencia ----------

def test_locked_update_sin_lost_updates(state_env):
    """8 hilos × 50 incrementos concurrentes = 400 exactos. Antes se perdían."""
    sj = state_env.safe_json
    path = state_env.dir / "counter.json"
    THREADS, ITERS = 8, 50

    def worker():
        for _ in range(ITERS):
            sj.locked_update(
                path, lambda: {"n": 0},
                lambda d: d.__setitem__("n", d["n"] + 1),
            )

    ts = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert sj.load_json(path, dict)["n"] == THREADS * ITERS


def test_escrituras_concurrentes_archivo_siempre_valido(state_env):
    """Bajo escritura concurrente intensa, el archivo NUNCA queda ilegible."""
    sj = state_env.safe_json
    path = state_env.dir / "s.json"
    stop = threading.Event()
    errors: list[Exception] = []

    def writer(i: int):
        k = 0
        while not stop.is_set():
            try:
                sj.save_json(path, {"writer": i, "k": k})
                k += 1
            except Exception as e:  # noqa: BLE001 — test colecta todo
                errors.append(e)

    def reader():
        while not stop.is_set():
            try:
                data = sj.load_json(path, dict)
                assert isinstance(data, dict)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    import time
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join()

    assert errors == []
    json.loads(path.read_text(encoding="utf-8"))  # legible al final
