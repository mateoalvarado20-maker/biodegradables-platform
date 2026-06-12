"""safe_json — persistencia JSON endurecida (Fase 1 del refactor, 2026-06-12).

Reemplaza el patrón frágil `Path.write_text(json.dumps(...))` +
`except JSONDecodeError: return {}` que tenía TODO el proyecto y que causaba
(auditoría H1/H2/A2/A3):
  - lost updates: dos escritores concurrentes se pisaban el archivo completo;
  - wipe silencioso: un archivo truncado por crash se "recuperaba" como estado
    vacío y la siguiente escritura persistía el vacío — pérdida total sin log.

Garantías de este módulo:
1. **Escritura atómica**: se escribe a un tmp en el mismo directorio, fsync,
   y `os.replace` (atómico en Windows y POSIX). Un crash a mitad de escritura
   nunca deja el archivo principal truncado.
2. **Backup `.bak`**: antes de cada escritura se preserva la versión anterior.
3. **Cuarentena**: un archivo corrupto JAMÁS se descarta. Primero se intenta
   restaurar desde `.bak`; si no hay backup legible, el corrupto se mueve a
   `<archivo>.corrupt-<timestamp>` (los datos quedan recuperables), se loguea
   CRITICAL y se invoca el hook `on_corruption` (Fase 3 lo conecta a la
   alerta por correo). Solo entonces se devuelve el default.
4. **Locks por archivo** (`threading.RLock`): los módulos de state envuelven
   sus ciclos load→mutar→save con `lock_for(path)` para que sean atómicos
   dentro del proceso (event loop + worker threads + jobs de APScheduler).

Limitación documentada: el lock es por-proceso. La protección entre procesos
distintos NO se resuelve aquí sino por diseño — un solo runtime dueño por
state file (ver auditoría §4.3). El bot corre en 1 instancia / 1 worker.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("safe_json")

_REGISTRY_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}

# Hook opcional de alerta: callable(path, motivo). Lo conecta teams_bot en
# startup para mandar correo a Mateo cuando un state entra en cuarentena.
on_corruption: Callable[[Path, str], None] | None = None


def lock_for(path: Path | str) -> threading.RLock:
    """Devuelve el RLock asociado a un archivo (uno por path, re-entrante)."""
    key = str(Path(path).resolve()) if Path(path).is_absolute() else str(path)
    with _REGISTRY_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _bak_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _quarantine(path: Path, reason: str) -> Path | None:
    """Mueve un archivo corrupto a *.corrupt-<ts> preservando los datos."""
    target = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        os.replace(path, target)
        logger.critical(
            "STATE CORRUPTO: %s movido a cuarentena %s (%s). "
            "Los datos NO se perdieron — revisar el archivo de cuarentena.",
            path, target.name, reason,
        )
    except OSError as e:
        logger.critical("STATE CORRUPTO: %s (%s) y la cuarentena fallo: %s", path, reason, e)
        target = None
    hook = on_corruption
    if hook is not None:
        try:
            hook(path, reason)
        except Exception:
            logger.exception("on_corruption hook fallo para %s", path)
    return target


def _try_read(path: Path) -> Any:
    """Lee y parsea JSON. Propaga la excepción si falla."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_json(path: Path | str, default: Callable[[], Any] | Any) -> Any:
    """Carga un JSON con recuperación segura.

    `default` puede ser un valor o un callable sin argumentos (preferido, para
    no compartir mutables). NUNCA devuelve default por corrupción sin antes
    cuarentenar el archivo y loguear CRITICAL.
    """
    path = Path(path)
    make_default = default if callable(default) else (lambda: default)
    with lock_for(path):
        if not path.exists():
            # Si el principal falta pero hay backup legible, restaurarlo
            # (crash justo después de una cuarentena, o borrado accidental).
            bak = _bak_path(path)
            if bak.exists():
                try:
                    data = _try_read(bak)
                    logger.warning("%s ausente: restaurado desde %s", path.name, bak.name)
                    save_json(path, data, backup=False)
                    return data
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass
            return make_default()
        try:
            return _try_read(path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            reason = f"{type(e).__name__}: {e}"
            bak = _bak_path(path)
            if bak.exists():
                try:
                    data = _try_read(bak)
                    _quarantine(path, reason)
                    save_json(path, data, backup=False)
                    logger.error(
                        "%s corrupto (%s): RESTAURADO desde backup %s. "
                        "Se pierden solo los cambios posteriores al ultimo save.",
                        path.name, reason, bak.name,
                    )
                    return data
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass
            _quarantine(path, reason)
            return make_default()


def save_json(
    path: Path | str,
    data: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    backup: bool = True,
) -> None:
    """Escribe JSON de forma atómica (tmp + fsync + os.replace) con backup."""
    path = Path(path)
    payload = json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=sort_keys)
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup and path.exists():
            try:
                shutil.copy2(path, _bak_path(path))
            except OSError as e:
                logger.warning("backup de %s fallo (se continua): %s", path.name, e)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def locked_update(
    path: Path | str,
    default: Callable[[], Any] | Any,
    mutate: Callable[[Any], Any],
    **save_kwargs: Any,
) -> Any:
    """RMW atómico de una pieza: load → mutate(data) → save, todo bajo lock.

    `mutate` recibe los datos y puede mutarlos in-place o devolver un valor
    nuevo (si devuelve algo no-None, eso es lo que se guarda).
    Devuelve los datos guardados.
    """
    path = Path(path)
    with lock_for(path):
        data = load_json(path, default)
        result = mutate(data)
        if result is not None:
            data = result
        save_json(path, data, **save_kwargs)
        return data
