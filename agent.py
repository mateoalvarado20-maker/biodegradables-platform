"""Agente interactivo Power BI + Outlook (Claude Sonnet 4.6).

- Power BI: vía MCP local stdio (powerbi-modeling-mcp.exe).
- Outlook: vía Microsoft Graph API con OAuth device-code (MSAL).
- Caching: bloque system con cache_control ephemeral.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
import msal
from anthropic import AsyncAnthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

POWERBI_EXE = (
    r"C:\Users\Mateo\.vscode\extensions"
    r"\analysis-services.powerbi-modeling-mcp-0.4.0-win32-x64"
    r"\server\powerbi-modeling-mcp.exe"
)
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

# Solo exponer las herramientas Power BI esenciales para evitar
# saturar el rate limit de input tokens.
PBI_TOOLS_WHITELIST = {
    "model_operations",
    "table_operations",
    "column_operations",
    "measure_operations",
    "relationship_operations",
    "dax_query_operations",
}

SYSTEM_PROMPT = """Eres un asistente que combina dos capacidades:
1) Consultar el modelo semántico de Power BI del usuario.
2) Enviar correos por Outlook.

Reglas:
- Power BI: antes de construir una consulta DAX, inspecciona las tablas y
  columnas disponibles con las herramientas del modelo. Si la pregunta es
  ambigua, pide aclaración o muestra opciones.
- Correos: SIEMPRE confirma destinatario, asunto y cuerpo con el usuario
  antes de invocar send_email. Formatea datos tabulares como HTML legible.
- Sé breve. Resume hallazgos en lenguaje claro."""

SEND_EMAIL_TOOL: dict[str, Any] = {
    "name": "send_email",
    "description": "Envía un correo desde la cuenta Outlook del usuario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Email del destinatario"},
            "subject": {"type": "string", "description": "Asunto"},
            "body_html": {"type": "string", "description": "Cuerpo en HTML"},
        },
        "required": ["to", "subject", "body_html"],
    },
}

GRAPH_CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
GRAPH_TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "common")
GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Send"]
_token_cache: dict[str, str] = {}


def get_graph_token() -> str:
    if "token" in _token_cache:
        return _token_cache["token"]
    if not GRAPH_CLIENT_ID:
        raise RuntimeError("Falta GRAPH_CLIENT_ID en variables de entorno.")
    app = msal.PublicClientApplication(
        GRAPH_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}",
    )
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow falló: {flow}")
    print(f"\n[AUTH OUTLOOK] {flow['message']}\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth falló: {result.get('error_description')}")
    _token_cache["token"] = result["access_token"]
    return result["access_token"]


async def send_email_impl(to: str, subject: str, body_html: str) -> str:
    token = get_graph_token()
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": body_html},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": True,
            },
        )
    if resp.status_code in (200, 202):
        return f"Correo enviado a {to}."
    return f"Error HTTP {resp.status_code}: {resp.text[:500]}"


def mcp_tool_to_anthropic(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


def mcp_content_to_text(content: Any) -> str:
    parts: list[str] = []
    for c in content:
        text = getattr(c, "text", None)
        parts.append(text if text is not None else str(c))
    return "\n".join(parts)


async def run_turn(
    client: AsyncAnthropic,
    mcp: ClientSession,
    tools: list[dict[str, Any]],
    history: list[dict[str, Any]],
    user_input: str,
) -> None:
    history.append({"role": "user", "content": user_input})

    while True:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools,
            messages=history,
        )

        history.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text:
                print(block.text)

        if response.stop_reason != "tool_use":
            usage = response.usage
            cached = getattr(usage, "cache_read_input_tokens", 0) or 0
            if cached:
                print(f"  [cache hit: {cached} tokens]")
            return

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"  [usando: {block.name}]")
            try:
                if block.name == "send_email":
                    result_text = await send_email_impl(**block.input)
                else:
                    mcp_result = await mcp.call_tool(block.name, block.input)
                    result_text = mcp_content_to_text(mcp_result.content)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text[:6000],
                    }
                )
            except Exception as e:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error ejecutando {block.name}: {e}",
                        "is_error": True,
                    }
                )

        history.append({"role": "user", "content": tool_results})


async def main() -> None:
    client = AsyncAnthropic()

    print("Iniciando MCP Power BI...")
    params = StdioServerParameters(command=POWERBI_EXE, args=["--start"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            all_pbi_tools = (await mcp.list_tools()).tools
            pbi_tools = [t for t in all_pbi_tools if t.name in PBI_TOOLS_WHITELIST]
            print(
                f"OK: {len(pbi_tools)}/{len(all_pbi_tools)} herramientas Power BI "
                f"expuestas (whitelist activa)."
            )

            tools = [mcp_tool_to_anthropic(t) for t in pbi_tools]
            tools.append(SEND_EMAIL_TOOL)

            history: list[dict[str, Any]] = []
            print("\nAgente listo. Escribe tu pregunta. 'salir' para terminar.\n")

            while True:
                try:
                    user_input = input("Tú: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user_input:
                    continue
                if user_input.lower() in ("salir", "exit", "quit"):
                    break

                try:
                    await run_turn(client, mcp, tools, history, user_input)
                except Exception as e:
                    print(f"\n[error] {e}\n")

    print("Adiós.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
