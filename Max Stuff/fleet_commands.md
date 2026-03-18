# Fleet Command Reference

> All commands run from WSL Ubuntu in the fleet directory:
> `cd /mnt/c/Users/max/Projects/Education/fleet`

---

## Start / Stop

| Command | What it does |
|---------|-------------|
| `mkdir -p logs knowledge/summaries knowledge/reports && nohup ~/.local/bin/uv run python supervisor.py >> logs/supervisor.log 2>&1 & echo "PID: $!"` | Start fleet in background — mkdir first or redirect fails |
| `nohup ~/.local/bin/uv run python dispatch_marathon.py >> logs/marathon.log 2>&1 & echo "PID: $!"` | Start 8-hour discussion + synthesis marathon |
| `pkill -f supervisor.py` | Stop all fleet workers + supervisor |
| `kill <PID>` | Stop a specific process by PID |
| `source ~/.secrets` | Load API keys into shell before starting fleet |

---

## Fleet Status

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py status` | Show all agents (IDLE/BUSY), task counts (pending/running/done/failed) |
| `cat STATUS.md` | Last status snapshot (auto-updated every 30s by supervisor) |
| `sqlite3 fleet.db "SELECT id,type,status,assigned_to FROM tasks ORDER BY id DESC LIMIT 20"` | Raw task queue view |
| `sqlite3 fleet.db "SELECT from_agent,body_json FROM messages ORDER BY id DESC LIMIT 10"` | Recent inter-agent messages |

---

## Dispatch Tasks

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py task "summarize <url or topic>"` | Summarize a URL or topic via Ollama |
| `uv run python lead_client.py task "search <query>"` | Web search (Brave→Tavily→Jina→DDG waterfall) |
| `uv run python lead_client.py task "arxiv 2501.00001"` | Fetch and summarize an arxiv paper |
| `uv run python lead_client.py task "analyze training results"` | Analyze autoresearch results.tsv |
| `uv run python lead_client.py task "leads healthcare 95076"` | Research local business leads by industry + zip |
| `uv run python lead_client.py task "discuss <topic>" --wait` | One discussion round from all agents |
| `uv run python lead_client.py task "synthesize business pitch"` | Sonnet synthesis into business document |
| `uv run python lead_client.py task "audit"` | Security audit of fleet files + credentials |
| `uv run python lead_client.py task "pen_test"` | Local network scan (auto-detects network range) |
| `uv run python lead_client.py task "pen_test 192.168.1.0/24 full"` | Full port scan on specific subnet |
| `uv run python lead_client.py task "security_apply <8-char-id>"` | Apply an approved security advisory |
| `uv run python lead_client.py task "<any>" --wait` | Block and print result when done |

### Assign to specific agent

```bash
# From Python/db directly — bypass infer_skill
uv run python -c "
import sys; sys.path.insert(0,'.');
import db, json; db.init_db()
db.post_task('pen_test', json.dumps({'target':'auto','scan_type':'service'}), priority=8, assigned_to='security')
"
```

---

## Read Results

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py result <task_id>` | Print result JSON for a completed task |
| `cat knowledge/security/pending/advisory_<id>.md` | Read a pending security advisory |
| `ls knowledge/security/pending/` | List all pending advisories awaiting approval |
| `ls knowledge/security/pen_tests/` | List all pen test reports |
| `cat knowledge/discussion/local_AI_services_*` | Read discussion round outputs |
| `cat knowledge/reports/*.md` | Read Sonnet synthesis reports |
| `ls knowledge/leads/` | List discovered business leads |

---

## Logs

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py logs <agent> --tail 30` | Tail last 30 lines from an agent's log |
| `tail -f logs/supervisor.log` | Live supervisor log stream |
| `tail -f logs/security.log` | Live security agent log |
| `tail -f logs/marathon.log` | Live marathon progress |
| `tail -f logs/<agent>.log` | Any agent: researcher, coder, archivist, analyst, sales, onboarding, implementation, security |

---

## Send Direct Messages

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py send <agent> "<message>"` | Send a free-text instruction to a specific agent |
| `uv run python lead_client.py send security "audit fleet configs"` | Direct the security agent manually |
| `uv run python lead_client.py send researcher "find papers on HIPAA AI compliance"` | Direct researcher |

---

## Security Workflow

```
1. Security agent runs audit/pen_test on idle schedule automatically
2. Advisory appears in knowledge/security/pending/advisory_<id>.md
3. Review: cat knowledge/security/pending/advisory_<id>.md
4. Approve automated fixes: uv run python lead_client.py task "security_apply <id>" --wait
5. Apply report saved to: knowledge/security/applied/apply_report_<id>.md
6. Manual items (credential rotation, code changes) listed in apply report
```

---

## UniFi Network Commands

| Command | What it does |
|---------|-------------|
| `uv run python lead_client.py task "pen_test 192.168.1.1 service"` | Scan UniFi controller/gateway |
| `uv run python lead_client.py task "search UniFi <model> default credentials CVE"` | Research known vulns for a device |
| `uv run python lead_client.py task "summarize UniFi network hardening best practices"` | Get hardening guide via Ollama |

### UniFi default ports to watch in pen_test results

| Port | Service |
|------|---------|
| 8080 | UniFi controller HTTP (redirect to 8443) |
| 8443 | UniFi controller HTTPS admin UI |
| 8880 | UniFi guest portal HTTP |
| 8843 | UniFi guest portal HTTPS |
| 3478 | STUN / device adoption UDP |
| 10001 | UniFi device discovery UDP |
| 6789 | UniFi throughput test |
| 27117 | UniFi embedded MongoDB (should never be exposed externally) |

---

## Agents Reference

| Agent | Role | Skill types it handles |
|-------|------|------------------------|
| researcher | Web search, arxiv, market research | web_search, arxiv_fetch, summarize |
| coder | Code indexing, Python docs | code_index, summarize |
| archivist | Synthesis, flashcards, reports | flashcard, synthesize, summarize |
| analyst | Training run analysis | analyze_results, review_discards |
| sales | Local lead research, outreach | lead_research, discuss, summarize |
| onboarding | Client onboarding docs | discuss, summarize |
| implementation | Local AI deployment specs | discuss, summarize |
| security | Security audits, pen tests | security_audit, security_apply, pen_test |

---

## Quick Diagnostics

```bash
# Is Ollama running?
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# Which models are loaded?
ollama list

# Is training running (affects Ollama GPU mode)?
ps aux | grep train.py

# How many tasks failed?
sqlite3 fleet.db "SELECT type, error FROM tasks WHERE status='FAILED' ORDER BY id DESC LIMIT 10"

# Check a worker restarted too many times?
grep "died" logs/supervisor.log | tail -20
```
