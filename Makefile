# local-tts — Lenovo Go Voice Agent (autonomous multi-turn sessions)
#
# Purpose:
#   Natural voice interface on the Lenovo Go Wired Speaker (plughw:1,0)
#   driving the thelab LangGraph agent + Supermemory brain.
#
#   Pipeline: Parakeet STT (NVIDIA NeMo) → thelab agent → Piper TTS playback.
#   The Teams button starts/ends multi-turn voice sessions with LED feedback.
#   Runs interactively via this Makefile or as systemd user services (make install).
#
# Recommended flow:
#   1. cd ../thelab && make          # preps thelab package (so -e install works)
#   2. (this dir) make keys          # copies .env.example -> .env; then edit .env
#   3. (this dir) make               # runs the voice agent
#   In a second terminal: make button   (or echo start > /tmp/voice_trigger)
#
# What `make` (bare) does:
#   - Creates isolated .venv (never pollutes global python)
#   - pip install -e ../thelab into this venv (pulls the real agent+Supermemory)
#   - Wires keys (via make keys or .env symlink)
#   - Starts the voice agent (Parakeet STT → thelab agent → Piper TTS on Go + LED)
#
# LLM control (env vars are passed through to thelab's config):
#   make                    # default: xai / grok-3-mini (from this Makefile)
#   make ollama             # local Ollama (openai_compatible)
#   LLM_PROVIDER=xai LLM_MODEL=grok-3 make
#   OLLAMA_MODEL=llama3.2 ./run... (when using ollama target)
#
# Key commands:
#   make install       # install as systemd user services (auto-start on boot)
#   make audio-reset   # pkill arecord/aplay cleanup (fixes "Device or resource busy")
#   make reset         # full cleanup (stop + audio + pipes)
#   make demo          # real agent + Supermemory + speak test (no mic)
#   make smoke         # quickest LED + Piper TTS sanity check
#
# See: README.md and specs/001-interim-lenovo-go-voice-spike.md for full context.

SHELL := /bin/bash
.DEFAULT_GOAL := local

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(PYTHON) -m pip

THELAB_ROOT := ../thelab

# Default LLM: xAI Grok (fast, high quality, key already in .env)
LLM_PROVIDER ?= xai
LLM_MODEL ?= grok-3-mini

.PHONY: help venv deps smoke-deps install uninstall keys local ollama demo smoke voice button stop kill pids status reset audio-reset clean service-status service-logs

help:
	@echo "local-tts — Lenovo Go Voice Agent (multi-turn sessions)"
	@echo ""
	@echo "Primary targets (run from this directory):"
	@echo "  make            Voice agent: Parakeet STT → thelab agent+Supermemory → Piper TTS on Go"
	@echo "  make ollama     Same but with local Ollama (see top comment for model override)"
	@echo "  make demo       Real agent + Supermemory + Piper TTS test (no mic)"
	@echo "  make smoke      Fastest sanity check (LED + Piper TTS, no agent, no hardware needed)"
	@echo "  make button     (2nd terminal) Start physical Teams button listener → /tmp/voice_trigger"
	@echo ""
	@echo "Systemd services (auto-start on boot):"
	@echo "  make install    Install voice agent + button listener as systemd user services"
	@echo "  make uninstall  Remove systemd services"
	@echo "  make service-status / service-logs"
	@echo ""
	@echo "Key management (critical for real thelab agent, not mock):"
	@echo "  make keys       Create project-local .env from .env.example (then edit it: XAI_API_KEY, SUPERMEMORY_API_KEY)"
	@echo ""
	@echo "Cleanup (use liberally — audio device contention and stale pipes are the two biggest gotchas):"
	@echo "  make audio-reset   pkill -9 arecord aplay (fixes 'device busy' after recording)"
	@echo "  make reset         Full reset: stop listeners + audio-reset + remove /tmp/voice_* pipes"
	@echo "  make kill          Force kill (local_tts processes + arecord/aplay)"
	@echo "  make stop          Graceful stop (SIGTERM)"
	@echo "  make pids          Show what's running + pipes"
	@echo "  make clean         Remove .venv + .env (start over)"
	@echo ""
	@echo "Other:"
	@echo "  make deps       Just the prep step (venv + evdev + pip -e thelab + keys wiring)"
	@echo "  make voice      Alias for 'make'"
	@echo ""
	@echo "Typical session:"
	@echo "  (thelab dir) make"
	@echo "  (here) make keys   # then edit .env with your keys, then: make"
	@echo "  (2nd shell)  make button   # then press Teams button to start a session"
	@echo "  Speak naturally — multi-turn conversation until you press the button again."
	@echo "  Watch for: [Parakeet] transcript, [AGENT] response, Piper TTS spoken reply + LED."
	@echo ""
	@echo "See top comment block, README.md, and specs/ for full context."

