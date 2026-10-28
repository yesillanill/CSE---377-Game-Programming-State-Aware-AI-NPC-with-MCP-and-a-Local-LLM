# npc_server.py
import json
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer  # type: ignore  # mcp >= 2
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore  # mcp < 2

STATE_FILE = Path(__file__).resolve().parent / "state.json"

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
    """Returns the current NPC state including npcId, health, ammo, enemyDistance, enemyCount, nearbyAllies, coverAvailable, and currentAction."""
    state = load_state()
    if "npc" not in state or not isinstance(state["npc"], dict):
        raise ValueError("Missing or invalid 'npc' section in state.json.")
    return state["npc"]


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
    """Returns tactical environment context including nearest_cover_distance, objective_under_threat, cover_available, and nearby_allies."""
    state = load_state()
    env = dict(state.get("environment") or {})
    npc = state.get("npc") or {}
    if isinstance(npc, dict):
        env.setdefault("cover_available", npc.get("coverAvailable", False))
        env.setdefault("nearby_allies", npc.get("nearbyAllies", 0))
    return env


if __name__ == "__main__":
    mcp.run()  # stdio transport