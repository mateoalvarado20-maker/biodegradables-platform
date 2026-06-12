from pathlib import Path
p = Path("teams_bot.py")
src = p.read_text(encoding="utf-8")
old = '''    token = request.headers.get("x-admin-token", "")
    if token != DATA_APP_PWD or not DATA_APP_PWD:
        raise HTTPException(status_code=401, detail="invalid admin token")'''
new = "    _require_admin(request)"
n = src.count(old)
src = src.replace(old, new)
p.write_text(src, encoding="utf-8")
print(f"Reemplazados: {n}")
