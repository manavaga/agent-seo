"""Lightweight MCP client for handshake and tool introspection.

Supports SSE and Streamable HTTP transports.
Does NOT require the mcp SDK — implements the minimal JSON-RPC subset needed.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import httpx

from .models import MCPInfo

TIMEOUT = 15.0
HEADERS = {"User-Agent": "AgentSEO/0.2 (mcp-handshake)"}


def _jsonrpc_request(method: str, params: Optional[dict] = None, id: Optional[str] = None) -> dict:
    """Build a JSON-RPC 2.0 request."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return msg


def _jsonrpc_notification(method: str, params: Optional[dict] = None) -> dict:
    """Build a JSON-RPC 2.0 notification (no id = no response expected)."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return msg


# ---------------------------------------------------------------------------
# Streamable HTTP transport
# ---------------------------------------------------------------------------

def _handshake_streamable_http(client: httpx.Client, endpoint: str) -> MCPInfo:
    """Perform MCP handshake over Streamable HTTP transport.

    Streamable HTTP: POST JSON-RPC to a single endpoint.
    Server responds with JSON or SSE stream.
    """
    info = MCPInfo(transport="streamable_http")

    # Step 1: Send initialize
    init_request = _jsonrpc_request(
        method="initialize",
        params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "agent-seo", "version": "0.2.0"},
        },
        id=str(uuid.uuid4()),
    )

    start = time.monotonic()
    try:
        resp = client.post(
            endpoint,
            json=init_request,
            headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=TIMEOUT,
        )
        info.handshake_latency_ms = (time.monotonic() - start) * 1000

        if resp.status_code != 200:
            info.error = f"Initialize returned {resp.status_code}"
            return info

        # Parse response — could be direct JSON or SSE
        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            # Parse SSE response
            result = _parse_sse_response(resp.text)
        else:
            # Direct JSON response
            result = resp.json()

        if not result:
            info.error = "Empty initialize response"
            return info

        # Extract from JSON-RPC result
        init_result = result.get("result", result)
        info.connected = True
        info.protocol_version = init_result.get("protocolVersion", "")
        info.server_name = init_result.get("serverInfo", {}).get("name", "")
        info.server_version = init_result.get("serverInfo", {}).get("version", "")
        info.capabilities = init_result.get("capabilities", {})

        # Check for session ID
        session_id = resp.headers.get("mcp-session-id", "")

        # Step 2: Send initialized notification
        init_notification = _jsonrpc_notification("notifications/initialized")
        extra_headers = {**HEADERS, "Content-Type": "application/json"}
        if session_id:
            extra_headers["Mcp-Session-Id"] = session_id

        try:
            client.post(endpoint, json=init_notification, headers=extra_headers, timeout=5.0)
        except Exception:
            pass  # Notification — no response expected

        # Step 3: Request tools/list
        if "tools" in info.capabilities:
            info = _fetch_tools_streamable(client, endpoint, session_id, info)

    except httpx.TimeoutException:
        info.error = "Handshake timed out"
    except Exception as e:
        info.error = f"Handshake failed: {e}"

    return info


def _fetch_tools_streamable(client: httpx.Client, endpoint: str, session_id: str, info: MCPInfo) -> MCPInfo:
    """Fetch tools list over Streamable HTTP."""
    tools_request = _jsonrpc_request(
        method="tools/list",
        params={},
        id=str(uuid.uuid4()),
    )

    extra_headers = {**HEADERS, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session_id:
        extra_headers["Mcp-Session-Id"] = session_id

    start = time.monotonic()
    try:
        resp = client.post(endpoint, json=tools_request, headers=extra_headers, timeout=TIMEOUT)
        info.tools_list_latency_ms = (time.monotonic() - start) * 1000

        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                result = _parse_sse_response(resp.text)
            else:
                result = resp.json()

            tools_result = result.get("result", result)
            tools = tools_result.get("tools", [])
            info.tools = tools
            info.tool_count = len(tools)
    except Exception as e:
        info.error = f"tools/list failed: {e}"

    return info


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------

def _handshake_sse(client: httpx.Client, sse_url: str, base_url: str) -> MCPInfo:
    """Perform MCP handshake over SSE transport.

    SSE transport:
    - GET /mcp/sse to establish SSE stream (receives endpoint URL)
    - POST messages to the received endpoint URL
    """
    info = MCPInfo(transport="sse")

    start = time.monotonic()
    try:
        # Step 1: Connect to SSE endpoint to get the message URL
        with client.stream("GET", sse_url, headers=HEADERS, timeout=TIMEOUT) as stream:
            message_url = None

            # Read SSE events to find the endpoint event
            buffer = ""
            for chunk in stream.iter_text():
                buffer += chunk
                # Parse SSE events
                while "\n\n" in buffer:
                    event_text, buffer = buffer.split("\n\n", 1)
                    lines = event_text.strip().split("\n")

                    event_type = ""
                    event_data = ""
                    for line in lines:
                        if line.startswith("event: "):
                            event_type = line[7:].strip()
                        elif line.startswith("data: "):
                            event_data = line[6:].strip()

                    if event_type == "endpoint" and event_data:
                        # The endpoint URL might be relative
                        if event_data.startswith("/"):
                            message_url = base_url.rstrip("/") + event_data
                        else:
                            message_url = event_data
                        break

                if message_url:
                    break

            if not message_url:
                info.error = "No endpoint event received from SSE"
                return info

            info.handshake_latency_ms = (time.monotonic() - start) * 1000

            # Step 2: Send initialize via POST to the message URL
            init_request = _jsonrpc_request(
                method="initialize",
                params={
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-seo", "version": "0.2.0"},
                },
                id=str(uuid.uuid4()),
            )

            # We need a separate client for the POST since we're inside a stream context
            pass  # Will handle after stream

    except httpx.TimeoutException:
        info.error = "SSE connection timed out"
        return info
    except Exception as e:
        info.error = f"SSE connection failed: {e}"
        return info

    if not message_url:
        return info

    # Now POST to the message URL (outside the stream context)
    try:
        init_request = _jsonrpc_request(
            method="initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "agent-seo", "version": "0.2.0"},
            },
            id=str(uuid.uuid4()),
        )

        resp = client.post(
            message_url,
            json=init_request,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )

        if resp.status_code in (200, 202):
            # For SSE transport, the response might come back on the SSE stream
            # or as a direct response. Try to parse direct response first.
            try:
                result = resp.json()
                init_result = result.get("result", result)
                info.connected = True
                info.protocol_version = init_result.get("protocolVersion", "")
                info.server_name = init_result.get("serverInfo", {}).get("name", "")
                info.server_version = init_result.get("serverInfo", {}).get("version", "")
                info.capabilities = init_result.get("capabilities", {})
            except (json.JSONDecodeError, ValueError):
                # Response came on SSE stream — we got accepted (202)
                info.connected = True

        # Send initialized notification
        init_notification = _jsonrpc_notification("notifications/initialized")
        try:
            client.post(message_url, json=init_notification, headers={**HEADERS, "Content-Type": "application/json"}, timeout=5.0)
        except Exception:
            pass

        # Fetch tools if supported
        if info.connected and ("tools" in info.capabilities or not info.capabilities):
            tools_request = _jsonrpc_request(method="tools/list", params={}, id=str(uuid.uuid4()))

            tools_start = time.monotonic()
            try:
                tools_resp = client.post(
                    message_url,
                    json=tools_request,
                    headers={**HEADERS, "Content-Type": "application/json"},
                    timeout=TIMEOUT,
                )
                info.tools_list_latency_ms = (time.monotonic() - tools_start) * 1000

                if tools_resp.status_code == 200:
                    try:
                        tools_result = tools_resp.json()
                        tools_data = tools_result.get("result", tools_result)
                        tools = tools_data.get("tools", [])
                        info.tools = tools
                        info.tool_count = len(tools)
                    except (json.JSONDecodeError, ValueError):
                        pass
            except Exception:
                pass

    except Exception as e:
        info.error = f"SSE POST failed: {e}"

    return info


# ---------------------------------------------------------------------------
# SSE response parser
# ---------------------------------------------------------------------------

def _parse_sse_response(text: str) -> Optional[dict]:
    """Parse a JSON-RPC response from SSE formatted text."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data = line[6:]
            try:
                return json.loads(data)
            except (json.JSONDecodeError, ValueError):
                continue
    # Try parsing the whole thing as JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Tool schema analysis
