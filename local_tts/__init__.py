"""local-tts — autonomous voice agent on Lenovo Go hardware.

Multi-turn voice sessions driven by the Microsoft Teams button:
  Parakeet STT (NVIDIA NeMo) captures speech,
  thelab LangGraph agent + Supermemory generates a response,
  Piper TTS synthesises audio playback on the Lenovo Go speaker.

Runs as a pair of systemd user services (voice loop + button listener)
or interactively via the Makefile / run_voice_loop.sh.
"""

from .play_tone import play_diagnostic_tone, generate_tone_wav

# voice_loop is a standalone script for now (run with -m local_tts.voice_loop)
# from .voice_loop import ...  # not yet modularized
