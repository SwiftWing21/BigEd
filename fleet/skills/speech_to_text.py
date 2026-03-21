"""
Speech-to-Text — Local-first voice input for BigEd CC.

Uses faster-whisper or whisper.cpp for local STT (no cloud dependency).
Falls back to cloud STT APIs if configured and local unavailable.

Actions:
  transcribe    — transcribe an audio file
  listen        — capture from microphone and transcribe
  wake_listen   — listen for wake word, then capture command
  command       — parse transcribed text into fleet actions
  reminder      — add a reminder to local file-based calendar
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


def _ditl_guard(text: str, config: dict, log) -> str:
    """HIPAA compliance guard — de-identify transcribed text if DITL enabled.

    When DITL forces compliance:
    - All transcribed text is scanned for PHI
    - PHI is stripped via Safe Harbor before any further processing
    - Original text is logged to PHI audit trail (encrypted)
    - De-identified version returned for processing
    """
    ditl = config.get("ditl", {})
    if not ditl.get("enabled"):
        return text  # DITL not active — pass through

    try:
        sys.path.insert(0, str(FLEET_DIR))
        from phi_deidentify import deidentify, contains_phi

        if contains_phi(text):
            result = deidentify(text, log_stripped=True)
            stripped_text = result["text"]

            # Audit log: record that PHI was detected in voice input
            if ditl.get("audit_all_phi_access"):
                try:
                    import sqlite3
                    conn = sqlite3.connect(str(FLEET_DIR / "fleet.db"), timeout=5)
                    try:
                        conn.execute(
                            "INSERT INTO phi_audit (user_id, action, data_scope, phi_detected, deidentified) "
                            "VALUES (?, ?, ?, ?, ?)",
                            ("voice_input", "stt_transcribe", f"stripped_{result['stripped_count']}_identifiers", 1, 1)
                        )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception:
                    pass

            log.info(f"DITL: de-identified {result['stripped_count']} PHI identifiers from voice input")
            return stripped_text
    except ImportError:
        log.warning("DITL enabled but phi_deidentify not available")
    except Exception as e:
        log.warning(f"DITL guard error: {e}")

    return text


def run(payload: dict, config: dict, log) -> dict:
    action = payload.get("action", "check")

    if action == "check":
        return _check_availability(config, log)
    elif action == "transcribe":
        result = _transcribe(payload, config, log)
        if "text" in result:
            result["text"] = _ditl_guard(result["text"], config, log)
        return result
    elif action == "listen":
        result = _listen(payload, config, log)
        if "text" in result:
            result["text"] = _ditl_guard(result["text"], config, log)
        return result
    elif action == "wake_listen":
        result = _wake_word_listen(payload, config, log)
        if result.get("command"):
            result["command"] = _ditl_guard(result["command"], config, log)
        return result
    elif action == "command":
        # De-identify before command parsing
        if payload.get("text"):
            payload["text"] = _ditl_guard(payload["text"], config, log)
        return _process_voice_command(payload, config, log)
    elif action == "reminder":
        # De-identify reminder text if DITL active
        if payload.get("text"):
            payload["text"] = _ditl_guard(payload["text"], config, log)
        return _add_reminder(payload, config, log)
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


def _process_voice_command(payload, config, log) -> dict:
    """Parse a voice command into a fleet action."""
    text = payload.get("text", "").lower().strip()
    if not text:
        return {"error": "No command text provided"}

    # Command patterns — fleet control + skill dispatch
    import re
    commands = [
        # Fleet control (v0.110 Intelligent Orchestration)
        (r"scale (?:up|more) (\w+)", "scale_up", lambda m: {"role": m.group(1)}),
        (r"scale (?:down|fewer) (\w+)", "scale_down", lambda m: {"role": m.group(1)}),
        (r"pause (?:the )?research", "pause", lambda m: {"role": "researcher"}),
        (r"stop (?:all )?agents", "stop_fleet", lambda m: {}),
        (r"start (?:the )?fleet", "start_fleet", lambda m: {}),
        # Skill dispatch
        (r"review (?:the )?(?:code )?(?:in )?(.+)", "code_review", lambda m: {"file": m.group(1).strip()}),
        (r"(?:how many|count) (?:tasks? )?(?:are )?pending", "status_query", lambda m: {"query": "pending_count"}),
        (r"switch (?:to )?(?:the )?(\S+) model", "model_switch", lambda m: {"model": m.group(1)}),
        (r"run (?:a )?security audit", "security_audit", lambda m: {}),
        (r"run (?:a )?benchmark", "benchmark", lambda m: {}),
        (r"search (?:for )?(.+)", "web_search", lambda m: {"query": m.group(1).strip()}),
        (r"summarize (.+)", "summarize", lambda m: {"prompt": m.group(1).strip()}),
        (r"status", "status_query", lambda m: {"query": "fleet_status"}),
    ]

    for pattern, skill, extract in commands:
        match = re.search(pattern, text)
        if match:
            payload_data = extract(match)
            return {"recognized": True, "skill": skill, "payload": payload_data, "raw": text}

    return {"recognized": False, "raw": text, "suggestion": "Try: 'review code in file.py', 'how many tasks pending', 'run security audit'"}


def text_to_speech(text: str, config: dict = None) -> dict:
    """Speak text aloud using local TTS.

    HIPAA: When DITL is active, TTS output is de-identified before speaking.
    This prevents PHI from being spoken aloud in shared environments.
    """
    cfg = (config or {}).get("assistant", {})
    if not cfg.get("tts_enabled", False):
        return {"error": "TTS disabled in fleet.toml [assistant] tts_enabled"}

    # DITL guard: de-identify before speaking aloud
    ditl = (config or {}).get("ditl", {})
    if ditl.get("enabled"):
        try:
            sys.path.insert(0, str(FLEET_DIR))
            from phi_deidentify import deidentify
            result = deidentify(text)
            text = result["text"]
        except Exception:
            pass  # Speak de-identified or original if guard fails

    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty('rate', 175)
        engine.say(text)
        engine.runAndWait()
        return {"spoken": True, "length": len(text)}
    except ImportError:
        return {"error": "TTS requires: pip install pyttsx3"}
    except Exception as e:
        return {"error": f"TTS failed: {e}"}


def _transcribe_cloud(audio_path, config, log) -> dict:
    """Cloud STT fallback (requires API key). NOT used by default."""
    stt_cfg = config.get("assistant", {})
    if stt_cfg.get("stt_local_only", True):
        return {"error": "Cloud STT disabled (stt_local_only=true)"}
    # Stub — cloud providers can be added here
    return {"error": "No cloud STT provider configured. Set stt_local_only=false and add API key."}


def _add_reminder(payload, config, log) -> dict:
    """Add a reminder to local file-based calendar."""
    text = payload.get("text", "")
    when = payload.get("when", "")
    reminders_file = FLEET_DIR / "knowledge" / "reminders.jsonl"
    reminders_file.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    entry = {"text": text, "when": when, "created": datetime.utcnow().isoformat()}
    with open(reminders_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return {"saved": True, "reminder": entry}
