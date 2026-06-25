"""Auto-writeup writer — quand un agent trouve un flag, il sauvegarde sa methode.

Les writeups sont stockes dans backend/knowledge/writeups/solved/
et injectes automatiquement dans les prochains challenges de meme categorie.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SOLVED_DIR = Path(__file__).parent / "writeups" / "solved"


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-') or "challenge"


def save_writeup(
    challenge_name: str,
    category: str,
    flag: str,
    findings_summary: str,
    model_spec: str,
    step_count: int,
    solver_trace_path: str = "",
) -> str:
    """Sauvegarde un writeup apres resolution. Retourne le chemin du fichier cree."""
    SOLVED_DIR.mkdir(parents=True, exist_ok=True)

    slug = _slugify(challenge_name)
    cat_slug = _slugify(category or "misc")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{cat_slug}_{slug}_{timestamp}.md"
    filepath = SOLVED_DIR / filename

    # Extraire les commandes cles du findings_summary
    key_commands = _extract_key_commands(findings_summary, solver_trace_path)

    content = f"""# Writeup: {challenge_name}

**Categorie**: {category or 'misc'}
**Flag**: `{flag}`
**Modele**: {model_spec}
**Steps**: {step_count}
**Date**: {datetime.now().strftime("%Y-%m-%d %H:%M")}

## Methode de resolution

{findings_summary[:1000] if findings_summary else '_Aucun detail disponible._'}

## Commandes cles utilisees

{key_commands}

## Tags
`{category}` `solved` `{_extract_difficulty(step_count)}`
"""

    filepath.write_text(content, encoding="utf-8")
    logger.info(f"[Knowledge] Writeup sauvegarde: {filepath}")

    # Mettre a jour l'index
    _update_index(challenge_name, category, flag, str(filepath))

    return str(filepath)


def _extract_key_commands(findings: str, trace_path: str) -> str:
    """Extrait les commandes importantes depuis le findings ou la trace."""
    commands = []

    # Extraire les blocs de code du findings
    code_blocks = re.findall(r'```(?:bash|python|sh)?\n(.*?)```', findings, re.DOTALL)
    for block in code_blocks[:3]:  # Max 3 blocs
        commands.append(f"```bash\n{block.strip()}\n```")

    # Si pas de code dans findings, essayer de lire la trace
    if not commands and trace_path:
        try:
            lines = Path(trace_path).read_text().strip().split('\n')
            bash_calls = []
            for line in lines[-50:]:  # derniers 50 events
                try:
                    event = json.loads(line)
                    if event.get('type') == 'tool_call' and event.get('tool') == 'bash':
                        cmd = event.get('args', {})
                        if isinstance(cmd, dict):
                            cmd = cmd.get('command', '')
                        if cmd and len(cmd) < 200:
                            bash_calls.append(f"```bash\n{cmd}\n```")
                except Exception:
                    pass
            commands = bash_calls[:5]  # Max 5 commandes
        except Exception:
            pass

    if not commands:
        return "_Trace non disponible._"

    return "\n\n".join(commands)


def _extract_difficulty(step_count: int) -> str:
    if step_count < 10:
        return "easy"
    elif step_count < 30:
        return "medium"
    else:
        return "hard"


def _update_index(name: str, category: str, flag: str, filepath: str) -> None:
    """Met a jour l'index JSON des challenges resolus."""
    index_path = SOLVED_DIR / "index.json"

    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except Exception:
            pass

    index[name] = {
        "category": category,
        "flag": flag,
        "writeup": filepath,
        "solved_at": datetime.now().isoformat(),
    }

    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False))


def get_similar_writeups(category: str, max_chars: int = 3000) -> str:
    """Retourne les writeups de challenges similaires deja resolus."""
    if not SOLVED_DIR.exists():
        return ""

    cat_slug = _slugify(category or "misc")
    writeups = []

    # Chercher les writeups de la meme categorie
    for f in sorted(SOLVED_DIR.glob(f"{cat_slug}_*.md"), reverse=True)[:3]:
        content = f.read_text(encoding="utf-8", errors="replace")
        writeups.append(content[:1000])

    if not writeups:
        # Fallback: derniers writeups resolus toutes categories
        for f in sorted(SOLVED_DIR.glob("*.md"), reverse=True)[:2]:
            if f.name == "index.json":
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            writeups.append(content[:500])

    if not writeups:
        return ""

    result = "## Challenges similaires deja resolus\n\n"
    result += "\n\n---\n\n".join(writeups)

    return result[:max_chars]


def get_solved_count() -> dict:
    """Retourne les stats des challenges resolus."""
    index_path = SOLVED_DIR / "index.json"
    if not index_path.exists():
        return {"total": 0, "by_category": {}}

    try:
        index = json.loads(index_path.read_text())
        by_cat: dict[str, int] = {}
        for entry in index.values():
            cat = entry.get("category", "misc")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {"total": len(index), "by_category": by_cat}
    except Exception:
        return {"total": 0, "by_category": {}}