# venv target is resilient: it (re)creates only if the venv python is missing or not executable.
# This survives uv-managed python moves, partial rm, shebang rot on the bin/ scripts, etc.
# We use $(PYTHON) -m pip (via the PIP var) instead of the bin/pip wrapper so we don't
# depend on entrypoint scripts whose shebangs may point at a deleted interpreter.
venv:
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "==> Creating venv at $(VENV)"; \
		rm -rf $(VENV); \
		python3 -m venv $(VENV); \
		$(PIP) install --upgrade pip wheel; \
		touch $(VENV)/bin/activate; \
	fi

deps: venv
	@echo "==> Installing runtime deps (evdev + pyudev for button hotplug)"
	$(PIP) install evdev pyudev
	@echo "==> Installing thelab (editable) into this venv so the spike can drive the *real* agent + Supermemory"
	@echo "    (this is the cross-repo seam; thelab's pyproject.toml + src/thelab_langchain layout is used)"
	$(PIP) install -e "$(THELAB_ROOT)"
	@echo "==> Checking project-local .env (thelab config does load_dotenv() + os.getenv('XAI_API_KEY' etc.))"
	@if [ -e .env ]; then \
		echo "    Project-local .env present (keeping it)."; \
	else \
		echo "    No .env yet. Run 'make keys' (copies .env.example -> .env), then edit it."; \
	fi
	@echo "==> Install complete. Keys must be in the project-local .env for the real agent (not mock fallback)."

# Create the project-local .env from the template so load_dotenv() has
# something to read when the agent graph is imported. The voice loop runs from
# the repo root, so a project-local .env is all that is needed — no personal
# paths, no cross-repo symlink. .env is gitignored; never commit real keys.
keys:
	@if [ -f .env ]; then \
		echo "==> .env already exists (keeping it). Edit it to update your keys."; \
	elif [ -f .env.example ]; then \
		cp .env.example .env && \
		echo "==> Created .env from .env.example. Now edit .env and set:"; \
		echo "    XAI_API_KEY and SUPERMEMORY_API_KEY (plus LLM_PROVIDER / LLM_MODEL as needed)."; \
	else \
		echo "    ERROR: .env.example not found. Create a .env manually with XAI_API_KEY + SUPERMEMORY_API_KEY."; \
		exit 1; \
	fi

local: deps
	@if [ ! -f .env ] || ! grep -q 'API_KEY' .env 2>/dev/null; then \
		echo "WARNING: .env missing or has no API keys. Run 'make keys' or check the symlink."; \
	fi
	@echo "==> Starting voice agent (LLM_PROVIDER=$(LLM_PROVIDER) LLM_MODEL=$(LLM_MODEL))"
	LLM_PROVIDER=$(LLM_PROVIDER) LLM_MODEL=$(LLM_MODEL) ./run_voice_loop.sh

ollama: deps
	@OLLAMA_MODEL=$${OLLAMA_MODEL:-qwen2.5:32b}; \
	echo "==> Starting voice agent with local Ollama (model=$$OLLAMA_MODEL)"; \
	LLM_PROVIDER=openai_compatible LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=$$OLLAMA_MODEL ./run_voice_loop.sh

demo: deps
	LLM_PROVIDER=$(LLM_PROVIDER) LLM_MODEL=$(LLM_MODEL) ./run_voice_loop.sh --demo

# smoke deliberately does NOT depend on `deps` — it must run standalone,
# without the ../thelab package and without any API keys. It only needs the
# venv plus Piper (best-effort; falls back to espeak-ng if Piper is missing).
smoke-deps: venv
	@echo "==> Installing minimal smoke deps (Piper TTS only — no thelab, no keys)"
	-$(PIP) install piper-tts

smoke: smoke-deps
	@echo "==> SMOKE (LED + Piper TTS only — standalone, no thelab, no API keys)"
	./run_voice_loop.sh --smoke

voice: local

button:
	@if [ ! -x "$(PYTHON)" ]; then $(MAKE) install; fi
	@echo "==> Starting button listener (Teams button → /tmp/voice_trigger)"
	$(PYTHON) -m local_tts.button_listener

clean:
	rm -rf $(VENV)
	rm -f .env
	@echo "Clean. Run 'make' to rebuild."

# --- Process management ---

