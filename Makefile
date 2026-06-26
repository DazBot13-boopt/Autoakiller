# CTF Agent — Makefile
# Usage: make build | make push | make pull | make run

DOCKER_USER ?= $(shell docker info 2>/dev/null | grep Username | awk '{print $$2}')
IMAGE_NAME   = ctf-sandbox
REGISTRY     = docker.io/$(DOCKER_USER)/$(IMAGE_NAME)
TAG          = latest

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	docker build -f sandbox/Dockerfile.sandbox -t $(IMAGE_NAME):$(TAG) .
	@echo ""
	@echo "Build done: $(IMAGE_NAME):$(TAG)"

# ── Push vers Docker Hub ──────────────────────────────────────────────────────
push: build
	docker tag $(IMAGE_NAME):$(TAG) $(REGISTRY):$(TAG)
	docker push $(REGISTRY):$(TAG)
	@echo ""
	@echo "Pushed: $(REGISTRY):$(TAG)"
	@echo "Sur ton PC: make pull DOCKER_USER=$(DOCKER_USER)"

# ── Pull depuis Docker Hub (sur ton PC) ───────────────────────────────────────
pull:
	docker pull $(REGISTRY):$(TAG)
	docker tag $(REGISTRY):$(TAG) $(IMAGE_NAME):$(TAG)
	@echo ""
	@echo "Image ready: $(IMAGE_NAME):$(TAG)"

# ── Lancer l'agent ────────────────────────────────────────────────────────────
run:
	uv run ctf-solve -v

# ── Lancer seulement avec Claude (sans Codex) ─────────────────────────────────
run-claude:
	uv run ctf-solve \
		--models claude-sdk/claude-opus-4-6/medium \
		--models claude-sdk/claude-opus-4-6/max \
		--coordinator claude -v

# ── Test rapide (dry-run, pas de soumission) ──────────────────────────────────
test:
	uv run ctf-solve --no-submit -v

# ── Update depuis GitHub ──────────────────────────────────────────────────────
update:
	git pull
	UV_HTTP_TIMEOUT=300 uv sync
	@echo "Code mis à jour. Relance avec: make run"

install:
	UV_HTTP_TIMEOUT=300 uv sync

# ── Nettoyer les containers orphelins ────────────────────────────────────────
clean:
	docker ps -a --filter "name=ctf-" --format "{{.ID}}" | xargs -r docker rm -f
	@echo "Containers nettoyés"

.PHONY: build push pull run test install clean
