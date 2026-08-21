"""Minimaler MCP-Client fuer den lokalen Fusion 360 MCP Server.

Spricht das MCP-Protokoll (streamable HTTP) direkt ueber urllib —
keine externen Abhaengigkeiten. Der Server wird in Fusion 360 unter
Voreinstellungen > Allgemein > "Fusion MCP Server" aktiviert.
"""
from __future__ import annotations

import json

from i18n import t
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:27182/mcp"
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT_S = 180


class FusionMcpError(RuntimeError):
    """Fehler bei der Kommunikation mit dem Fusion MCP Server."""


class FusionMcpClient:
    """Kleine Session fuer Tool-Aufrufe gegen den Fusion MCP Server."""

    def __init__(self, url: str = DEFAULT_URL) -> None:
        self.url = url
        self._session_id: str | None = None
        self._next_id = 0

    def _post(self, payload: dict) -> tuple[str | None, dict | None]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                session_id = resp.headers.get("MCP-Session-Id")
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise FusionMcpError(
                t("mcp.unreachable", url=f"{self.url}, {reason}")) from exc
        except (TimeoutError, OSError) as exc:
            raise FusionMcpError(
                t("mcp.timeout", seconds=TIMEOUT_S) + f" ({exc})") from exc
        if not body.strip():
            return session_id, None
        if "text/event-stream" in content_type:
            body = self._sse_payload(body)
        try:
            return session_id, json.loads(body)
        except json.JSONDecodeError as exc:
            raise FusionMcpError(
                f"Antwort des Servers ist kein JSON ({content_type}): {body[:300]}"
            ) from exc

    @staticmethod
    def _sse_payload(body: str) -> str:
        """JSON-Nutzlast aus einer Server-Sent-Events-Antwort ziehen."""
        data_lines = [
            line[5:].lstrip()
            for line in body.splitlines()
            if line.startswith("data:")
        ]
        return "\n".join(data_lines)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, data = self._post(payload)
        if data is None:
            raise FusionMcpError(f"Leere Antwort auf '{method}'.")
        if "error" in data:
            raise FusionMcpError(f"MCP-Fehler bei '{method}': {data['error']}")
        return data

    def connect(self) -> None:
        """Initialisiert die MCP-Session (Handshake)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "fusion360-to-svg", "version": "1.0"},
            },
        }
        session_id, data = self._post(payload)
        if data is None or "error" in (data or {}):
            raise FusionMcpError(f"Initialize fehlgeschlagen: {data}")
        self._session_id = session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict) -> list[dict]:
        """Ruft ein MCP-Tool auf und liefert dessen Content-Liste zurueck."""
        data = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = data.get("result") or {}
        content = result.get("content")
        if not content:
            raise FusionMcpError(f"Tool '{name}' lieferte keinen Inhalt: {data}")
        return content

    def run_fusion_script(self, script: str) -> str:
        """Fuehrt ein Python-Skript in Fusion aus und liefert dessen print-Ausgabe.

        Das Skript muss eine Funktion ``def run(_context: str)`` definieren.
        Wirft FusionMcpError, wenn das Skript in Fusion eine Exception ausloest.
        """
        content = self.call_tool(
            "fusion_mcp_execute",
            {"featureType": "script", "object": {"script": script}},
        )
        try:
            result = json.loads(content[0]["text"])
        except (KeyError, ValueError) as exc:
            raise FusionMcpError(f"Unerwartetes Tool-Ergebnis: {content[0]}") from exc
        if not result.get("success", False):
            raise FusionMcpError(
                t("mcp.script_error", error=result.get("error", result)))
        return str(result.get("message", ""))