# ---------------------------------------------------------------------------

def analyze_tool_quality(tools: list[dict]) -> dict:
    """Analyze the quality of MCP tool schemas.

    Returns metrics about description quality, schema completeness, etc.
    """
    if not tools:
        return {"tool_count": 0, "quality_score": 0}

    total_tools = len(tools)
    has_description = 0
    good_description = 0  # > 50 chars, contains a verb-like word
    has_input_schema = 0
    has_properties = 0
    has_required = 0
    has_param_descriptions = 0
    has_annotations = 0
    total_params = 0
    described_params = 0
    has_examples = 0
    has_enums = 0

    for tool in tools:
        # Top-level description
        desc = str(tool.get("description", ""))
        if len(desc) > 10:
            has_description += 1
        if len(desc) > 50:
            good_description += 1

        # Input schema
        schema = tool.get("inputSchema", {})
        if schema and isinstance(schema, dict):
            has_input_schema += 1

            props = schema.get("properties", {})
            if props:
                has_properties += 1

                # Check parameter descriptions
                for param_name, param_def in props.items():
                    total_params += 1
                    if isinstance(param_def, dict):
                        if param_def.get("description"):
                            described_params += 1
                        if param_def.get("examples") or param_def.get("default") is not None:
                            has_examples += 1
                        if param_def.get("enum"):
                            has_enums += 1

            if schema.get("required"):
                has_required += 1

            # Check for param-level descriptions
            if props and described_params > 0:
                has_param_descriptions += 1

        # Annotations
        annotations = tool.get("annotations", {})
        if annotations and isinstance(annotations, dict):
            has_annotations += 1

    # Calculate quality score (0-100)
    scores = []
    scores.append(has_description / total_tools * 100)          # Basic descriptions
    scores.append(good_description / total_tools * 100)          # Good descriptions
    scores.append(has_input_schema / total_tools * 100)          # Has schemas
    scores.append(has_properties / total_tools * 100)            # Has properties defined
    scores.append(has_required / total_tools * 100)              # Has required fields
    scores.append((described_params / total_params * 100) if total_params > 0 else 0)  # Param descriptions
    scores.append(has_annotations / total_tools * 100)           # Has annotations

    quality_score = round(sum(scores) / len(scores))

    return {
        "tool_count": total_tools,
        "quality_score": quality_score,
        "has_description": has_description,
        "good_description": good_description,
        "has_input_schema": has_input_schema,
        "has_properties": has_properties,
        "has_required": has_required,
        "has_param_descriptions": has_param_descriptions,
        "has_annotations": has_annotations,
        "total_params": total_params,
        "described_params": described_params,
        "param_description_pct": round(described_params / total_params * 100) if total_params > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Auto-detect and connect
# ---------------------------------------------------------------------------

def mcp_handshake(base_url: str, mcp_endpoint: Optional[str] = None) -> MCPInfo:
    """Auto-detect transport and perform MCP handshake.

    Tries in order:
    1. Explicit MCP endpoint if provided
    2. /.well-known/mcp.json for discovery
    3. /mcp/stream (Streamable HTTP)
    4. /mcp/sse (SSE)
    """
    base_url = base_url.rstrip("/")

    with httpx.Client() as client:
        # Try to discover MCP endpoint from well-known
        if not mcp_endpoint:
            try:
                mcp_json = client.get(
                    f"{base_url}/.well-known/mcp.json",
                    headers=HEADERS,
                    timeout=5.0,
                    follow_redirects=True,
                ).json()
                transports = mcp_json.get("transports", {})
                if "streamable_http" in transports:
                    mcp_endpoint = transports["streamable_http"]
                elif "sse" in transports:
                    mcp_endpoint = transports["sse"]
            except Exception:
                pass

        # Try Streamable HTTP first
        if not mcp_endpoint or "stream" in str(mcp_endpoint):
            stream_url = mcp_endpoint or f"{base_url}/mcp/stream"
            if not stream_url.startswith("http"):
                stream_url = f"{base_url}{stream_url}"

            info = _handshake_streamable_http(client, stream_url)
            if info.connected:
                return info

        # Try SSE
        sse_url = mcp_endpoint if (mcp_endpoint and "sse" in str(mcp_endpoint)) else f"{base_url}/mcp/sse"
        if not sse_url.startswith("http"):
            sse_url = f"{base_url}{sse_url}"

        info = _handshake_sse(client, sse_url, base_url)
        if info.connected:
            return info

        # Nothing worked
        if not info.error:
            info.error = "No MCP transport detected (tried Streamable HTTP and SSE)"
        return info