pids status:
	@echo "==> Spike processes (voice_loop, button_listener, audio children):"
	@pgrep -af 'local_tts\.(voice_loop|button_listener|play_tone)' | grep -v 'pgrep' || echo "  (none)"
	@pgrep -af '\barecord\b|\baplay\b|piper' | grep -v 'pgrep' || true
	@pgrep -af 'thelab_langchain|thelab-chat' | grep -v 'pgrep' || echo "  (no thelab cross-repo processes visible)"
	@echo ""
	@echo "==> Named pipes (created on first run of the waiter):"
	@ls -l /tmp/voice_trigger /tmp/voice_speak 2>/dev/null || echo "  (no /tmp/voice_* pipes — they are created lazily by the script)"

stop:
	@echo "==> Stopping spike listeners (SIGTERM)..."
	@pkill -f 'local_tts\.voice_loop' 2>/dev/null || true
	@pkill -f 'local_tts\.button_listener' 2>/dev/null || true
	@echo "==> Sent SIGTERM. Use 'make pids' to verify."

kill:
	@echo "==> Force-killing rogue spike PIDs + audio processes (kill -9)..."
	@pkill -9 -f 'local_tts\.(voice_loop|button_listener)' 2>/dev/null || true
	@pkill -9 arecord aplay 2>/dev/null || true
	@echo "==> Force kill done. 'make pids' or 'make reset' to verify."

# The single command the user requested for the pkill arecord/aplay cleanup.
# Why it exists: the Lenovo Go USB audio (plughw:1,0) frequently stays "busy" for a short time
# after an arecord capture ends. The subsequent Piper TTS aplay in the speak() path then fails
# with "Device or resource busy". This target (and the 0.8s settle sleep inside voice_loop.py)
# + the robust retry loop in _robust_aplay are the current mitigations.
audio-reset:
	@echo "==> Killing lingering arecord/aplay (the #1 cause of playback silence after a trigger)"
	@pkill -9 arecord aplay 2>/dev/null || true
	@echo "==> Audio processes cleaned. If still 'busy', also try:"
	@echo "    make reset; or physically unplug/replug the Lenovo Go."
	@echo "    (This target is intentionally separate so you can call it quickly between attempts.)"

reset: stop audio-reset
	@echo "==> Removing named pipes (in case readers/writers died and left stale state)"
	@rm -f /tmp/voice_trigger /tmp/voice_speak
	@echo "==> Full reset complete (listeners stopped + audio cleaned + both pipes removed)."
	@echo "    Re-run 'make' (or ./run_voice_loop.sh) to recreate pipes and start the waiter fresh."

# --- Service install/uninstall (systemd user services) ---

install:
	@if [ ! -d "$(VENV)" ]; then echo "ERROR: Run 'make deps' first to set up the venv."; exit 1; fi
	@echo "==> Installing local-tts as systemd user services (project dir: $(CURDIR))..."
	@mkdir -p ~/.config/systemd/user
	@sed 's|__PROJECT_DIR__|$(CURDIR)|g' systemd/local-tts.service > ~/.config/systemd/user/local-tts.service
	@sed 's|__PROJECT_DIR__|$(CURDIR)|g' systemd/local-tts-buttons.service > ~/.config/systemd/user/local-tts-buttons.service
	@systemctl --user daemon-reload
	@systemctl --user reset-failed local-tts.service local-tts-buttons.service 2>/dev/null || true
	@systemctl --user enable local-tts.service local-tts-buttons.service
	@systemctl --user start local-tts.service local-tts-buttons.service
	@echo ""
	@echo "==> Installed. Services will auto-start on boot."
	@echo "    LED turns on when voice agent is ready."
	@echo "    Press Teams button to start/end voice sessions."
	@echo ""
	@echo "    Status:     systemctl --user status local-tts"
	@echo "    Logs:       journalctl --user -u local-tts -f"
	@echo "    Stop:       systemctl --user stop local-tts local-tts-buttons"
	@echo "    Uninstall:  make uninstall"

uninstall:
	@echo "==> Uninstalling local-tts systemd services..."
	@systemctl --user stop local-tts.service local-tts-buttons.service 2>/dev/null || true
	@systemctl --user disable local-tts.service local-tts-buttons.service 2>/dev/null || true
	@rm -f ~/.config/systemd/user/local-tts.service
	@rm -f ~/.config/systemd/user/local-tts-buttons.service
	@systemctl --user daemon-reload
	@echo "==> Uninstalled. Services removed and will not auto-start."
	@echo "    You can still run manually with 'make' and 'make button'."

service-status:
	@systemctl --user status local-tts.service local-tts-buttons.service 2>&1 || true

service-logs:
	@journalctl --user -u local-tts -u local-tts-buttons -f --no-hostname
