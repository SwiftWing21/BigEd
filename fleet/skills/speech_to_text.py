"""
Speech-to-Text — Local-first voice input for BigEd CC.

Uses faster-whisper or whisper.cpp for local STT (no cloud dependency).
Falls back to cloud STT APIs if configured and local unavailable.

Actions:
  transcribe    — transcribe an audio file
  listen        — capture from microphone and transcribe
  wake_listen   — listen for wake word, then capture command
  check         — verify STT availability (local vs cloud)

Privacy: audio never stored beyond transcription. Local-first by default.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "speech_to_text"
DESCRIPTION = "Local-first speech-to-text for voice input. Privacy-first: audio never stored."
REQUIRES_NETWORK = False


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "check")

    if action == "check":
        return _check_availability(config, log)
    elif action == "transcribe":
        return _transcribe(payload, config, log)
    elif action == "listen":
        return _listen(payload, config, log)
    elif action == "wake_listen":
        return _wake_word_listen(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}"}


def _check_availability(config, log) -> dict:
    """Check which STT backends are available."""
    backends = {}

    # Check faster-whisper (Python package)
    try:
        import faster_whisper
        backends["faster_whisper"] = {"available": True, "version": faster_whisper.__version__}
    except ImportError:
        backends["faster_whisper"] = {"available": False, "install": "pip install faster-whisper"}

    # Check whisper.cpp (CLI)
    import shutil
    whisper_cpp = shutil.which("whisper-cpp") or shutil.which("main")
    backends["whisper_cpp"] = {"available": bool(whisper_cpp), "path": whisper_cpp or "not found"}

    # Check sounddevice (microphone capture)
    try:
        import sounddevice
        backends["microphone"] = {"available": True, "devices": len(sounddevice.query_devices())}
    except ImportError:
        backends["microphone"] = {"available": False, "install": "pip install sounddevice"}

    # Determine best available
    best = None
    if backends["faster_whisper"]["available"]:
        best = "faster_whisper"
    elif backends["whisper_cpp"]["available"]:
        best = "whisper_cpp"

    stt_cfg = config.get("assistant", {})

    return {
        "backends": backends,
        "best_available": best,
        "model": stt_cfg.get("stt_model", "base"),
        "local_only": stt_cfg.get("stt_local_only", True),
    }


def _transcribe(payload, config, log) -> dict:
    """Transcribe an audio file to text."""
    audio_path = payload.get("audio_path", "")
    if not audio_path or not Path(audio_path).exists():
        return {"error": f"Audio file not found: {audio_path}"}

    stt_cfg = config.get("assistant", {})
    model_size = stt_cfg.get("stt_model", "base")

    # Try faster-whisper first
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_path, beam_size=5)
        text = " ".join(seg.text for seg in segments)
        log.info(f"Transcribed: {len(text)} chars, language={info.language}")
        return {
            "text": text.strip(),
            "language": info.language,
            "duration_secs": round(info.duration, 1),
            "backend": "faster_whisper",
            "model": model_size,
        }
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"faster-whisper failed: {e}")

    # Fallback: whisper.cpp CLI
    import shutil
    whisper_exe = shutil.which("whisper-cpp") or shutil.which("main")
    if whisper_exe:
        try:
            result = subprocess.run(
                [whisper_exe, "-m", f"models/ggml-{model_size}.bin", "-f", audio_path, "--no-timestamps"],
                capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0:
                text = result.stdout.strip()
                return {"text": text, "backend": "whisper_cpp", "model": model_size}
        except Exception as e:
            log.warning(f"whisper.cpp failed: {e}")

    return {"error": "No STT backend available. Install: pip install faster-whisper"}


def _listen(payload, config, log) -> dict:
    """Capture audio from microphone and transcribe."""
    duration = payload.get("duration_secs", 5)

    try:
        import sounddevice as sd
        import numpy as np
        import tempfile
        import wave

        sample_rate = 16000
        log.info(f"Recording {duration}s from microphone...")
        audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate,
                       channels=1, dtype='int16')
        sd.wait()

        # Save to temp WAV
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        with wave.open(tmp.name, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())

        # Transcribe
        result = _transcribe({"audio_path": tmp.name}, config, log)

        # Clean up audio immediately (privacy)
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

        result["recorded_secs"] = duration
        return result

    except ImportError:
        return {"error": "Microphone capture requires: pip install sounddevice numpy"}
    except Exception as e:
        return {"error": f"Microphone capture failed: {e}"}


def _wake_word_listen(payload, config, log) -> dict:
    """Listen for wake word, then capture and transcribe."""
    wake_word = config.get("assistant", {}).get("wake_word", "hey biged")
    if not wake_word:
        return {"error": "No wake word configured in fleet.toml [assistant] wake_word"}

    duration = payload.get("listen_secs", 3)
    max_attempts = payload.get("max_attempts", 10)

    log.info(f"Listening for wake word: '{wake_word}' ({max_attempts} attempts)")

    for attempt in range(max_attempts):
        result = _listen({"duration_secs": duration}, config, log)
        if "text" in result and result["text"]:
            text_lower = result["text"].lower().strip()
            if wake_word.lower() in text_lower:
                # Wake word detected — capture the command after it
                command = text_lower.split(wake_word.lower(), 1)[-1].strip()
                if not command:
                    # Wake word alone — listen again for the command
                    log.info("Wake word detected — listening for command...")
                    cmd_result = _listen({"duration_secs": 5}, config, log)
                    command = cmd_result.get("text", "")
                return {
                    "wake_detected": True,
                    "command": command,
                    "attempt": attempt + 1,
                    "wake_word": wake_word,
                }

    return {"wake_detected": False, "attempts": max_attempts}
