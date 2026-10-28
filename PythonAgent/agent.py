# agent.py
import sys
import time
import json
import asyncio
import logging
import traceback
from pathlib import Path
from typing import List, Optional

import ollama
from fastapi import FastAPI
from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "llama3.1:8b"
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
SERVER_SCRIPT = BASE_DIR / "npc_server.py"

VALID_ACTIONS = {
    "ATTACK",
    "TAKE_COVER",
    "RETREAT",
    "CALL_FOR_HELP",
    "RELOAD",
    "HOLD_POSITION",
}

REQUIRED_MCP_TOOLS = (
    "get_npc_state",
    "get_legal_actions",
    "get_environment_context",
)

SYSTEM_PROMPT = (
    "You are a guard NPC's tactical brain in a combat game. "
    "Always call get_npc_state, get_legal_actions, and get_environment_context first to inspect the situation, "
    "then choose the single best tactical action from the legal actions.\n"
    "Evaluate the full tactical context returned by the MCP tools:\n"
    "- When health is high, ammo is plentiful, and enemy count is low (e.g. 1 enemy), press the advantage with ATTACK.\n"
    "- When health is critically low, prioritize survival by choosing TAKE_COVER (if legal) or RETREAT.\n"
    "- When ammo is 0, choose RELOAD so you can fight again.\n"
    "- When outnumbered with no cover available, choose RETREAT or CALL_FOR_HELP rather than fighting exposed.\n"
    "- When supported by multiple nearby allies (e.g. nearbyAllies >= 2) with cover available, leverage ally support for a coordinated defense (such as HOLD_POSITION, TAKE_COVER, or ATTACK) and mention the nearby allies in your reason."
)

app = FastAPI(title="State-Aware NPC Decision Agent")
log = logging.getLogger("uvicorn.error")
llm = ollama.AsyncClient()          # Async client so the FastAPI event loop is not blocked
decide_lock = asyncio.Lock()        # Serialize access to the shared state.json file


class NpcStateModel(BaseModel):
    npcId: str
    health: float
    ammo: int
    enemyDistance: float
    enemyCount: int
    nearbyAllies: int
    coverAvailable: bool
    currentAction: str
    stateVersion: Optional[int] = None


class EnvironmentModel(BaseModel):
    nearest_cover_distance: float
    objective_under_threat: bool


class DecisionRequest(BaseModel):
    stateVersion: int
    npc: NpcStateModel
    legalActions: List[str]
    environment: EnvironmentModel


