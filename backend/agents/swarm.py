"""ChallengeSolver — un modèle, un challenge.

Nouveau mode de distribution : chaque modèle disponible prend un challenge
différent. Pas de compétition entre modèles sur le même challenge.

L'ancien ChallengeSwarm (N modèles → 1 challenge) est remplacé par
ChallengeSolver (1 modèle → 1 challenge). Le coordinator distribue les
challenges en round-robin sur les modèles disponibles.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.agents.solver import Solver
from backend.cost_tracker import CostTracker
from backend.models import DEFAULT_MODELS, provider_from_spec
from backend.prompts import ChallengeMeta
from backend.solver_base import (
    CANCELLED,
    ERROR,
    FLAG_FOUND,
    GAVE_UP,
    QUOTA_ERROR,
    SolverProtocol,
    SolverResult,
)

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ChallengeSolver:
    """Un solver unique : un modèle, un challenge.

    Remplace l'ancien ChallengeSwarm multi-modèles.
    Si le solver échoue il relance avec bump jusqu'à trouver le flag.
    """

    challenge_dir: str
    meta: ChallengeMeta
    ctfd: Any                          # PlatformClient
    cost_tracker: CostTracker
    settings: "Settings"
    model_spec: str                    # Le modèle assigné à ce challenge
    no_submit: bool = False
    coordinator_inbox: asyncio.Queue | None = None
    max_bumps: int = 10                # Nombre max de bumps avant abandon (0 = infini)

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    solver: SolverProtocol | None = field(default=None, repr=False)
    winner: SolverResult | None = None
    confirmed_flag: str | None = None

    _flag_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _submit_count: int = 0
    _submitted_flags: set[str] = field(default_factory=set)
    _last_submit_time: float = 0.0

    # Escalating cooldowns after incorrect submissions
    SUBMISSION_COOLDOWNS = [0, 30, 120, 300, 600]

    def _make_notify_fn(self):
        async def _notify(message: str) -> None:
            if self.coordinator_inbox:
                self.coordinator_inbox.put_nowait(
                    f"[{self.meta.name}/{self.model_spec}] {message}"
                )
        return _notify

    def _create_solver(self) -> SolverProtocol:
        provider = provider_from_spec(self.model_spec)
        submit_fn = lambda flag: self.try_submit_flag(flag)
        notify_fn = self._make_notify_fn()

        if provider == "claude-sdk":
            from backend.agents.claude_solver import ClaudeSolver
            return ClaudeSolver(
                model_spec=self.model_spec,
                challenge_dir=self.challenge_dir,
                meta=self.meta,
                ctfd=self.ctfd,
                cost_tracker=self.cost_tracker,
                settings=self.settings,
                cancel_event=self.cancel_event,
                no_submit=self.no_submit,
                submit_fn=submit_fn,
                notify_coordinator=notify_fn,
            )

        if provider == "codex":
            from backend.agents.codex_solver import CodexSolver
            return CodexSolver(
                model_spec=self.model_spec,
                challenge_dir=self.challenge_dir,
                meta=self.meta,
                ctfd=self.ctfd,
                cost_tracker=self.cost_tracker,
                settings=self.settings,
                cancel_event=self.cancel_event,
                no_submit=self.no_submit,
                submit_fn=submit_fn,
                notify_coordinator=notify_fn,
            )

        # API-backed fallback (bedrock, azure, google, zen)
        solver = Solver(
            model_spec=self.model_spec,
            challenge_dir=self.challenge_dir,
            meta=self.meta,
            ctfd=self.ctfd,
            cost_tracker=self.cost_tracker,
            settings=self.settings,
            cancel_event=self.cancel_event,
        )
        solver.deps.no_submit = self.no_submit
        solver.deps.submit_fn = submit_fn
        solver.deps.notify_coordinator = notify_fn
        return solver

    async def try_submit_flag(self, flag: str) -> tuple[str, bool]:
        """Soumission dédupliquée avec cooldown. Retourne (display, is_confirmed)."""
        async with self._flag_lock:
            if self.confirmed_flag:
                return f"ALREADY SOLVED — flag already confirmed: {self.confirmed_flag}", True

            normalized = flag.strip()
            if normalized in self._submitted_flags:
                return "INCORRECT — already tried this exact flag.", False

            # Cooldown
            cooldown_idx = min(self._submit_count, len(self.SUBMISSION_COOLDOWNS) - 1)
            cooldown = self.SUBMISSION_COOLDOWNS[cooldown_idx]
            if cooldown > 0:
                elapsed = time.monotonic() - self._last_submit_time
                if elapsed < cooldown:
                    remaining = int(cooldown - elapsed)
                    return (
                        f"COOLDOWN — wait {remaining}s before submitting again. "
                        f"{self._submit_count} incorrect submissions so far.",
                        False,
                    )

            # Si trop de soumissions incorrectes → mettre en pending pour validation manuelle
            if self._submit_count >= 3:
                try:
                    from backend.pending_flags import add_pending_flag
                    add_pending_flag(
                        challenge_name=self.meta.name,
                        flag=normalized,
                        model_spec=self.model_spec,
                        wrong_attempts=self._submit_count,
                        confidence="medium",
                        reason=f"{self._submit_count} soumissions incorrectes — validation manuelle requise",
                    )
                except Exception:
                    pass
                return (
                    f"FLAG MIS EN ATTENTE — {self._submit_count} soumissions incorrectes. "
                    f"Flag '{normalized}' sauvegardé dans pending_flags.json pour validation manuelle.",
                    False,
                )

            self._submitted_flags.add(normalized)

            from backend.tools.core import do_submit_flag
            display, is_confirmed = await do_submit_flag(self.ctfd, self.meta.name, flag)
            if is_confirmed:
                self.confirmed_flag = normalized
            else:
                self._submit_count += 1
                self._last_submit_time = time.monotonic()
            return display, is_confirmed

    async def run(self) -> SolverResult | None:
        """Lance le solver et recommence avec bump jusqu'à trouver le flag."""
        self.solver = self._create_solver()
        bump_count = 0
        consecutive_errors = 0

        try:
            await self.solver.start()

            while not self.cancel_event.is_set():
                result = await self.solver.run_until_done_or_gave_up()

                if result.status == FLAG_FOUND:
                    self.winner = result
                    self.cancel_event.set()
                    logger.info(f"[{self.meta.name}] Flag trouvé par {self.model_spec}: {result.flag}")

                    # Auto-sauvegarde du writeup dans la knowledge base
                    try:
                        from backend.knowledge.writeup_writer import save_writeup
                        save_writeup(
                            challenge_name=self.meta.name,
                            category=self.meta.category,
                            flag=result.flag or "",
                            findings_summary=result.findings_summary,
                            model_spec=self.model_spec,
                            step_count=result.step_count,
                            solver_trace_path=result.log_path,
                        )
                    except Exception as e:
                        logger.debug(f"[Knowledge] Writeup save failed (non-fatal): {e}")

                    return result

                if result.status == CANCELLED:
                    return result

                if result.status == QUOTA_ERROR:
                    logger.warning(f"[{self.meta.name}/{self.model_spec}] Quota épuisé — abandon")
                    return result

                if result.status == ERROR:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        logger.warning(f"[{self.meta.name}/{self.model_spec}] 3 erreurs consécutives — abandon")
                        return result
                else:
                    consecutive_errors = 0

                # Solver gave up — bump et on réessaie
                if result.step_count == 0 and result.cost_usd == 0:
                    logger.warning(f"[{self.meta.name}/{self.model_spec}] Solver cassé (0 steps) — abandon")
                    return result

                bump_count += 1
                # Vérifier la limite de bumps
                if self.max_bumps > 0 and bump_count >= self.max_bumps:
                    logger.warning(f"[{self.meta.name}/{self.model_spec}] Limite de bumps atteinte ({self.max_bumps}) — abandon")
                    return result

                wait = 5  # attendre seulement 5s entre les bumps
                logger.info(f"[{self.meta.name}/{self.model_spec}] Bump #{bump_count}, reprise dans {wait}s")

                try:
                    await asyncio.wait_for(self.cancel_event.wait(), timeout=wait)
                    return result  # annulé pendant l'attente
                except TimeoutError:
                    pass

                self.solver.bump(
                    f"Tentative #{bump_count} échouée. Essaie une approche complètement différente.\n"
                    f"Ce qui a déjà été tenté : {result.findings_summary[:300]}"
                )

            return self.winner

        except asyncio.CancelledError:
            return None
        except Exception as e:
            logger.error(f"[{self.meta.name}/{self.model_spec}] Erreur fatale: {e}", exc_info=True)
            return None
        finally:
            if self.solver:
                await self.solver.stop()

    def kill(self) -> None:
        self.cancel_event.set()

    def get_status(self) -> dict:
        return {
            "challenge": self.meta.name,
            "model": self.model_spec,
            "cancelled": self.cancel_event.is_set(),
            "flag": self.confirmed_flag,
        }


