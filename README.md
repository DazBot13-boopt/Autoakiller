# CTF Agent

Autonomous CTF (Capture The Flag) solver that races multiple AI models against challenges in parallel. Built in a weekend, we used it to solve all 52/52 challenges and win **1st place at BSidesSF 2026 CTF**.

Built by [Veria Labs](https://verialabs.com), founded by members of [.;,;.](https://ctftime.org/team/222911) (smiley), the [#1 US CTF team on CTFTime in 2024 and 2025](https://ctftime.org/stats/2024/US). We build AI agents that find and exploit real security vulnerabilities for large enterprises.

## Results

| Competition | Challenges Solved | Result |
|-------------|:-:|--------|
| **BSidesSF 2026** | 52/52 (100%) | **1st place ($1,500)** |

The agent solves challenges across all categories — pwn, rev, crypto, forensics, web, and misc.

## How It Works

A **coordinator** LLM manages the competition while **solver swarms** attack individual challenges. Each swarm runs multiple models simultaneously — the first to find the flag wins.

```
                        +-----------------+
                        |  CTFd Platform  |
                        +--------+--------+
                                 |
                        +--------v--------+
                        |  Poller (5s)    |
                        +--------+--------+
                                 |
                        +--------v--------+
                        | Coordinator LLM |
                        | (Claude/Codex)  |
                        +--------+--------+
                                 |
              +------------------+------------------+
              |                  |                  |
     +--------v--------+ +------v---------+ +------v---------+
     | Swarm:          | | Swarm:         | | Swarm:         |
     | challenge-1     | | challenge-2    | | challenge-N    |
     |                 | |                | |                |
     |  Opus (med)     | |  Opus (med)    | |                |
     |  Opus (max)     | |  Opus (max)    | |     ...        |
     |  GPT-5.4        | |  GPT-5.4       | |                |
     |  GPT-5.4-mini   | |  GPT-5.4-mini  | |                |
     |  GPT-5.3-codex  | |  GPT-5.3-codex | |                |
     +--------+--------+ +--------+-------+ +----------------+
              |                    |
     +--------v--------+  +-------v--------+
     | Docker Sandbox  |  | Docker Sandbox |
     | (isolated)      |  | (isolated)     |
     |                 |  |                |
     | pwntools, r2,   |  | pwntools, r2,  |
     | gdb, python...  |  | gdb, python... |
     +-----------------+  +----------------+
```

Each solver runs in an isolated Docker container with CTF tools pre-installed. Solvers never give up — they keep trying different approaches until the flag is found.

## Quick Start

```bash
# Install
uv sync

# Build sandbox image
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .

# Configure credentials
cp .env.example .env
# Edit .env with your CTF platform URL and credentials
# No API keys required — uses claude and codex CLIs

# Run against any CTF platform (auto-detected)
uv run ctf-solve \
  --url https://ctf.example.com \
  --user yourteam \
  --password yourpassword \
  --max-challenges 10 \
  -v

# HackTheBox CTF event
uv run ctf-solve --url https://ctf.hackthebox.com/event/123 --token your_htb_token

# picoCTF
uv run ctf-solve --url https://play.picoctf.org --user me --password pw

# Single challenge (no platform needed)
uv run ctf-solve --challenge ./challenges/my-challenge

# Offline mode (local JSON file with challenge data)
uv run ctf-solve --url http://any.ctf.site --challenges-json challenges.json
```

## Supported Platforms

The agent auto-detects and adapts to any CTF platform:

| Platform | Detection | Auth |
|----------|-----------|------|
| **CTFd** | HTML fingerprint | user/pass or token |
| **HackTheBox** | Hostname or HTML | API token or email/pass |
| **rCTF** | HTML fingerprint | team token |
| **picoCTF** | Hostname or HTML | user/pass or token |
| **Generic** | Fallback | user/pass, token, or unauthenticated |

Pass `--url`, `--user`, `--password` (and optionally `--token`) — the rest is automatic.

## Coordinator Backends

```bash
# Claude CLI coordinator (default)
uv run ctf-solve --url https://ctf.example.com --user team --password pw --coordinator claude

# Codex CLI coordinator
uv run ctf-solve --url https://ctf.example.com --user team --password pw --coordinator codex
```

## Models — No API Keys Required

The default setup uses only the `claude` and `codex` CLIs — **zero API keys needed**.

| Model | Provider | CLI |
|-------|----------|-----|
| Claude Opus 4.6 (medium) | Claude Code | `claude` CLI |
| Claude Opus 4.6 (max) | Claude Code | `claude` CLI |
| GPT-5.4 | Codex | `codex` CLI |
| GPT-5.4-mini | Codex | `codex` CLI |
| GPT-5.3-codex | Codex | `codex` CLI |

Optional API-backed fallbacks (Bedrock, Azure, Google) are available if you set the corresponding keys in `.env`.

## Requirements

- Python 3.14+
- Docker
- `claude` CLI (Claude Code) — authenticated
- `codex` CLI — authenticated
- No API keys required for the default configuration

## Sandbox Tooling

Each solver gets an isolated Docker container pre-loaded with CTF tools:

| Category | Tools |
|----------|-------|
| **Binary** | radare2, GDB, objdump, binwalk, strings, readelf |
| **Pwn** | pwntools, ROPgadget, angr, unicorn, capstone |
| **Crypto** | SageMath, RsaCtfTool, z3, gmpy2, pycryptodome, cado-nfs |
| **Forensics** | volatility3, Sleuthkit (mmls/fls/icat), foremost, exiftool |
| **Stego** | steghide, stegseek, zsteg, ImageMagick, tesseract OCR |
| **Web** | curl, nmap, Python requests, flask |
| **Misc** | ffmpeg, sox, Pillow, numpy, scipy, PyTorch, podman |

## Features

- **Multi-model racing** — multiple AI models attack each challenge simultaneously
- **Auto-spawn** — new challenges detected and attacked automatically
- **Coordinator LLM** — reads solver traces, crafts targeted technical guidance
- **Cross-solver insights** — findings shared between models via message bus
- **Docker sandboxes** — isolated containers with full CTF tooling
- **Operator messaging** — send hints to running solvers mid-competition

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

```env
# CTF Platform (any supported site)
CTF_URL=https://ctf.example.com
CTF_USER=yourteam
CTF_PASS=yourpassword
CTF_TOKEN=           # Optional API token

# API keys — only needed for optional API-backed fallback providers
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
```

## Requirements

- Python 3.14+
- Docker
- API keys for at least one provider (Anthropic, OpenAI, Google)
- `codex` CLI (for Codex solver/coordinator)
- `claude` CLI (bundled with claude-agent-sdk)

## Acknowledgements

- [es3n1n/Eruditus](https://github.com/es3n1n/Eruditus) — CTFd interaction and HTML helpers in `pull_challenges.py`
