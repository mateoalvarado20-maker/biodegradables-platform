"""Tests del preflight — decisión del board 2026-07-13 (npm ci nunca más manual)."""

import pytest

import marketing.preflight as pf


@pytest.fixture
def entorno_completo(monkeypatch, tmp_path):
    for var in pf.REQUIRED_ENV:
        monkeypatch.setenv(var, "valor-test")
    monkeypatch.setenv("MICROSOFT_APP_ID", "x")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "x")
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "x")
    return tmp_path


def test_preflight_ok_con_entorno_completo(entorno_completo):
    # el worktree de dev tiene node_modules instalado → sin problemas
    assert pf.preflight("biodegradables") == []


def test_falta_env_var_es_bloqueante(entorno_completo, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY")
    problems = pf.preflight("biodegradables")
    assert any("PEXELS_API_KEY" in p for p in problems)


def test_sin_credenciales_de_correo_es_bloqueante(entorno_completo, monkeypatch):
    for v in ("MICROSOFT_APP_ID", "GRAPH_CLIENT_ID"):
        monkeypatch.delenv(v, raising=False)
    problems = pf.preflight("biodegradables")
    assert any("credenciales de correo" in p for p in problems)


def test_node_modules_ausente_dispara_npm_ci_automatico(entorno_completo, monkeypatch, tmp_path):
    render_fake = tmp_path / "render"
    (render_fake).mkdir()
    monkeypatch.setattr(pf, "RENDER_DIR", render_fake)
    llamadas = []

    def npm_fake(cwd):
        llamadas.append(cwd)
        (render_fake / "node_modules" / "remotion").mkdir(parents=True)

    problems = pf.preflight("biodegradables", npm_runner=npm_fake)
    assert llamadas == [render_fake]  # auto-reparación ejecutada
    assert problems == []  # y quedó operativo sin humanos


def test_npm_ci_fallido_es_bloqueante_claro(entorno_completo, monkeypatch, tmp_path):
    render_fake = tmp_path / "render"
    render_fake.mkdir()
    monkeypatch.setattr(pf, "RENDER_DIR", render_fake)

    def npm_roto(cwd):
        raise RuntimeError("registry caído")

    problems = pf.preflight("biodegradables", npm_runner=npm_roto)
    assert any("npm ci automático falló" in p for p in problems)


def test_tenant_invalido_es_bloqueante(entorno_completo):
    problems = pf.preflight("tenant-inexistente")
    assert any("configuración del tenant" in p for p in problems)