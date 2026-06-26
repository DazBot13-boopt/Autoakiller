"""Local solver — claude et codex travaillent directement sur la machine hôte.

Pas de Docker sandbox. Les commandes bash s'exécutent localement.
Plus rapide, utilise tous les outils installés sur la machine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
)

from backend.cost_tracker import CostTracker
from backend.loop_detect import LoopDetector
from backend.models import model_id_from_spec
from backend.output_types import solver_output_json_schema
from backend.prompts import ChallengeMeta, build_prompt, list_distfiles
from backend.solver_base import CANCELLED, ERROR, FLAG_FOUND, GAVE_UP, QUOTA_ERROR, SolverResult
from backend.tracing import SolverTracer

logger = logging.getLogger(__name__)


class LocalSolver:
    """Solver qui tourne directement sur la machine hôte — pas de Docker.

    Claude/Codex exécutent leurs commandes bash directement avec les outils
    installés localement. Plus rapide, pas besoin de Docker.
    """

    def __init__(
        self,
        model_spec: str,
        challenge_dir: str,
        meta: ChallengeMeta,
        ctfd,
        cost_tracker: CostTracker,
        settings: object,
        cancel_event: asyncio.Event | None = None,
        no_submit: bool = False,
        submit_fn=None,
        message_bus=None,
        notify_coordinator=None,
    ) -> None:
        self.model_spec = model_spec
        self.model_id = model_id_from_spec(model_spec)
        self.challenge_dir = challenge_dir
        self.meta = meta
        self.ctfd = ctfd
        self.cost_tracker = cost_tracker
        self.settings = settings
        self.cancel_event = cancel_event or asyncio.Event()
        self.no_submit = no_submit
        self.submit_fn = submit_fn
        self.message_bus = message_bus
        self.notify_coordinator = notify_coordinator

        # Workspace local pour le challenge
        self.workspace_dir = str(Path(challenge_dir) / "workspace")
        Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)

        self.loop_detector = LoopDetector()
        self.tracer = SolverTracer(meta.name, self.model_id)
        self.agent_name = f"{meta.name}/{self.model_id}"
        # Pas de sandbox Docker
        self.sandbox = None

        self._client: ClaudeSDKClient | None = None
        self._session_id: str | None = None
        self._step_count = 0
        self._flag: str | None = None
        self._confirmed = False
        self._findings = ""
        self._cost_usd = 0.0
        self._bump_insights: str | None = None

    async def _exec_local(self, command: str, timeout: int = 60) -> str:
        """Exécute une commande bash localement."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
                env={**os.environ, "CHALLENGE_DIR": self.challenge_dir},
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                return f"[timeout after {timeout}s]"

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr]\n{err}")
            if proc.returncode != 0:
                parts.append(f"[exit {proc.returncode}]")
            return "\n".join(parts).strip() or "(no output)"
        except Exception as e:
            return f"Error: {e}"

    async def start(self) -> None:
        distfile_names = list_distfiles(self.challenge_dir)

        # Prompt adapté au mode local
        local_preamble = (
            "IMPORTANT: Tu travailles DIRECTEMENT sur la machine hôte (pas dans un container).\n"
            f"Les fichiers du challenge sont dans : {self.challenge_dir}/distfiles/\n"
            f"Ton workspace est : {self.workspace_dir}/\n"
            "Tous tes outils locaux sont disponibles : python3, gdb, radare2, pwntools, z3, etc.\n"
            "submit_flag 'FLAG' pour soumettre. notify_coordinator 'MSG' pour le coordinator.\n\n"
        )

        system_prompt = local_preamble + build_prompt(
            self.meta, distfile_names,
            container_arch="local",
            has_named_tools=False,
            inject_knowledge=True,
        )

        async def local_hook(input_data, tool_use_id, context):
            """Hook PreToolUse — exécute bash localement au lieu de Docker."""
            try:
                return await _local_hook_inner(input_data, tool_use_id, context)
            except Exception as e:
                logger.warning(f"[{self.agent_name}] Hook error: {e}")
                return {}

        async def _local_hook_inner(input_data, tool_use_id, context):
            if input_data.get("hook_event_name") != "PreToolUse":
                return {}

            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            self._step_count += 1
            self.tracer.tool_call(tool_name, tool_input, self._step_count)

            # Détection de boucle
            loop_status = self.loop_detector.check(tool_name, str(tool_input)[:200])
            if loop_status == "break":
                self.tracer.event("loop_break", tool=tool_name, step=self._step_count)
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Loop détectée — essaie une approche différente.",
                    }
                }

            if tool_name == "Bash":
                command = tool_input.get("command", "")

                # Intercepter submit_flag
                flag_match = re.match(r"submit_flag\s+['\"]?(.+?)['\"]?\s*$", command.strip())
                if flag_match:
                    flag_val = flag_match.group(1).strip()
                    if self.no_submit:
                        result_msg = f'DRY RUN — would submit "{flag_val}"'
                    else:
                        if self.submit_fn:
                            display, confirmed = await self.submit_fn(flag_val)
                        else:
                            from backend.tools.core import do_submit_flag
                            display, confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag_val)
                        result_msg = display
                        if confirmed:
                            self._confirmed = True
                            self._flag = flag_val
                            self.tracer.event("flag_confirmed", flag=flag_val, step=self._step_count)
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {
                                **tool_input,
                                "command": f"echo {shlex.quote(result_msg)}",
                            },
                        }
                    }

                # Intercepter notify_coordinator
                notify_match = re.match(r"notify_coordinator\s+['\"]?(.+?)['\"]?\s*$", command.strip())
                if notify_match and self.notify_coordinator:
                    msg = notify_match.group(1).strip()
                    await self.notify_coordinator(msg)
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {**tool_input, "command": "echo 'Message envoyé au coordinator.'"},
                        }
                    }

                # Exécution locale — ajouter le challenge dir dans le PATH
                local_command = (
                    f"cd {shlex.quote(self.workspace_dir)} && "
                    f"CHALLENGE_DIR={shlex.quote(self.challenge_dir)} "
                    f"DISTFILES={shlex.quote(self.challenge_dir + '/distfiles')} "
                    f"{command}"
                )

                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": {
                            **tool_input,
                            "command": local_command,
                        },
                    }
                }

            # WebFetch/WebSearch — laisser passer
            if tool_name in ("WebFetch", "WebSearch"):
                return {}

            # Bloquer Read/Write/Edit sur le filesystem hôte
            if tool_name in ("Read", "Write", "Edit", "Glob", "Grep"):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"{tool_name} bloqué — utilise bash pour lire/écrire les fichiers.",
                    }
                }

            return {}

        from backend.models import effort_from_spec
        effort = effort_from_spec(self.model_spec)

        options = ClaudeAgentOptions(
            model=self.model_id,
            system_prompt=system_prompt,
            effort=effort,
            env={"CLAUDECODE": ""},
            allowed_tools=["Bash", "WebFetch", "WebSearch"],
            permission_mode="bypassPermissions",
            output_format={"type": "json_schema", "schema": solver_output_json_schema()},
            hooks={
                "PreToolUse": [HookMatcher(hooks=[local_hook])],
            },
        )

        self._client = ClaudeSDKClient(options=options)
        await self._client.__aenter__()
        self.tracer.event("start", challenge=self.meta.name, model=self.model_id, mode="local")
        logger.info(f"[{self.agent_name}] Local solver started (no Docker)")

    async def run_until_done_or_gave_up(self) -> SolverResult:
        if not self._client:
            await self.start()
        assert self._client is not None

        t0 = time.monotonic()
        cost_before = self._cost_usd
        steps_before = self._step_count

        try:
            if self._bump_insights:
                prompt = (
                    "Tentative précédente échouée. "
                    f"Insights:\n\n{self._bump_insights}\n\n"
                    "Essaie une approche différente."
                )
                self._bump_insights = None
            elif self._session_id:
                prompt = "Continue. Essaie une approche différente."
            else:
                prompt = "Résous ce challenge CTF."

            await self._client.query(prompt)

            async for message in self._client.receive_response():
                if self.cancel_event.is_set():
                    break
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            self._findings = block.text[:2000]
                elif isinstance(message, ResultMessage):
                    self._session_id = message.session_id
                    turn_cost = getattr(message, "total_cost_usd", 0.0)
                    self._cost_usd += turn_cost
                    output = getattr(message, "structured_output", None)
                    if output and output.get("type") == "flag_found":
                        self._flag = output.get("flag")
                        self._findings = f"Flag trouvé via {output.get('method', '?')}: {self._flag}"
                        if self.no_submit:
                            self._confirmed = True

            if self._confirmed and self._flag:
                return self._result(FLAG_FOUND)
            run_steps = self._step_count - steps_before
            run_cost = self._cost_usd - cost_before
            return self._result(GAVE_UP, run_steps=run_steps, run_cost=run_cost)

        except asyncio.CancelledError:
            return self._result(CANCELLED)
        except Exception as e:
            error_str = str(e)
            logger.error(f"[{self.agent_name}] Error: {e}", exc_info=True)
            self._findings = f"Error: {e}"
            self.tracer.event("error", error=error_str)
            if "quota" in error_str.lower() or "rate" in error_str.lower():
                return self._result(QUOTA_ERROR)
            return self._result(ERROR)

    def bump(self, insights: str) -> None:
        self._bump_insights = insights
        self.loop_detector.reset()
        self.tracer.event("bump", insights=insights[:500])

    def _result(self, status: str, run_steps: int | None = None, run_cost: float | None = None) -> SolverResult:
        self.tracer.event("finish", status=status, flag=self._flag, confirmed=self._confirmed)
        return SolverResult(
            flag=self._flag, status=status,
            findings_summary=self._findings[:2000],
            step_count=run_steps if run_steps is not None else self._step_count,
            cost_usd=run_cost if run_cost is not None else self._cost_usd,
            log_path=self.tracer.path,
        )

    async def stop(self) -> None:
        self.tracer.event("stop", step_count=self._step_count)
        self.tracer.close()
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
