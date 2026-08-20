#!/bin/bash
# Run the local-tts voice agent.
#
# Multi-turn voice sessions: Parakeet STT -> thelab LangGraph agent -> Piper TTS.
# The Teams button on the Lenovo Go speaker starts and ends each session.
#
# This script is normally invoked by the local-tts Makefile (make, make demo, make ollama, etc.).
# It activates the project-local .venv (never global python) and runs the Python module.
#
# The Makefile passes LLM_PROVIDER / LLM_MODEL (and for ollama also LLM_BASE_URL) so that
# thelab's config (pydantic settings + load_dotenv) picks up the desired brain.
#
# Usage (direct, advanced):
#   ./run_voice_loop.sh
#   ./run_voice_loop.sh --demo     # real agent + Supermemory + speak, no mic
#   ./run_voice_loop.sh --smoke    # fastest LED + Piper TTS sanity check
#
# You should normally just do:
#   (thelab) make
#   (this repo) make keys   # then edit .env
#   (this repo) make
#
# See the top of Makefile and README.md for the full recommended flow.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Error: .venv not found. Run 'make install' (or 'make') first."
    exit 1
fi

source "$VENV/bin/activate"

# Helpful startup banner so you can see at a glance which brain is active.
echo "==> local-tts voice agent starting"
echo "    LLM_PROVIDER=${LLM_PROVIDER:-xai}  LLM_MODEL=${LLM_MODEL:-grok-3-mini}"
if [ -n "$LLM_BASE_URL" ]; then
    echo "    LLM_BASE_URL=$LLM_BASE_URL"
fi
echo "    (Parakeet STT on CPU, Piper TTS, multi-turn sessions, real thelab agent when keys present)"
echo ""

# Use the explicit venv python binary (do not rely on bare "python" in PATH).
# The user's environment may only have "python3" globally, and even after
# `source activate` some setups don't expose a "python" command.
# The Makefile and other launchers already use the full $(PYTHON) path for this reason.
# exec so this process *is* the voice loop: Ctrl+C / SIGTERM reach Python
# instead of sitting in an extra bash that wait()s on it (and make waiting
# on that bash).
export PYTHONPATH="$SCRIPT_DIR"
exec "$VENV/bin/python" -m local_tts.voice_loop "$@"
