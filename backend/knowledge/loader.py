"""Knowledge loader — charge les techniques et writeups selon la categorie du challenge."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent

# Mapping categorie CTF → dossiers de knowledge
CATEGORY_MAP: dict[str, list[str]] = {
    # Web
    "web":        ["web", "writeups/web.md"],
    "web exploitation": ["web", "writeups/web.md"],
    "webapp":     ["web", "writeups/web.md"],

    # Crypto
    "crypto":     ["crypto", "writeups/crypto.md"],
    "cryptography": ["crypto", "writeups/crypto.md"],

    # Pwn / Binary exploitation
    "pwn":        ["pwn", "writeups/pwn.md"],
    "binary":     ["pwn", "writeups/pwn.md"],
    "exploit":    ["pwn", "writeups/pwn.md"],
    "binary exploitation": ["pwn", "writeups/pwn.md"],

    # Reverse
    "rev":        ["reverse", "writeups/reverse.md"],
    "reverse":    ["reverse", "writeups/reverse.md"],
    "reversing":  ["reverse", "writeups/reverse.md"],
    "re":         ["reverse", "writeups/reverse.md"],

    # Forensics
    "forensics":  ["forensics", "writeups/forensics.md"],
    "forensic":   ["forensics", "writeups/forensics.md"],
    "for":        ["forensics", "writeups/forensics.md"],

    # Stego
    "stego":      ["forensics", "writeups/forensics.md"],
    "steganography": ["forensics", "writeups/forensics.md"],

    # OSINT
    "osint":      ["osint"],
    "recon":      ["osint"],

    # Misc
    "misc":       ["misc"],
    "miscellaneous": ["misc"],
    "general":    ["misc"],

    # Malware
    "malware":    ["malware"],
    "malware analysis": ["malware"],

    # AI/ML
    "ai":         ["ai-ml"],
    "ml":         ["ai-ml"],
    "ai/ml":      ["ai-ml"],
}

# Taille max du contexte injecte (en caracteres)
MAX_KNOWLEDGE_CHARS = 8_000


def _load_skill_md(folder: str) -> str:
    """Charge le SKILL.md principal d'une categorie."""
    path = KNOWLEDGE_DIR / folder / "SKILL.md"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _load_file(rel_path: str) -> str:
    """Charge un fichier relatif au dossier knowledge."""
    path = KNOWLEDGE_DIR / rel_path
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def get_knowledge_for_category(category: str, max_chars: int = MAX_KNOWLEDGE_CHARS) -> str:
    """Retourne le contexte de knowledge pour une categorie CTF.

    Charge : SKILL.md de la categorie + writeups correspondants.
    Tronque si trop long pour ne pas saturer le contexte.
    """
    cat_lower = (category or "").lower().strip()

    # Trouver les sources de knowledge
    sources = CATEGORY_MAP.get(cat_lower, [])

    # Fallback : chercher une correspondance partielle
    if not sources:
        for key in CATEGORY_MAP:
            if key in cat_lower or cat_lower in key:
                sources = CATEGORY_MAP[key]
                break

    # Fallback final : misc
    if not sources:
        sources = ["misc"]

    parts: list[str] = []
    total = 0

    for source in sources:
        if source.endswith(".md"):
            # Fichier direct (writeup)
            content = _load_file(source)
            label = f"## Writeups — {source.split('/')[-1].replace('.md','').upper()}\n\n"
        else:
            # Dossier → charger SKILL.md
            content = _load_skill_md(source)
            label = f"## Techniques — {source.upper()}\n\n"

        if not content:
            continue

        # Tronquer si necessaire
        remaining = max_chars - total
        if remaining <= 200:
            break

        chunk = content[:remaining]
        parts.append(label + chunk)
        total += len(chunk)

    if not parts:
        return ""

    header = "# Knowledge Base CTF\n\n"
    return header + "\n\n---\n\n".join(parts)


def inject_knowledge_into_prompt(base_prompt: str, category: str) -> str:
    """Injecte le knowledge CTF dans le system prompt du solver."""
    knowledge = get_knowledge_for_category(category)
    if not knowledge:
        return base_prompt

    injection = (
        "\n\n---\n"
        "# Base de Connaissances CTF\n"
        "Utilise ces techniques et writeups comme reference pour resoudre ce challenge :\n\n"
        + knowledge
        + "\n---\n"
    )

    # Inserer apres le premier bloc (avant les instructions specifiques au challenge)
    # pour ne pas noyer le contexte du challenge
    lines = base_prompt.split("\n")
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## Challenge"):
            insert_at = i
            break

    if insert_at > 0:
        lines.insert(insert_at, injection)
        return "\n".join(lines)

    return base_prompt + injection
