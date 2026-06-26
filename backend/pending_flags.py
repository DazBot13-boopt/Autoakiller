"""Système de flags en attente — quand un solver est peu sûr de son flag,
il le met en attente plutôt que de soumettre directement.

Les flags pending sont sauvegardés dans pending_flags.json et affichés
en rouge dans la console pour que l'opérateur puisse valider manuellement.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logger = logging.getLogger(__name__)
console = Console()

PENDING_FILE = Path("pending_flags.json")


@dataclass
class PendingFlag:
    challenge_name: str
    flag: str
    model_spec: str
    confidence: str          # "high" | "medium" | "low"
    reason: str              # pourquoi il n'a pas soumis directement
    wrong_attempts: int      # nombre de soumissions incorrectes déjà faites
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    submitted: bool = False
    result: str = ""


def load_pending() -> list[PendingFlag]:
    if not PENDING_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_FILE.read_text())
        return [PendingFlag(**p) for p in data]
    except Exception:
        return []


def save_pending(flags: list[PendingFlag]) -> None:
    PENDING_FILE.write_text(
        json.dumps([f.__dict__ for f in flags], indent=2, ensure_ascii=False)
    )


def add_pending_flag(
    challenge_name: str,
    flag: str,
    model_spec: str,
    wrong_attempts: int,
    confidence: str = "medium",
    reason: str = "",
) -> None:
    """Ajoute un flag en attente et l'affiche en rouge dans la console."""
    flags = load_pending()

    # Éviter les doublons
    for f in flags:
        if f.challenge_name == challenge_name and f.flag == flag:
            return

    pending = PendingFlag(
        challenge_name=challenge_name,
        flag=flag,
        model_spec=model_spec,
        confidence=confidence,
        reason=reason or f"Trop de soumissions incorrectes ({wrong_attempts})",
        wrong_attempts=wrong_attempts,
    )
    flags.append(pending)
    save_pending(flags)

    # Affichage console en rouge
    _display_pending_flag(pending)
    logger.warning(f"[PendingFlag] {challenge_name}: {flag} (confidence={confidence})")


def _display_pending_flag(flag: PendingFlag) -> None:
    """Affiche un flag pending en rouge dans la console."""
    confidence_color = {
        "high": "green",
        "medium": "yellow",
        "low": "red",
    }.get(flag.confidence, "yellow")

    text = Text()
    text.append("🚨 FLAG EN ATTENTE — VALIDATION REQUISE\n", style="bold red")
    text.append(f"Challenge : ", style="bold")
    text.append(f"{flag.challenge_name}\n", style="cyan")
    text.append(f"Flag      : ", style="bold")
    text.append(f"{flag.flag}\n", style="bold yellow")
    text.append(f"Modèle    : {flag.model_spec}\n", style="dim")
    text.append(f"Confiance : ", style="bold")
    text.append(f"{flag.confidence.upper()}\n", style=confidence_color)
    text.append(f"Raison    : {flag.reason}\n", style="dim")
    text.append(f"\nPour soumettre manuellement:\n", style="bold")
    text.append(f"  uv run ctf-submit \"{flag.challenge_name}\" \"{flag.flag}\"", style="green")

    console.print(Panel(text, border_style="red", title="⚠️  FLAG PENDING"))


def show_all_pending() -> None:
    """Affiche tous les flags en attente."""
    flags = [f for f in load_pending() if not f.submitted]
    if not flags:
        console.print("[green]Aucun flag en attente.[/green]")
        return

    console.print(f"\n[bold red]{'='*50}[/bold red]")
    console.print(f"[bold red]{len(flags)} FLAG(S) EN ATTENTE DE VALIDATION[/bold red]")
    console.print(f"[bold red]{'='*50}[/bold red]\n")

    for f in flags:
        _display_pending_flag(f)


def mark_submitted(challenge_name: str, flag: str, result: str) -> None:
    """Marque un flag pending comme soumis."""
    flags = load_pending()
    for f in flags:
        if f.challenge_name == challenge_name and f.flag == flag:
            f.submitted = True
            f.result = result
    save_pending(flags)
