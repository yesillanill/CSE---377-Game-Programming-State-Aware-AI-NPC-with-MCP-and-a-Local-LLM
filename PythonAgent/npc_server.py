# npc_server.py
import os
import json
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer  # type: ignore  # mcp >= 2
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore  # mcp < 2

STATE_FILE = Path(os.environ.get("NPC_STATE_FILE", str(Path(__file__).resolve().parent / "state.json")))

ROLE_GOALS = {
    "GUARD": "Protect the gate and defensive perimeter; use tactical cover when threatened.",
    "SCOUT": "Observe enemies and avoid unnecessary direct combat; prefer RETREAT or TAKE_COVER when health drops or enemies are close.",
    "ASSAULT": "Apply aggressive combat pressure while remaining combat-effective; prioritize ATTACK when armed.",
}

mcp = MCPServer("npc-decision")


def load_state() -> dict:
    """Loads and validates the current tactical state snapshot from state.json."""
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"State file not found at {STATE_FILE}")
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("State file root must be a JSON object.")
    return data


@mcp.tool()
def get_npc_state() -> dict:
    """Returns the current NPC state including npcId, role, tacticalGoal, health, ammo, enemyDistance, enemyCount, nearbyAllies, coverAvailable, allyNeedsHelp, and currentAction."""
    state = load_state()
    if "npc" not in state or not isinstance(state["npc"], dict):
        raise ValueError("Missing or invalid 'npc' section in state.json.")
    npc = dict(state["npc"])
    role = str(npc.get("role", "GUARD")).upper()
    npc["role"] = role
    npc["tacticalGoal"] = ROLE_GOALS.get(role, ROLE_GOALS["GUARD"])
    npc.setdefault("allyNeedsHelp", False)
    return npc


@mcp.tool()
def get_legal_actions() -> list[str]:
    """Returns the list of tactical actions allowed right now based on current game rules."""
    state = load_state()
    legal = state.get("legalActions")
    if not isinstance(legal, list):
        raise ValueError("Missing or invalid 'legalActions' list in state.json.")
    return [str(a) for a in legal]


@mcp.tool()
def get_environment_context() -> dict:
    """Returns tactical environment and cooperation context including nearest_cover_distance, objective_under_threat, cover_available, nearby_allies, role, and ally_needs_help."""
    state = load_state()
    env = dict(state.get("environment") or {})
    npc = state.get("npc") or {}
    if isinstance(npc, dict):
        role = str(npc.get("role", "GUARD")).upper()
        env.setdefault("cover_available", npc.get("coverAvailable", False))
        env.setdefault("nearby_allies", npc.get("nearbyAllies", 0))
        env.setdefault("role", role)
        env.setdefault("tactical_goal", ROLE_GOALS.get(role, ROLE_GOALS["GUARD"]))
        env.setdefault("ally_needs_help", npc.get("allyNeedsHelp", False))
    return env


@mcp.tool()
def get_cooperation_context() -> dict:
    """Returns NPC personality role, tactical goal, and multi-agent cooperation status (allyNeedsHelp and nearbyAllies)."""
    state = load_state()
    npc = state.get("npc") or {}
    role = str(npc.get("role", "GUARD")).upper() if isinstance(npc, dict) else "GUARD"
    return {
        "role": role,
        "tacticalGoal": ROLE_GOALS.get(role, ROLE_GOALS["GUARD"]),
        "allyNeedsHelp": bool(npc.get("allyNeedsHelp", False)) if isinstance(npc, dict) else False,
        "nearbyAllies": int(npc.get("nearbyAllies", 0)) if isinstance(npc, dict) else 0,
    }


if __name__ == "__main__":
    mcp.run()  # stdio transport
