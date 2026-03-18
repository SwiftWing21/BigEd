#!/usr/bin/env python3
"""
Fleet Discord bridge — long-lived bot process managed by supervisor.
Routes messages from biged-fleetchat to fleet agents and posts results back.

Commands:
  /aider <instructions>   — code_write via aider + local Ollama
  /claude <prompt>        — Claude API (Sonnet)
  /gemini <prompt>        — Gemini API
  /local <prompt>         — Local Ollama (qwen3:8b)
  /status                 — Fleet status snapshot
  /task <natural request> — Queue a fleet task (auto-routes to skill)
  /result <id>            — Get result of a completed task

Agent addressing (natural names):
  biged <request>         — Supervisor (queues fleet task, reports result)
  lcbiged <prompt>        — Local console (Ollama direct)
  clauded <prompt>        — Claude API
  gemined <prompt>        — Gemini API
  Agents respond with status when unavailable (sleeping/resting).
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

try:
    import discord
except ImportError:
    print("discord.py not installed. Run: pip install discord.py")
    sys.exit(1)

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config

# ── Config ───────────────────────────────────────────────────────────────────
CHANNEL_ID = 1483720731014594560
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
MAX_MSG_LEN = 1900  # Discord limit is 2000, leave room for formatting

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DISCORD] %(message)s",
    handlers=[
        logging.FileHandler(FLEET_DIR / "logs" / "discord_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("discord_bot")

# ── Discord client ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

config = None


def _truncate(text: str, limit: int = MAX_MSG_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _format_code(text: str, lang: str = "") -> str:
    return f"```{lang}\n{_truncate(text, MAX_MSG_LEN - 20)}\n```"


async def _reply(message: discord.Message, text: str):
    """Send a reply, splitting into multiple messages if needed."""
    chunks = []
    while len(text) > 2000:
        # Find a good split point
        split = text.rfind("\n", 0, 1900)
        if split == -1:
            split = 1900
        chunks.append(text[:split])
        text = text[split:]
    chunks.append(text)
    for chunk in chunks:
        if chunk.strip():
            await message.channel.send(chunk)


# ── Command handlers ─────────────────────────────────────────────────────────

async def cmd_status(message: discord.Message, _args: str):
    """Fleet status snapshot."""
    try:
        status = db.get_fleet_status()
        lines = ["**Fleet Status**\n"]
        lines.append("```")
        lines.append(f"{'Agent':<20} {'Role':<18} {'Status':<8}")
        lines.append("-" * 48)
        for a in status["agents"]:
            lines.append(f"{a['name']:<20} {a['role']:<18} {a['status']:<8}")
        t = status["tasks"]
        lines.append(f"\nTasks: {t['PENDING']} pending | {t['RUNNING']} running | {t['DONE']} done | {t['FAILED']} failed")
        lines.append("```")
        await _reply(message, "\n".join(lines))
    except Exception as e:
        await _reply(message, f"Error getting status: {e}")


async def cmd_local(message: discord.Message, args: str):
    """Send prompt to local Ollama."""
    if not args:
        await _reply(message, "Usage: `/local <prompt>`")
        return
    await message.add_reaction("\u23f3")  # hourglass
    try:
        from skills._models import _call_local
        models = config.get("models", {})
        result = await asyncio.to_thread(_call_local, "", args, models, 2048)
        await _reply(message, f"**Local ({models.get('local', 'qwen3:8b')})**\n{_truncate(result)}")
    except Exception as e:
        await _reply(message, f"Local error: {e}")
    await message.remove_reaction("\u23f3", client.user)


async def cmd_claude(message: discord.Message, args: str):
    """Send prompt to Claude API."""
    if not args:
        await _reply(message, "Usage: `/claude <prompt>`")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        await _reply(message, "ANTHROPIC_API_KEY not set.")
        return
    await message.add_reaction("\u23f3")
    try:
        from skills._models import _call_claude
        models = config.get("models", {})
        result = await asyncio.to_thread(_call_claude, "", args, models, 2048)
        await _reply(message, f"**Claude**\n{_truncate(result)}")
    except Exception as e:
        await _reply(message, f"Claude error: {e}")
    await message.remove_reaction("\u23f3", client.user)


async def cmd_gemini(message: discord.Message, args: str):
    """Send prompt to Gemini API."""
    if not args:
        await _reply(message, "Usage: `/gemini <prompt>`")
        return
    if not os.environ.get("GEMINI_API_KEY"):
        await _reply(message, "GEMINI_API_KEY not set.")
        return
    await message.add_reaction("\u23f3")
    try:
        from skills._models import _call_gemini
        models = config.get("models", {})
        result = await asyncio.to_thread(_call_gemini, "", args, models, 2048)
        await _reply(message, f"**Gemini**\n{_truncate(result)}")
    except Exception as e:
        await _reply(message, f"Gemini error: {e}")
    await message.remove_reaction("\u23f3", client.user)


async def cmd_aider(message: discord.Message, args: str):
    """Queue a code_write task via aider."""
    if not args:
        await _reply(message, "Usage: `/aider <instructions>`")
        return
    payload = json.dumps({"instructions": args})
    task_id = db.post_task("code_write", payload, priority=7)
    await _reply(message, f"Queued aider task **#{task_id}**. Use `/result {task_id}` to check.")
    # Poll for completion in background
    asyncio.create_task(_poll_and_report(message, task_id))


async def cmd_task(message: discord.Message, args: str):
    """Queue a generic fleet task with auto-routing."""
    if not args:
        await _reply(message, "Usage: `/task <description>`")
        return
    skill = _infer_skill(args)
    payload = json.dumps({"instructions": args, "query": args, "prompt": args})
    task_id = db.post_task(skill, payload, priority=5)
    await _reply(message, f"Queued **{skill}** task **#{task_id}**. Use `/result {task_id}` to check.")


async def cmd_result(message: discord.Message, args: str):
    """Get task result by ID."""
    if not args or not args.strip().isdigit():
        await _reply(message, "Usage: `/result <task_id>`")
        return
    task = db.get_task_result(int(args.strip()))
    if not task:
        await _reply(message, f"Task #{args.strip()} not found.")
        return
    status = task["status"]
    lines = [f"**Task #{task['id']}** — {task['type']} — `{status}`"]
    if task.get("result_json"):
        try:
            result = json.loads(task["result_json"])
            if isinstance(result, dict):
                summary = result.get("summary", result.get("response", json.dumps(result, indent=2)))
            else:
                summary = str(result)
            lines.append(_format_code(str(summary)))
        except Exception:
            lines.append(_format_code(task["result_json"]))
    if task.get("error"):
        lines.append(f"**Error:** {task['error']}")
    await _reply(message, "\n".join(lines))


async def cmd_rag(message: discord.Message, args: str):
    """Search indexed .md files via RAG."""
    if not args:
        await _reply(message, "Usage: `/rag <search query>`")
        return
    await message.add_reaction("\U0001f50d")  # magnifying glass
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from rag import RAGIndex
        idx = RAGIndex()
        chunks = await asyncio.to_thread(idx.search, args, 5)
        if not chunks:
            await _reply(message, f"No results for: **{args}**")
        else:
            lines = [f"**RAG results for:** {args}\n"]
            for i, c in enumerate(chunks, 1):
                lines.append(f"**{i}.** `{c['source']}` > {c['heading']}")
                preview = c['text'][:300].replace('```', '` ` `')
                lines.append(f"```\n{preview}\n```")
            await _reply(message, "\n".join(lines))
    except Exception as e:
        await _reply(message, f"RAG error: {e}")
    await message.remove_reaction("\U0001f50d", client.user)


async def cmd_help(message: discord.Message, _args: str):
    await _reply(message, (
        "**Fleet Bot Commands**\n"
        "`/aider <instructions>` — Code generation via aider\n"
        "`/claude <prompt>` — Claude API\n"
        "`/gemini <prompt>` — Gemini API\n"
        "`/local <prompt>` — Local Ollama\n"
        "`/rag <query>` — Search indexed docs (RAG)\n"
        "`/status` — Fleet status\n"
        "`/task <description>` — Queue a fleet task\n"
        "`/result <id>` — Get task result\n"
        "`/help` — This message\n\n"
        "**Agent Addressing**\n"
        "`biged <request>` — Supervisor (queues fleet task)\n"
        "`lcbiged <prompt>` — Local console (Ollama direct)\n"
        "`clauded <prompt>` — Claude API\n"
        "`gemined <prompt>` — Gemini API\n"
        "_Agents respond with status when unavailable._"
    ))


# ── Skill inference (mirrors lead_client.py) ─────────────────────────────────

SKILL_MAP = {
    "code write": "code_write", "build": "code_write", "aider": "code_write",
    "code review": "code_write_review", "review": "code_write_review",
    "summarize": "summarize", "summary": "summarize",
    "arxiv": "arxiv_fetch", "paper": "arxiv_fetch",
    "search": "web_search", "google": "web_search", "find": "web_search",
    "plan": "plan_workload", "workload": "plan_workload",
    "flashcard": "flashcard", "quiz": "flashcard",
    "lead": "lead_research", "prospect": "lead_research",
    "security": "security_audit", "audit": "security_audit",
    "index": "code_index",
    "fma": "fma_review", "launcher": "fma_review", "fleet manager": "fma_review",
    "rag": "rag_query", "lookup": "rag_query", "context": "rag_query",
    "test skill": "skill_test", "promote": "skill_promote",
    "skill gaps": "skill_learn", "chain": "skill_chain", "pipeline": "skill_chain",
    "branch": "branch_manager", "product": "branch_manager",
    "evolve": "skill_evolve", "benchmark": "benchmark",
    "curriculum": "curriculum_update", "release": "product_release",
    "security review": "security_review", "security scan": "security_review",
    "quality": "code_quality", "lint": "code_quality", "best practices": "code_quality",
    "diffusion": "diffusion", "generate image": "diffusion", "stable diffusion": "diffusion",
    "sd15": "diffusion", "sdxl": "diffusion", "txt2img": "diffusion",
    "ingest": "ingest", "import": "ingest", "import files": "ingest",
}


def _infer_skill(text: str) -> str:
    lower = text.lower()
    for keyword, skill in SKILL_MAP.items():
        if keyword in lower:
            return skill
    return "summarize"  # safe default


# ── Agent addressing system ───────────────────────────────────────────────────
# Each addressable agent: (prefix, display_name, handler, readiness_check, sleep_msg)

def _check_ollama() -> tuple:
    """Check if Ollama is reachable."""
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True, ""
    except Exception:
        return False, "is sleeping... (Ollama not running)"


def _check_claude_key() -> tuple:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, ""
    return False, "is sleeping... (no API key configured)"


def _check_gemini_key() -> tuple:
    if os.environ.get("GEMINI_API_KEY"):
        return True, ""
    return False, "is sleeping... (no API key configured)"


def _check_fleet_online() -> tuple:
    """Check if supervisor is running (agents registered)."""
    try:
        status = db.get_fleet_status()
        agents = status.get("agents", [])
        if agents:
            busy = sum(1 for a in agents if a["status"] == "BUSY")
            idle = sum(1 for a in agents if a["status"] == "IDLE")
            if busy > 0:
                return True, ""
            if idle > 0:
                return True, ""
            return False, "is resting... (agents registered but none active)"
        return False, "is sleeping... (no agents online)"
    except Exception:
        return False, "is sleeping... (fleet database unavailable)"


async def _handle_biged(message, args):
    """Supervisor handler — queues fleet task."""
    payload = json.dumps({"instructions": args, "query": args, "prompt": args})
    skill = _infer_skill(args)
    task_id = db.post_task(skill, payload, priority=7)
    await _reply(message, f"**biged** queued **{skill}** task **#{task_id}**.")
    asyncio.create_task(_poll_and_report(message, task_id))


async def _handle_clauded(message, args):
    """Claude handler — direct API call."""
    await message.add_reaction("\u23f3")
    try:
        from skills._models import _call_claude
        models = config.get("models", {})
        result = await asyncio.to_thread(_call_claude, "", args, models, 2048)
        await _reply(message, f"**clauded**\n{_truncate(result)}")
    except Exception as e:
        await _reply(message, f"**clauded** error: {e}")
    await message.remove_reaction("\u23f3", client.user)


async def _handle_gemined(message, args):
    """Gemini handler — direct API call."""
    await message.add_reaction("\u23f3")
    try:
        from skills._models import _call_gemini
        models = config.get("models", {})
        result = await asyncio.to_thread(_call_gemini, "", args, models, 2048)
        await _reply(message, f"**gemined**\n{_truncate(result)}")
    except Exception as e:
        await _reply(message, f"**gemined** error: {e}")
    await message.remove_reaction("\u23f3", client.user)


async def _handle_lcbiged(message, args):
    """Local console handler — Ollama direct."""
    await cmd_local(message, args)


# Agent registry: (prefix, name, handler, readiness_fn)
_AGENTS = [
    ("biged ",   "biged",   _handle_biged,   _check_fleet_online),
    ("lcbiged ", "lcbiged", _handle_lcbiged, _check_ollama),
    ("clauded ", "clauded", _handle_clauded, _check_claude_key),
    ("gemined ", "gemined", _handle_gemined, _check_gemini_key),
]


def _match_agent(lower: str):
    """Match message start against registered agent names.
    Returns (name, prefix_len, handler, ready_check, None) or None.
    """
    for prefix, name, handler, check in _AGENTS:
        if lower.startswith(prefix) or lower == prefix.strip():
            return name, len(prefix), handler, check, None
    return None


# ── Background polling ───────────────────────────────────────────────────────

async def _poll_and_report(message: discord.Message, task_id: int, timeout: int = 660):
    """Poll for task completion and post result back to Discord."""
    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(10)
        task = db.get_task_result(task_id)
        if not task:
            continue
        if task["status"] in ("DONE", "FAILED"):
            if task["status"] == "DONE":
                try:
                    result = json.loads(task["result_json"])
                    summary = result.get("summary", str(result)[:800])
                    diff = result.get("diff", "")
                    text = f"Task **#{task_id}** complete.\n**Summary:** {summary}"
                    if diff:
                        text += f"\n{_format_code(diff[:1200], 'diff')}"
                    await _reply(message, text)
                except Exception:
                    await _reply(message, f"Task **#{task_id}** done.\n{_format_code(task['result_json'])}")
            else:
                await _reply(message, f"Task **#{task_id}** failed: {task.get('error', 'unknown')}")
            return
    await _reply(message, f"Task **#{task_id}** still running after {timeout}s. Use `/result {task_id}` to check later.")


# ── Command dispatch ─────────────────────────────────────────────────────────

COMMANDS = {
    "/aider": cmd_aider,
    "/claude": cmd_claude,
    "/gemini": cmd_gemini,
    "/local": cmd_local,
    "/status": cmd_status,
    "/task": cmd_task,
    "/result": cmd_result,
    "/rag": cmd_rag,
    "/help": cmd_help,
}


@client.event
async def on_ready():
    log.info(f"Bot connected as {client.user} — watching channel {CHANNEL_ID}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("Fleet bot online.")


@client.event
async def on_message(message: discord.Message):
    # Ignore own messages and wrong channels
    if message.author == client.user:
        return
    if message.channel.id != CHANNEL_ID:
        return

    from skills._security import sanitize_discord_content
    content = sanitize_discord_content(message.content.strip())
    if not content:
        return

    lower = content.lower()

    # ── Addressable agent names ──────────────────────────────────────────────
    # Each agent has a Discord handle, a readiness check, and a handler.
    agent_hit = _match_agent(lower)
    if agent_hit:
        name, prefix_len, handler, ready_check, sleep_msg = agent_hit
        args = content[prefix_len:].strip()
        if not args:
            await _reply(message, f"**{name}** is listening. What do you need?")
            return
        ready, reason = ready_check()
        if not ready:
            await _reply(message, f"**{name}** {reason}")
            return
        log.info(f"{message.author}: {name} {args[:80]}")
        await handler(message, args)
        return

    # ── Slash commands ───────────────────────────────────────────────────────
    for prefix, handler in COMMANDS.items():
        if lower.startswith(prefix):
            args = content[len(prefix):].strip()
            log.info(f"{message.author}: {prefix} {args[:80]}")
            await handler(message, args)
            return

    # No command prefix — treat as /local by default
    log.info(f"{message.author}: (default->local) {content[:80]}")
    await cmd_local(message, content)


# ── Entry ────────────────────────────────────────────────────────────────────

def main():
    global config
    if not TOKEN:
        log.error("DISCORD_BOT_TOKEN not set — exiting")
        sys.exit(1)

    db.init_db()
    config = load_config()
    (FLEET_DIR / "logs").mkdir(exist_ok=True)

    log.info("Starting Discord bot...")
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
