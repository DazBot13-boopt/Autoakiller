"""Claude Agent SDK coordinator — uses the shared event loop with a Claude SDK client."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    create_sdk_mcp_server,
    tool,
)

from backend.agents.coordinator_core import (
    do_broadcast,
    do_bump_agent,
    do_check_swarm_status,
    do_fetch_challenges,
    do_get_solve_status,
    do_kill_swarm,
    do_read_solver_trace,
    do_spawn_swarm,
    do_submit_flag,
)
from backend.agents.coordinator_loop import build_deps, run_event_loop
from backend.config import Settings
from backend.deps import CoordinatorDeps

logger = logging.getLogger(__name__)

COORDINATOR_PROMPT = """\
Tu es le coordinateur d'une compétition CTF en cours. Ton objectif : maximiser le nombre de challenges résolus.

Stratégie :
- Chaque challenge est assigné à UN seul modèle (round-robin automatique)
- Les modèles travaillent en PARALLÈLE sur des challenges DIFFÉRENTS — pas de compétition entre eux
- Spawn un solver par challenge non résolu dès le départ
- Surveille les solvers bloqués via read_solver_trace et envoie des bumps ciblés
- Quand un solver résout un challenge, il s'arrête automatiquement

RÈGLES :
- NE JAMAIS kill un swarm sauf si le challenge est déjà résolu
- Quand un solver est bloqué, lit sa trace et lui envoie des instructions techniques précises
- Dès qu'un nouveau challenge apparaît, spawn immédiatement un solver dessus
- Le coût n'est pas un problème — garde tous les solvers actifs

Tu recevras des messages d'événements. Réponds avec des appels d'outils.
"""


def _text(s: str) -> dict:
    """Wrap a string in the Claude SDK MCP tool return format."""
    return {"content": [{"type": "text", "text": s}]}


def _build_coordinator_mcp(deps: CoordinatorDeps):
    """Build MCP server — thin wrappers around coordinator_core functions."""

    @tool("fetch_challenges", "List all challenges with category, points, solve count, and status.", {})
    async def fetch_challenges(args: dict) -> dict:
        return _text(await do_fetch_challenges(deps))

    @tool("get_solve_status", "Check which challenges are solved and which swarms are running.", {})
    async def get_solve_status(args: dict) -> dict:
        return _text(await do_get_solve_status(deps))

    @tool("spawn_swarm", "Launch all solver models on a challenge.", {"challenge_name": str})
    async def spawn_swarm(args: dict) -> dict:
        return _text(await do_spawn_swarm(deps, args["challenge_name"]))

    @tool("check_swarm_status", "Get per-agent progress for a swarm.", {"challenge_name": str})
    async def check_swarm_status(args: dict) -> dict:
        return _text(await do_check_swarm_status(deps, args["challenge_name"]))

    @tool("submit_flag", "Submit a flag to CTFd.", {"challenge_name": str, "flag": str})
    async def submit_flag(args: dict) -> dict:
        return _text(await do_submit_flag(deps, args["challenge_name"], args["flag"]))

    @tool("kill_swarm", "Cancel all agents for a challenge.", {"challenge_name": str})
    async def kill_swarm(args: dict) -> dict:
        return _text(await do_kill_swarm(deps, args["challenge_name"]))

    @tool("bump_agent", "Send targeted insights to a stuck agent.", {"challenge_name": str, "model_spec": str, "insights": str})
    async def bump_agent(args: dict) -> dict:
        return _text(await do_bump_agent(deps, args["challenge_name"], args["model_spec"], args["insights"]))

    @tool("broadcast", "Broadcast a strategic hint to ALL solvers on a challenge.", {"challenge_name": str, "message": str})
    async def broadcast(args: dict) -> dict:
        return _text(await do_broadcast(deps, args["challenge_name"], args["message"]))

    @tool("read_solver_trace", "Read recent trace events from a specific solver. Use this to understand what a solver is doing, what it tried, and where it's stuck.", {"challenge_name": str, "model_spec": str, "last_n": int})
    async def read_solver_trace(args: dict) -> dict:
        return _text(await do_read_solver_trace(deps, args["challenge_name"], args["model_spec"], args.get("last_n", 20)))

    return create_sdk_mcp_server(
        name="coordinator", version="1.0.0",
        tools=[fetch_challenges, get_solve_status, spawn_swarm, check_swarm_status,
               submit_flag, kill_swarm, bump_agent, broadcast, read_solver_trace],
    )


async def run_claude_coordinator(
    settings: Settings,
    model_specs: list[str] | None = None,
    challenges_root: str = "challenges",
    no_submit: bool = False,
    coordinator_model: str | None = None,
    msg_port: int = 0,
    ctfd=None,
    only_challenges: list[str] | None = None,
    max_bumps: int = 10,
    local_mode: bool = False,
) -> dict[str, Any]:
    """Run the Claude Agent SDK coordinator with the shared event loop."""
    ctfd, cost_tracker, deps = build_deps(
        settings, model_specs, challenges_root, no_submit, ctfd=ctfd,
        only_challenges=only_challenges, max_bumps=max_bumps, local_mode=local_mode,
    )
    deps.msg_port = msg_port

    mcp_server = _build_coordinator_mcp(deps)
    resolved_model = coordinator_model or "claude-opus-4-6"

    allowed = {
        "mcp__coordinator__fetch_challenges", "mcp__coordinator__get_solve_status",
        "mcp__coordinator__spawn_swarm", "mcp__coordinator__check_swarm_status",
        "mcp__coordinator__submit_flag", "mcp__coordinator__kill_swarm",
        "mcp__coordinator__bump_agent", "mcp__coordinator__broadcast",
        "mcp__coordinator__read_solver_trace",
        "ToolSearch",
        "TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskOutput", "TaskStop",
    }

    async def enforce_allowlist(input_data, tool_use_id, context):
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        tool = input_data.get("tool_name", "")
        if tool in allowed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{tool} not available to coordinator.",
            }
        }

    options = ClaudeAgentOptions(
        model=resolved_model,
        system_prompt=COORDINATOR_PROMPT,
        env={"CLAUDECODE": ""},
        mcp_servers={"coordinator": mcp_server},
        allowed_tools=list(allowed),
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [HookMatcher(hooks=[enforce_allowlist])],
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        async def turn_fn(msg: str) -> None:
            logger.debug(f"Coordinator query: {msg[:200]}")
            await client.query(msg)
            msg_count = 0
            async for message in client.receive_response():
                msg_count += 1
                msg_type = type(message).__name__
                logger.debug(f"Coordinator received: {msg_type}")
                if isinstance(message, ResultMessage):
                    cost = getattr(message, "total_cost_usd", 0)
                    session = getattr(message, "session_id", None)
                    logger.info(f"Claude coordinator turn done (messages={msg_count}, cost=${cost:.4f}, session={session})")
            if msg_count == 0:
                logger.warning("Coordinator turn produced no messages!")

        return await run_event_loop(deps, ctfd, cost_tracker, turn_fn)
