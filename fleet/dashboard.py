#!/usr/bin/env python3
"""
Fleet Dashboard — localhost web UI for activity tracking and metrics.

Serves charts, tables, and live stats pulled from fleet.db and knowledge/.
Run standalone or as a supervisor-managed subprocess.

Usage:
    python dashboard.py                # http://localhost:5555
    python dashboard.py --port 8080    # custom port
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, Response

FLEET_DIR = Path(__file__).parent
DB_PATH = FLEET_DIR / "fleet.db"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

app = Flask(__name__)


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── API endpoints ────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    agents = query("SELECT name, role, status, last_heartbeat FROM agents ORDER BY name")
    counts = {}
    for s in ("PENDING", "RUNNING", "DONE", "FAILED"):
        row = query("SELECT COUNT(*) as n FROM tasks WHERE status=?", (s,))
        counts[s] = row[0]["n"] if row else 0
    return jsonify({"agents": agents, "tasks": counts})


@app.route("/api/activity")
def api_activity():
    """Task completions per day for the last 30 days."""
    rows = query("""
        SELECT date(created_at) as day, status, COUNT(*) as n
        FROM tasks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY day, status
        ORDER BY day
    """)
    # Pivot into {day: {DONE: n, FAILED: n, ...}}
    days = defaultdict(lambda: {"DONE": 0, "FAILED": 0, "PENDING": 0, "RUNNING": 0})
    for r in rows:
        if r["day"]:
            days[r["day"]][r["status"]] = r["n"]
    # Fill gaps
    result = []
    today = datetime.utcnow().date()
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append({"day": d, **days[d]})
    return jsonify(result)


@app.route("/api/skills")
def api_skills():
    """Task counts by skill type."""
    rows = query("""
        SELECT type, status, COUNT(*) as n
        FROM tasks
        GROUP BY type, status
        ORDER BY type
    """)
    skills = defaultdict(lambda: {"DONE": 0, "FAILED": 0, "PENDING": 0, "RUNNING": 0, "total": 0})
    for r in rows:
        skills[r["type"]][r["status"]] = r["n"]
        skills[r["type"]]["total"] += r["n"]
    return jsonify(dict(skills))


@app.route("/api/discussions")
def api_discussions():
    """Discussion/meeting summary from messages table."""
    rows = query("""
        SELECT from_agent, body_json, created_at
        FROM messages
        WHERE body_json IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 200
    """)
    topics = defaultdict(lambda: {"agents": set(), "rounds": set(), "count": 0, "last": ""})
    for r in rows:
        try:
            body = json.loads(r["body_json"])
            topic = body.get("topic", "unknown")
            topics[topic]["agents"].add(r["from_agent"])
            topics[topic]["rounds"].add(body.get("round", 1))
            topics[topic]["count"] += 1
            if not topics[topic]["last"] or r["created_at"] > topics[topic]["last"]:
                topics[topic]["last"] = r["created_at"]
        except Exception:
            pass
    result = []
    for topic, data in sorted(topics.items(), key=lambda x: x[1]["last"], reverse=True):
        result.append({
            "topic": topic,
            "agents": sorted(data["agents"]),
            "rounds": max(data["rounds"]) if data["rounds"] else 0,
            "contributions": data["count"],
            "last_activity": data["last"],
        })
    return jsonify(result)


@app.route("/api/knowledge")
def api_knowledge():
    """Files created in knowledge/ grouped by category."""
    categories = {}
    if not KNOWLEDGE_DIR.exists():
        return jsonify(categories)
    for subdir in sorted(KNOWLEDGE_DIR.iterdir()):
        if subdir.is_dir():
            files = list(subdir.rglob("*"))
            file_list = [
                {"name": str(f.relative_to(KNOWLEDGE_DIR)), "size": f.stat().st_size,
                 "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()}
                for f in files if f.is_file()
            ]
            categories[subdir.name] = {
                "count": len(file_list),
                "files": sorted(file_list, key=lambda x: x["modified"], reverse=True)[:20],
            }
        elif subdir.is_file():
            categories[subdir.name] = {
                "count": 1,
                "files": [{"name": subdir.name, "size": subdir.stat().st_size,
                           "modified": datetime.fromtimestamp(subdir.stat().st_mtime).isoformat()}],
            }
    return jsonify(categories)


@app.route("/api/code_stats")
def api_code_stats():
    """Lines of code stats from code_writes workspace git log."""
    workspace = KNOWLEDGE_DIR / "code_writes" / "workspace"
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return jsonify({"commits": 0, "lines_added": 0, "lines_deleted": 0, "files_changed": 0})

    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--numstat", "--pretty=format:"],
            cwd=str(workspace), capture_output=True, text=True, timeout=10,
        )
        added = 0
        deleted = 0
        files = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3:
                try:
                    a, d = int(parts[0]), int(parts[1])
                    added += a
                    deleted += d
                    files.add(parts[2])
                except ValueError:
                    pass

        commits = subprocess.run(
            ["git", "rev-list", "--count", "--all"],
            cwd=str(workspace), capture_output=True, text=True, timeout=5,
        )
        commit_count = int(commits.stdout.strip()) if commits.returncode == 0 else 0
    except Exception:
        return jsonify({"commits": 0, "lines_added": 0, "lines_deleted": 0, "files_changed": 0})

    return jsonify({
        "commits": commit_count,
        "lines_added": added,
        "lines_deleted": deleted,
        "files_changed": len(files),
    })


@app.route("/api/reviews")
def api_reviews():
    """FMA and code review summaries."""
    reviews = []
    for review_dir in [KNOWLEDGE_DIR / "code_reviews", KNOWLEDGE_DIR / "fma_reviews"]:
        if not review_dir.exists():
            continue
        for f in sorted(review_dir.glob("*_review_*.md"), reverse=True)[:30]:
            try:
                content = f.read_text(errors="ignore")
                # Extract header info
                lines = content.splitlines()[:6]
                reviews.append({
                    "file": f.name,
                    "category": review_dir.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "header": "\n".join(lines),
                })
            except Exception:
                pass
    return jsonify(reviews)


@app.route("/api/timeline")
def api_timeline():
    """Recent events across all sources for the activity feed."""
    events = []

    # Tasks completed/failed in last 7 days
    for row in query("""
        SELECT id, type, status, assigned_to, created_at
        FROM tasks WHERE status IN ('DONE','FAILED')
        AND created_at >= date('now','-7 days')
        ORDER BY created_at DESC LIMIT 50
    """):
        events.append({
            "time": row["created_at"],
            "type": "task",
            "detail": f"Task #{row['id']} ({row['type']}) → {row['status']}",
            "agent": row["assigned_to"] or "",
            "status": row["status"],
        })

    # Recent messages
    for row in query("""
        SELECT from_agent, body_json, created_at
        FROM messages WHERE created_at >= date('now','-7 days')
        ORDER BY created_at DESC LIMIT 30
    """):
        try:
            body = json.loads(row["body_json"])
            topic = body.get("topic", "message")
        except Exception:
            topic = "message"
        events.append({
            "time": row["created_at"],
            "type": "discussion",
            "detail": f"Discussion: {topic}",
            "agent": row["from_agent"],
            "status": "INFO",
        })

    events.sort(key=lambda x: x.get("time", ""), reverse=True)
    return jsonify(events[:80])


@app.route("/api/rag")
def api_rag():
    """RAG index statistics."""
    rag_db = FLEET_DIR / "rag.db"
    if not rag_db.exists():
        return jsonify({"files": 0, "chunks": 0, "sources": []})
    try:
        conn = sqlite3.connect(rag_db, timeout=5)
        conn.row_factory = sqlite3.Row
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
        sources = [
            dict(r) for r in conn.execute(
                "SELECT path, chunks, indexed FROM files ORDER BY indexed DESC LIMIT 30"
            ).fetchall()
        ]
        conn.close()
        return jsonify({"files": files, "chunks": chunks, "sources": sources})
    except Exception as e:
        return jsonify({"error": str(e), "files": 0, "chunks": 0, "sources": []})


# ── Main page ────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #111; --bg2: #1a1a1a; --bg3: #242424; --border: #333;
    --text: #e2e2e2; --dim: #888; --accent: #b22222; --gold: #c8a84b;
    --green: #4caf50; --red: #f44336; --orange: #ff9800; --blue: #42a5f5;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; }
  a { color: var(--gold); text-decoration: none; }

  .header {
    background: var(--bg3); padding: 16px 24px; border-bottom: 2px solid var(--accent);
    display: flex; align-items: center; gap: 12px;
  }
  .header h1 { font-size: 20px; color: var(--gold); }
  .header .status { margin-left: auto; font-size: 13px; color: var(--dim); }

  .grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 16px; padding: 16px; max-width: 1600px; margin: 0 auto;
  }

  .card {
    background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; min-height: 200px;
  }
  .card h2 { font-size: 14px; color: var(--gold); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
  .card.wide { grid-column: span 2; }

  .stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
  .stat {
    background: var(--bg3); border-radius: 6px; padding: 12px 16px; flex: 1; min-width: 120px; text-align: center;
  }
  .stat .value { font-size: 28px; font-weight: bold; }
  .stat .label { font-size: 11px; color: var(--dim); margin-top: 4px; }
  .stat.green .value { color: var(--green); }
  .stat.red .value { color: var(--red); }
  .stat.gold .value { color: var(--gold); }
  .stat.blue .value { color: var(--blue); }
  .stat.orange .value { color: var(--orange); }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--dim); font-weight: normal; padding: 6px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid var(--bg3); }
  tr:hover td { background: var(--bg3); }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;
  }
  .badge-done { background: #1b3d1b; color: var(--green); }
  .badge-failed { background: #3d1b1b; color: var(--red); }
  .badge-running { background: #3d2e0e; color: var(--orange); }
  .badge-pending { background: #1b2a3d; color: var(--blue); }
  .badge-idle { background: #1b2a3d; color: var(--blue); }
  .badge-busy { background: #3d2e0e; color: var(--orange); }
  .badge-offline { background: #2d2d2d; color: var(--dim); }
  .badge-info { background: #1b2a3d; color: var(--blue); }

  .chart-container { position: relative; height: 260px; }
  .chart-container-sm { position: relative; height: 200px; }

  .timeline { max-height: 400px; overflow-y: auto; }
  .timeline-item {
    display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--bg3);
    font-size: 12px;
  }
  .timeline-item .time { color: var(--dim); min-width: 60px; }
  .timeline-item .agent { color: var(--gold); min-width: 80px; }

  .file-list { max-height: 300px; overflow-y: auto; font-size: 12px; }
  .file-item { padding: 4px 0; border-bottom: 1px solid var(--bg3); display: flex; justify-content: space-between; }
  .file-item .name { color: var(--text); }
  .file-item .meta { color: var(--dim); }

  .refresh-btn {
    background: var(--bg3); border: 1px solid var(--border); color: var(--dim);
    padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  .refresh-btn:hover { color: var(--text); border-color: var(--gold); }

  @media (max-width: 800px) {
    .grid { grid-template-columns: 1fr; }
    .card.wide { grid-column: span 1; }
  }
</style>
</head>
<body>

<div class="header">
  <span style="font-size:24px">&#x1f9f1;</span>
  <h1>FLEET DASHBOARD</h1>
  <div class="status">
    <button class="refresh-btn" onclick="loadAll()">Refresh</button>
    <span id="lastUpdate" style="margin-left:8px"></span>
  </div>
</div>

<div class="grid">
  <!-- Task Stats -->
  <div class="card">
    <h2>Task Summary</h2>
    <div class="stat-row" id="taskStats"></div>
  </div>

  <!-- Code Stats -->
  <div class="card">
    <h2>Code Output</h2>
    <div class="stat-row" id="codeStats"></div>
  </div>

  <!-- Agent Status -->
  <div class="card">
    <h2>Agents</h2>
    <table><thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Last Seen</th></tr></thead>
    <tbody id="agentTable"></tbody></table>
  </div>

  <!-- Activity Chart -->
  <div class="card">
    <h2>Activity — Last 30 Days</h2>
    <div class="chart-container"><canvas id="activityChart"></canvas></div>
  </div>

  <!-- Skills Breakdown -->
  <div class="card">
    <h2>Skills Used</h2>
    <div class="chart-container"><canvas id="skillsChart"></canvas></div>
  </div>

  <!-- Discussions -->
  <div class="card">
    <h2>Discussions / Meetings</h2>
    <table><thead><tr><th>Topic</th><th>Agents</th><th>Rounds</th><th>Posts</th></tr></thead>
    <tbody id="discussionTable"></tbody></table>
  </div>

  <!-- Reviews -->
  <div class="card">
    <h2>Code Reviews</h2>
    <div class="file-list" id="reviewList"></div>
  </div>

  <!-- Knowledge Files -->
  <div class="card">
    <h2>Knowledge Base</h2>
    <div class="stat-row" id="knowledgeStats"></div>
    <div class="file-list" id="knowledgeList"></div>
  </div>

  <!-- RAG Index -->
  <div class="card">
    <h2>RAG Index</h2>
    <div class="stat-row" id="ragStats"></div>
    <div class="file-list" id="ragSources"></div>
  </div>

  <!-- Timeline -->
  <div class="card wide">
    <h2>Recent Activity</h2>
    <div class="timeline" id="timeline"></div>
  </div>
</div>

<script>
let activityChart = null;
let skillsChart = null;

async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function badge(status) {
  const s = (status || '').toLowerCase();
  return `<span class="badge badge-${s}">${status}</span>`;
}

function timeAgo(dateStr) {
  if (!dateStr) return 'never';
  const d = new Date(dateStr + 'Z');
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

function shortTime(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'Z');
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}

async function loadStatus() {
  const data = await fetchJSON('/api/status');
  const t = data.tasks;
  const total = t.DONE + t.FAILED + t.PENDING + t.RUNNING;
  document.getElementById('taskStats').innerHTML = `
    <div class="stat green"><div class="value">${t.DONE}</div><div class="label">Done</div></div>
    <div class="stat red"><div class="value">${t.FAILED}</div><div class="label">Failed</div></div>
    <div class="stat orange"><div class="value">${t.RUNNING}</div><div class="label">Running</div></div>
    <div class="stat blue"><div class="value">${t.PENDING}</div><div class="label">Pending</div></div>
    <div class="stat gold"><div class="value">${total}</div><div class="label">Total</div></div>
  `;
  document.getElementById('agentTable').innerHTML = data.agents.map(a => `
    <tr><td>${a.name}</td><td style="color:var(--dim)">${a.role}</td>
    <td>${badge(a.status)}</td><td style="color:var(--dim)">${timeAgo(a.last_heartbeat)}</td></tr>
  `).join('');
}

async function loadCodeStats() {
  const data = await fetchJSON('/api/code_stats');
  document.getElementById('codeStats').innerHTML = `
    <div class="stat green"><div class="value">${data.lines_added}</div><div class="label">Lines Added</div></div>
    <div class="stat red"><div class="value">${data.lines_deleted}</div><div class="label">Lines Deleted</div></div>
    <div class="stat blue"><div class="value">${data.files_changed}</div><div class="label">Files Changed</div></div>
    <div class="stat gold"><div class="value">${data.commits}</div><div class="label">Commits</div></div>
  `;
}

async function loadActivity() {
  const data = await fetchJSON('/api/activity');
  const labels = data.map(d => d.day.slice(5));
  const done = data.map(d => d.DONE);
  const failed = data.map(d => d.FAILED);

  if (activityChart) activityChart.destroy();
  activityChart = new Chart(document.getElementById('activityChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Done', data: done, backgroundColor: '#4caf50', borderRadius: 3 },
        { label: 'Failed', data: failed, backgroundColor: '#f44336', borderRadius: 3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#888', font: { size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#666', font: { size: 10 } }, grid: { color: '#222' } },
        y: { stacked: true, ticks: { color: '#666' }, grid: { color: '#222' } },
      }
    }
  });
}

async function loadSkills() {
  const data = await fetchJSON('/api/skills');
  const entries = Object.entries(data).sort((a,b) => b[1].total - a[1].total);
  const labels = entries.map(e => e[0]);
  const done = entries.map(e => e[1].DONE);
  const failed = entries.map(e => e[1].FAILED);

  if (skillsChart) skillsChart.destroy();
  skillsChart = new Chart(document.getElementById('skillsChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Done', data: done, backgroundColor: '#4caf50' },
        { label: 'Failed', data: failed, backgroundColor: '#f44336' },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#888', font: { size: 11 } } } },
      scales: {
        x: { stacked: true, ticks: { color: '#666' }, grid: { color: '#222' } },
        y: { stacked: true, ticks: { color: '#aaa', font: { size: 11 } }, grid: { display: false } },
      }
    }
  });
}

async function loadDiscussions() {
  const data = await fetchJSON('/api/discussions');
  document.getElementById('discussionTable').innerHTML = data.slice(0, 15).map(d => `
    <tr><td>${d.topic}</td><td style="color:var(--dim)">${d.agents.join(', ')}</td>
    <td>${d.rounds}</td><td>${d.contributions}</td></tr>
  `).join('') || '<tr><td colspan="4" style="color:var(--dim)">No discussions yet</td></tr>';
}

async function loadReviews() {
  const data = await fetchJSON('/api/reviews');
  document.getElementById('reviewList').innerHTML = data.slice(0, 20).map(r => `
    <div class="file-item">
      <span class="name">${r.file}</span>
      <span class="meta">${r.category} · ${timeAgo(r.modified)}</span>
    </div>
  `).join('') || '<div style="color:var(--dim)">No reviews yet</div>';
}

async function loadKnowledge() {
  const data = await fetchJSON('/api/knowledge');
  const entries = Object.entries(data);
  const totalFiles = entries.reduce((s, [,v]) => s + v.count, 0);

  document.getElementById('knowledgeStats').innerHTML = `
    <div class="stat gold"><div class="value">${totalFiles}</div><div class="label">Total Files</div></div>
    <div class="stat blue"><div class="value">${entries.length}</div><div class="label">Categories</div></div>
  `;

  document.getElementById('knowledgeList').innerHTML = entries
    .sort((a,b) => b[1].count - a[1].count)
    .map(([cat, v]) => `
      <div class="file-item">
        <span class="name">${cat}/</span>
        <span class="meta">${v.count} files</span>
      </div>
    `).join('');
}

async function loadRAG() {
  const data = await fetchJSON('/api/rag');
  document.getElementById('ragStats').innerHTML = `
    <div class="stat gold"><div class="value">${data.files}</div><div class="label">Files Indexed</div></div>
    <div class="stat blue"><div class="value">${data.chunks}</div><div class="label">Chunks</div></div>
  `;
  document.getElementById('ragSources').innerHTML = (data.sources || []).slice(0, 15).map(s => `
    <div class="file-item">
      <span class="name">${s.path}</span>
      <span class="meta">${s.chunks} chunks</span>
    </div>
  `).join('') || '<div style="color:var(--dim)">Not indexed yet — run rag_index skill</div>';
}

async function loadTimeline() {
  const data = await fetchJSON('/api/timeline');
  document.getElementById('timeline').innerHTML = data.map(e => `
    <div class="timeline-item">
      <span class="time">${shortTime(e.time)}</span>
      <span class="agent">${e.agent}</span>
      <span>${badge(e.status)} ${e.detail}</span>
    </div>
  `).join('') || '<div style="color:var(--dim);padding:12px">No recent activity</div>';
}

async function loadAll() {
  await Promise.all([
    loadStatus(), loadCodeStats(), loadActivity(), loadSkills(),
    loadDiscussions(), loadReviews(), loadKnowledge(), loadRAG(), loadTimeline(),
  ]);
  document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

loadAll();
setInterval(loadAll, 30000);  // auto-refresh every 30s
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


# ── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Fleet Dashboard: http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