# ── Compat : ChallengeSwarm pointe maintenant sur ChallengeSolver ────────────
# Garde l'ancien nom pour ne pas casser les imports existants.
# En mode nouveau (1 modèle / 1 challenge), model_specs ne contient qu'un seul spec.
@dataclass
class ChallengeSwarm:
    """Wrapper de compatibilité — délègue à ChallengeSolver (1 modèle par challenge)."""

    challenge_dir: str
    meta: ChallengeMeta
    ctfd: Any
    cost_tracker: CostTracker
    settings: "Settings"
    model_specs: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    no_submit: bool = False
    coordinator_inbox: asyncio.Queue | None = None
    max_bumps: int = 10

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    solvers: dict[str, SolverProtocol] = field(default_factory=dict)
    winner: SolverResult | None = None
    confirmed_flag: str | None = None
    _active_solver: ChallengeSolver | None = field(default=None, repr=False)

    def __post_init__(self):
        # En mode nouveau : on prend le premier modèle de la liste
        # Le coordinator est responsable de donner le bon modèle
        self._model_spec = self.model_specs[0] if self.model_specs else DEFAULT_MODELS[0]

    async def run(self) -> SolverResult | None:
        self._active_solver = ChallengeSolver(
            challenge_dir=self.challenge_dir,
            meta=self.meta,
            ctfd=self.ctfd,
            cost_tracker=self.cost_tracker,
            settings=self.settings,
            model_spec=self._model_spec,
            no_submit=self.no_submit,
            coordinator_inbox=self.coordinator_inbox,
            cancel_event=self.cancel_event,
            max_bumps=self.max_bumps,
        )
        result = await self._active_solver.run()
        if result and result.status == FLAG_FOUND:
            self.winner = result
            self.confirmed_flag = result.flag
        return result

    def kill(self) -> None:
        self.cancel_event.set()
        if self._active_solver:
            self._active_solver.kill()

    def get_status(self) -> dict:
        if self._active_solver:
            return self._active_solver.get_status()
        return {
            "challenge": self.meta.name,
            "model": self._model_spec,
            "cancelled": self.cancel_event.is_set(),
            "flag": self.confirmed_flag,
        }
