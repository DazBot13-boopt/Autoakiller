"""Click CLI entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from backend.config import Settings
from backend.models import DEFAULT_MODELS

console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiodocker").setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%X"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


@click.command()
# ── Platform connection ───────────────────────────────────────────────────────
@click.option("--url", default=None, help="CTF platform URL (any supported site)")
@click.option("--user", default=None, help="Username / email for login")
@click.option("--password", default=None, help="Password for login")
@click.option("--token", default=None, help="API token (optional, overrides user/password)")
# ── Legacy CTFd aliases (backward-compat) ─────────────────────────────────────
@click.option("--ctfd-url", default=None, hidden=True, help="[legacy] CTFd URL")
@click.option("--ctfd-token", default=None, hidden=True, help="[legacy] CTFd API token")
# ── Solver options ────────────────────────────────────────────────────────────
@click.option("--image", default="ctf-sandbox", help="Docker sandbox image name")
@click.option("--models", multiple=True, help="Model specs (default: all configured)")
@click.option("--challenge", default=None, help="Solve a single challenge directory")
@click.option("--challenges-dir", default="challenges", help="Directory for challenge files")
@click.option("--no-submit", is_flag=True, help="Dry run — don't submit flags")
@click.option("--coordinator-model", default=None, help="Model for coordinator (default: claude-opus-4-6)")
@click.option("--coordinator", default="claude", type=click.Choice(["claude", "codex"]), help="Coordinator backend")
@click.option("--max-challenges", default=3, type=int, help="Max challenges solved concurrently")
@click.option("--msg-port", default=0, type=int, help="Operator message port (0 = auto)")
@click.option("--challenges-json", default=None, help="Path to local JSON/YAML file with challenge data (offline mode)")
@click.option("--only", multiple=True, help="Solve only these specific challenges (by name). Can be repeated.")
@click.option("--max-bumps", default=10, type=int, help="Max retries per challenge before giving up (0 = unlimited)")
@click.option("--local", "local_mode", is_flag=True, help="Run solvers locally without Docker (uses host tools)")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def main(
    url: str | None,
    user: str | None,
    password: str | None,
    token: str | None,
    ctfd_url: str | None,
    ctfd_token: str | None,
    image: str,
    models: tuple[str, ...],
    challenge: str | None,
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator: str,
    max_challenges: int,
    msg_port: int,
    challenges_json: str | None,
    only: tuple[str, ...],
    max_bumps: int,
    local_mode: bool,
    verbose: bool,
) -> None:
    """CTF Agent — multi-model solver swarm.

    Point it at any CTF platform and it will auto-detect, login, and solve.

    Examples:
      ctf-solve --url https://ctf.example.com --user team --password secret
      ctf-solve --url https://app.hackthebox.com --token your_htb_token
      ctf-solve --url https://2026.picoctf.org --user me --password pw
      ctf-solve --challenge ./challenges/my-challenge  # single challenge, no platform
    """
    _setup_logging(verbose)

    settings = Settings(sandbox_image=image)

    # Resolve effective URL/credentials (new flags > legacy flags > .env)
    effective_url = url or ctfd_url or settings.effective_url()
    effective_user = user or settings.effective_user()
    effective_pass = password or settings.effective_pass()
    effective_token = token or ctfd_token or settings.effective_token()

    settings.ctf_url = effective_url
    settings.ctf_user = effective_user
    settings.ctf_pass = effective_pass
    settings.ctf_token = effective_token
    settings.max_concurrent_challenges = max_challenges

    model_specs = list(models) if models else list(DEFAULT_MODELS)

    console.print("[bold]CTF Agent[/bold]")
    console.print(f"  Platform: {effective_url}")
    if effective_user:
        console.print(f"  User    : {effective_user}")
    if effective_token:
        console.print(f"  Token   : {'*' * 8}{effective_token[-4:] if len(effective_token) > 4 else '****'}")
    console.print(f"  Models  : {', '.join(model_specs)}")
    console.print(f"  Image   : {settings.sandbox_image}")
    console.print(f"  Max     : {max_challenges} concurrent challenges")
    if only:
        console.print(f"  Filter  : {', '.join(only)}")
    console.print(f"  Max bumps: {max_bumps if max_bumps > 0 else 'unlimited'}")
    if local_mode:
        console.print(f"  [bold yellow]Mode LOCAL — pas de Docker, outils hôte[/bold yellow]")
    console.print()

    settings.local_mode = local_mode

    if challenge:
        asyncio.run(_run_single(settings, challenge, model_specs, no_submit, max_challenges, local_mode=local_mode))
    else:
        asyncio.run(_run_coordinator(
            settings, model_specs, challenges_dir, no_submit,
            coordinator_model, coordinator, max_challenges, msg_port,
            challenges_json=challenges_json,
            only_challenges=list(only),
            max_bumps=max_bumps,
            local_mode=local_mode,
        ))


async def _build_platform_client(settings: Settings, challenges_json: str | None = None):
    """Auto-detect platform and return a PlatformClient."""
    from backend.platform.detect import detect_platform
    from backend.platform.compat import PlatformClient

    platform = await detect_platform(
        url=settings.effective_url(),
        username=settings.effective_user(),
        password=settings.effective_pass(),
        token=settings.effective_token(),
        challenges_json=challenges_json or "",
    )
    console.print(f"  [dim]Platform detected: {platform.platform_name}[/dim]")
    return PlatformClient(platform)


async def _run_single(
    settings: Settings,
    challenge_dir: str,
    model_specs: list[str],
    no_submit: bool,
    max_challenges: int,
) -> None:
    """Run a single challenge with a swarm (no platform required)."""
    from backend.agents.swarm import ChallengeSwarm
    from backend.cost_tracker import CostTracker
    from backend.prompts import ChallengeMeta
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore

    max_containers = max_challenges * len(model_specs)
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()

    challenge_path = Path(challenge_dir)
    meta_path = challenge_path / "metadata.yml"
    if not meta_path.exists():
        console.print(f"[red]No metadata.yml found in {challenge_dir}[/red]")
        sys.exit(1)

    meta = ChallengeMeta.from_yaml(meta_path)
    console.print(f"[bold]Challenge:[/bold] {meta.name} ({meta.category}, {meta.value} pts)")

    # For single-challenge mode we still need a platform client (for flag submission)
    # If no URL is configured we use a no-op client
    client = await _build_platform_client(settings)
    cost_tracker = CostTracker()

    swarm = ChallengeSwarm(
        challenge_dir=str(challenge_path),
        meta=meta,
        ctfd=client,
        cost_tracker=cost_tracker,
        settings=settings,
        model_specs=model_specs,
        no_submit=no_submit,
    )

    try:
        result = await swarm.run()
        from backend.solver_base import FLAG_FOUND
        if result and result.status == FLAG_FOUND:
            console.print(f"\n[bold green]FLAG FOUND:[/bold green] {result.flag}")
        else:
            console.print("\n[bold red]No flag found.[/bold red]")

        console.print("\n[bold]Cost Summary:[/bold]")
        for agent_name in cost_tracker.by_agent:
            console.print(f"  {agent_name}: {cost_tracker.format_usage(agent_name)}")
        console.print(f"  [bold]Total: ${cost_tracker.total_cost_usd:.2f}[/bold]")
    finally:
        await client.close()


async def _run_coordinator(
    settings: Settings,
    model_specs: list[str],
    challenges_dir: str,
    no_submit: bool,
    coordinator_model: str | None,
    coordinator_backend: str,
    max_challenges: int,
    msg_port: int = 0,
    challenges_json: str | None = None,
    only_challenges: list[str] | None = None,
    max_bumps: int = 10,
    local_mode: bool = False,
) -> None:
    """Run the full coordinator (continuous until Ctrl+C)."""
    from backend.sandbox import cleanup_orphan_containers, configure_semaphore

    max_containers = max_challenges * len(model_specs)
    configure_semaphore(max_containers)
    await cleanup_orphan_containers()

    client = await _build_platform_client(settings, challenges_json=challenges_json)
    if only_challenges:
        console.print(f"  [dim]Filtering to challenges: {', '.join(only_challenges)}[/dim]")
    console.print(f"[bold]Starting coordinator ({coordinator_backend}, Ctrl+C to stop)...[/bold]\n")

    try:
        if coordinator_backend == "codex":
            from backend.agents.codex_coordinator import run_codex_coordinator
            results = await run_codex_coordinator(
                settings=settings,
                model_specs=model_specs,
                challenges_root=challenges_dir,
                no_submit=no_submit,
                coordinator_model=coordinator_model,
                msg_port=msg_port,
                ctfd=client,
                only_challenges=only_challenges,
                max_bumps=max_bumps,
                local_mode=local_mode,
            )
        else:
            from backend.agents.claude_coordinator import run_claude_coordinator
            results = await run_claude_coordinator(
                settings=settings,
                model_specs=model_specs,
                challenges_root=challenges_dir,
                no_submit=no_submit,
                coordinator_model=coordinator_model,
                msg_port=msg_port,
                ctfd=client,
                only_challenges=only_challenges,
                max_bumps=max_bumps,
                local_mode=local_mode,
            )
    finally:
        await client.close()

    console.print("\n[bold]Final Results:[/bold]")
    for challenge, data in results.get("results", {}).items():
        console.print(f"  {challenge}: {data.get('flag', 'no flag')}")
    console.print(f"\n[bold]Total cost: ${results.get('total_cost_usd', 0):.2f}[/bold]")


@click.command()
@click.argument("message")
@click.option("--port", default=9400, type=int, help="Coordinator message port")
@click.option("--host", default="127.0.0.1", help="Coordinator host")
def msg(message: str, port: int, host: str) -> None:
    """Send a message to the running coordinator."""
    import json
    import urllib.request

    body = json.dumps({"message": message}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/msg",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            console.print(f"[green]Sent:[/green] {data.get('queued', message[:200])}")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        console.print("Is the coordinator running?")
        sys.exit(1)


@click.command()
def pending() -> None:
    """Afficher tous les flags en attente de validation manuelle."""
    from backend.pending_flags import show_all_pending
    show_all_pending()


@click.command()
@click.argument("challenge_name")
@click.argument("flag")
@click.option("--url", default=None, help="CTF platform URL")
@click.option("--token", default=None, help="CTF API token")
def submit(challenge_name: str, flag: str, url: str | None, token: str | None) -> None:
    """Soumettre manuellement un flag pending."""
    import asyncio as _asyncio
    from backend.config import Settings
    from backend.pending_flags import mark_submitted, load_pending

    settings = Settings()
    effective_url = url or settings.effective_url()
    effective_token = token or settings.effective_token()
    settings.ctf_url = effective_url
    settings.ctf_token = effective_token

    async def _submit():
        from backend.platform.detect import detect_platform
        from backend.platform.compat import PlatformClient
        platform = await detect_platform(
            url=effective_url,
            token=effective_token,
            username=settings.effective_user(),
            password=settings.effective_pass(),
        )
        client = PlatformClient(platform)
        try:
            result = await client.submit_flag(challenge_name, flag)
            if result.status in ("correct", "already_solved"):
                console.print(f"[bold green]✅ CORRECT — {result.display}[/bold green]")
                mark_submitted(challenge_name, flag, result.status)
            else:
                console.print(f"[bold red]❌ INCORRECT — {result.display}[/bold red]")
        finally:
            await client.close()

    _asyncio.run(_submit())


if __name__ == "__main__":
    main()
