# CSE-377 Game Programming: State-Aware AI NPC with MCP and a Local LLM

A tactical Guard NPC demo built with **Unity 6 (C#)**, **Python FastAPI**, **Model Context Protocol (MCP / FastMCP)**, and a local LLM via **Ollama (`llama3.1:8b`)**, including **Multiple NPC Personalities (Section 16)**, **Multi-Agent Cooperation (Section 17)**, and an **Event-Driven Damage System**.

## Architecture Pipeline

```text
Game State (Unity) -> Legal Actions (Unity) -> MCP Tools (npc_server.py) -> AI Decision (Ollama) -> Validation (Unity) -> NPC Action Executor (Unity)
```

- **Unity (`UnityClient`)** owns game state, physics, combat, damage system (`TakeDamage`), personality roles (`GUARD`, `SCOUT`, `ASSAULT`), multi-agent cooperation events (`OnCallForHelpTriggered`), legal-action filtering, stale-state validation (`stateVersion`), deterministic fallback, HUD rendering, and per-frame movement execution.
- **MCP Server (`PythonAgent/npc_server.py`)** exposes small read-only tools (`get_npc_state`, `get_legal_actions`, `get_environment_context`, `get_cooperation_context`) over `stdio`.
- **Decision Agent (`PythonAgent/agent.py`)** hosts `POST /decide` and `GET /warmup`, runs a bounded MCP tool-calling loop (max 4 iterations), and constrains the final output with a JSON Schema whose `action` `enum` is dynamically restricted to the current `legalActions`.

## Prerequisites & Setup

1. **Install and start Ollama**, then pull the local model:
   ```bash
   ollama pull llama3.1:8b
   ```
   *(Optional alternative model: `ollama pull qwen2.5:7b`)*

2. **Install Python dependencies** (Python 3.10+):
   ```bash
   pip install mcp ollama fastapi uvicorn pydantic
   # or:
   pip install -r PythonAgent/requirements.txt
   ```

3. **Start the FastAPI Decision Server**:
   ```bash
   cd PythonAgent
   uvicorn agent:app --host 127.0.0.1 --port 8000
   ```

4. **Run the Unity Scene**:
   - Open `UnityClient` in **Unity 6** (`6000.x`).
   - Open `Assets/Scenes/SampleScene.unity` and press **Play**.
   - Keyboard shortcuts in Play Mode (`UnityEngine.InputSystem`):
     - `1`: Scenario A – Strong Position (`health=90, ammo=10, enemies=1, dist=5, allies=1, cover=true`)
     - `2`: Scenario B – Critical Health (`health=15, ammo=7 [brief], enemies=2, dist=4, allies=0, cover=true`)
     - `6` / `B`: Scenario B (Infographic variant, `ammo=8`)
     - `3`: Scenario C – No Ammo (`health=70, ammo=0, enemies=1, dist=7, allies=2, cover=true`)
     - `4`: Scenario D – Outnumbered (`health=40, ammo=6, enemies=4, dist=8, allies=0, cover=false`)
     - `5`: Scenario E – Supported Defense (`health=55, ammo=4, enemies=2, dist=12, allies=3, cover=true`)
     - `H`: Deal `20` damage to the active NPC (`TakeDamage(20f)`)
     - `J`: Manually broadcast `OnCallForHelpTriggered` to test Section 17 (`allyNeedsHelp = true`)
     - `R`: Cycle NPC Personality Role (`GUARD -> SCOUT -> ASSAULT`, Section 16)
     - `C`: Toggle `coverAvailable` (tests the event-driven "cover changed" trigger)
     - `F1`: Toggle on-screen Debug HUD