def unwrap_exception(exc: BaseException) -> BaseException:
    """Unwraps nested ExceptionGroups (e.g. from anyio/MCP task groups) to expose the root exception."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def tool_result_text(res) -> str:
    """Converts an MCP CallToolResult into a string and raises RuntimeError if the tool reported an error."""
    texts = [c.text for c in getattr(res, "content", []) if hasattr(c, "text")]
    joined = texts[0] if len(texts) == 1 else json.dumps(texts)
    if getattr(res, "isError", False):
        raise RuntimeError(f"MCP tool execution error: {joined}")
    return joined


def parse_legal_actions(res) -> list[str]:
    """Parses and validates the legal action list returned by the get_legal_actions MCP tool."""
    if getattr(res, "isError", False):
        err_text = tool_result_text(res)
        raise RuntimeError(f"get_legal_actions failed: {err_text}")

    texts = [c.text for c in getattr(res, "content", []) if hasattr(c, "text")]
    if len(texts) == 1 and texts[0].lstrip().startswith("["):
        parsed = json.loads(texts[0])
    else:
        parsed = texts

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a list of legal actions, got: {parsed!r}")

    valid = [str(a) for a in parsed if str(a) in VALID_ACTIONS]
    if not valid:
        raise ValueError(f"No valid actions found in MCP legalActions response: {parsed!r}")
    return valid


async def run_decision_loop() -> tuple[dict, list[str], list[str]]:
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"State file not found: {STATE_FILE}")
    with open(STATE_FILE, "r", encoding="utf-8") as sf:
        state_check = json.load(sf)
    if not isinstance(state_check, dict) or "npc" not in state_check or "legalActions" not in state_check:
        raise ValueError("Invalid state.json structure: missing 'npc' or 'legalActions'.")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
    )
    tools_called: list[str] = []
    tool_outputs: dict[str, str] = {}

    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        # List MCP tools and convert to Ollama tool schema format
        mcp_tools = (await s.list_tools()).tools
        tools = [{
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
                              or {"type": "object", "properties": {}}
            }
        } for t in mcp_tools]

        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "What should this guard do now?"}
        ]

        # Stage 1: Bounded MCP tool-calling loop (max 4 iterations)
        for _ in range(4):
            resp = await llm.chat(
                model=MODEL,
                messages=msgs,
                tools=tools,
                options={"temperature": 0},
                keep_alive="30m",
            )
            msgs.append(resp.message)

            if not resp.message.tool_calls:
                break

            for call in resp.message.tool_calls:
                tool_name = call.function.name
                if tool_name not in tools_called:
                    tools_called.append(tool_name)
                try:
                    if tool_name in tool_outputs:
                        content = tool_outputs[tool_name]
                    else:
                        res = await s.call_tool(tool_name, call.function.arguments or {})
                        if getattr(res, "isError", False) and call.function.arguments:
                            res = await s.call_tool(tool_name, {})
                        content = tool_result_text(res)
                        tool_outputs[tool_name] = content
                except Exception as e:
                    content = f"Tool error: {e}"
                msgs.append({"role": "tool", "content": content, "tool_name": tool_name})

            # Once all available MCP tools have been inspected, proceed to the final schema-constrained decision
            if all(t["function"]["name"] in tools_called for t in tools):
                break

        # Safeguard: if the model skipped any required MCP tool, call it now so the decision is never blind
        for req_tool in REQUIRED_MCP_TOOLS:
            if req_tool not in tool_outputs:
                res = await s.call_tool(req_tool, {})
                content = tool_result_text(res)
                tool_outputs[req_tool] = content
                if req_tool not in tools_called:
                    tools_called.append(req_tool)
                msgs.append({"role": "tool", "content": content, "tool_name": req_tool})

        # Stage 2: Schema-constrained final decision with dynamic legal action enum
        legal = parse_legal_actions(await s.call_tool("get_legal_actions", {}))

        schema = {
            "type": "object",
            "required": ["action", "confidence", "reason"],
            "properties": {
                "action": {"type": "string", "enum": legal},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"}
            }
        }

        final_prompt = (
            "MCP tool observations:\n"
            f"- get_npc_state: {tool_outputs.get('get_npc_state', '{}')}\n"
            f"- get_legal_actions: {json.dumps(legal)}\n"
            f"- get_environment_context: {tool_outputs.get('get_environment_context', '{}')}\n\n"
            "Based on these observations, choose the single best tactical action from get_legal_actions "
            "and return your final decision as JSON. Keep the reason to one short sentence citing the key state values."
        )
        final_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": final_prompt},
        ]
        final = await llm.chat(
            model=MODEL,
            messages=final_messages,
            format=schema,
            options={"temperature": 0, "num_predict": 200},
            keep_alive="30m",
        )
        parsed_decision = json.loads(final.message.content)
        return parsed_decision, tools_called, legal


def fallback_action(req: DecisionRequest) -> str:
    """Deterministic safe fallback action when an LLM or MCP error occurs."""
    for a in ("TAKE_COVER", "RETREAT", "HOLD_POSITION"):
        if a in req.legalActions:
            return a
    for a in req.legalActions:
        if a in VALID_ACTIONS:
            return a
    return "HOLD_POSITION"


@app.post("/decide")
async def decide_endpoint(req: DecisionRequest):
    start_time = time.perf_counter()

    # Handle empty legal action list immediately without calling the LLM with an empty enum
    valid_req_actions = [a for a in req.legalActions if a in VALID_ACTIONS]
    if not valid_req_actions:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "stateVersion": req.stateVersion,
            "action": "HOLD_POSITION",
            "confidence": 0.0,
            "reason": "Empty or invalid legalActions list provided; defaulting to deterministic fallback HOLD_POSITION.",
            "toolsCalled": [],
            "latencyMs": latency_ms,
        }

    async with decide_lock:
        try:
            # Write the incoming state snapshot to state.json for the MCP server to read
            payload_dict = req.model_dump()
            payload_dict["npc"]["stateVersion"] = req.stateVersion
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload_dict, f, indent=2)

            decision, tools_called, legal = await run_decision_loop()
            action = decision.get("action")
            if action not in legal:
                action = fallback_action(req)
            confidence = float(decision.get("confidence", 0.5))
            reason = decision.get("reason", "No explanation provided.")
        except Exception as e:
            root_err = unwrap_exception(e)
            log.error("Decision failed: %s\n%s", root_err, traceback.format_exc())
            tools_called = []
            action = fallback_action(req)
            confidence = 0.0
            reason = f"Agent error ({type(root_err).__name__}: {root_err}). Server-side fallback."

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    return {
        "stateVersion": req.stateVersion,
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "toolsCalled": tools_called,
        "latencyMs": latency_ms
    }


@app.get("/warmup")
async def warmup():
    """Cold-start prevention endpoint that preloads the local LLM into RAM/VRAM."""
    try:
        await llm.chat(model=MODEL, messages=[{"role": "user", "content": "ping"}], keep_alive="30m")
        return {"status": "warmed_up", "model": MODEL}
    except Exception as e:
        root_err = unwrap_exception(e)
        log.warning("Warmup failed: %s", root_err)
        return {"status": "offline", "model": MODEL, "error": f"{type(root_err).__name__}: {root_err}"}


@app.get("/")
async def root():
    return {"status": "ok", "model": MODEL, "endpoints": ["/warmup", "/decide"]}
