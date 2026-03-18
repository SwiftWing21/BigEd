#!/usr/bin/env python3
"""
8-hour discussion marathon orchestrator.
Waits for current research to finish, then dispatches structured discussion
rounds across all agents, lead research, and final Sonnet synthesis.

Usage: uv run python dispatch_marathon.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db

db.init_db()

TOPIC = "local AI services business opportunity Watsonville CA"
ZIPS = ["95076", "95003", "95010", "95019", "95060", "95062", "95065", "95066", "95073"]
INDUSTRIES = ["healthcare", "accounting", "legal"]

AGENTS = {
    "researcher":       "market research analyst identifying opportunities and threats",
    "analyst":          "data analyst evaluating ROI, pricing, and revenue potential",
    "archivist":        "strategic organizer synthesizing findings into actionable insights",
    "sales":            "sales strategist focused on SMB outreach and revenue generation",
    "onboarding":       "client onboarding specialist focused on smooth implementation",
    "implementation":   "technical implementation specialist focused on deployment and reliability",
    "security":         "security advisor identifying risks and compliance requirements for local AI deployments",
}


def wait_for_idle(timeout_mins=30):
    print(f"Waiting for current tasks to finish (max {timeout_mins}min)...")
    deadline = time.time() + timeout_mins * 60
    while time.time() < deadline:
        status = db.get_fleet_status()
        pending = status["tasks"]["PENDING"]
        running = status["tasks"]["RUNNING"]
        if pending == 0 and running == 0:
            print(f"  Queue clear. Done={status['tasks']['DONE']} Failed={status['tasks']['FAILED']}")
            return True
        print(f"  pending={pending} running={running} — waiting...")
        time.sleep(15)
    print("Timeout — proceeding anyway.")
    return False


def dispatch_discussion_round(round_num, priority=7):
    print(f"\n--- Dispatching discussion round {round_num} ---")
    for agent_name, perspective in AGENTS.items():
        task_id = db.post_task(
            "discuss",
            json.dumps({
                "agent_name": agent_name,
                "topic": TOPIC,
                "role_perspective": perspective,
                "round": round_num,
            }),
            priority=priority,
            assigned_to=agent_name,
        )
        print(f"  Task {task_id} → {agent_name} (round {round_num})")


def dispatch_lead_research(priority=6):
    print("\n--- Dispatching lead research ---")
    cities = {
        "95076": "Watsonville CA",
        "95003": "Aptos CA",
        "95010": "Capitola CA",
        "95019": "Freedom CA",
        "95060": "Santa Cruz CA",
        "95062": "Santa Cruz CA",
        "95065": "Santa Cruz CA",
        "95066": "Scotts Valley CA",
        "95073": "Soquel CA",
    }
    for zip_code, city in cities.items():
        for industry in INDUSTRIES:
            task_id = db.post_task(
                "lead_research",
                json.dumps({"industry": industry, "zip_code": zip_code, "city": city}),
                priority=priority,
                assigned_to="sales",
            )
            print(f"  Task {task_id} → sales: {industry} in {city} {zip_code}")


def dispatch_synthesis(priority=5):
    print("\n--- Dispatching synthesis tasks ---")

    # Business pitch
    task_id = db.post_task(
        "synthesize",
        json.dumps({
            "doc_type": "business_pitch",
            "topic": TOPIC,
            "output_name": "business_pitch_watsonville_ai",
        }),
        priority=priority,
        assigned_to="archivist",
    )
    print(f"  Task {task_id} → archivist: business pitch")

    # Sales agent prep doc
    task_id = db.post_task(
        "synthesize",
        json.dumps({
            "doc_type": "agent_prep",
            "agent_role": "sales",
            "topic": TOPIC,
            "output_name": "sales_agent_prep",
        }),
        priority=priority,
        assigned_to="archivist",
    )
    print(f"  Task {task_id} → archivist: sales agent prep")

    # Onboarding agent prep doc
    task_id = db.post_task(
        "synthesize",
        json.dumps({
            "doc_type": "agent_prep",
            "agent_role": "onboarding",
            "topic": TOPIC,
            "output_name": "onboarding_agent_prep",
        }),
        priority=priority,
        assigned_to="archivist",
    )
    print(f"  Task {task_id} → archivist: onboarding agent prep")

    # Implementation agent prep doc
    task_id = db.post_task(
        "synthesize",
        json.dumps({
            "doc_type": "agent_prep",
            "agent_role": "implementation",
            "topic": TOPIC,
            "output_name": "implementation_agent_prep",
        }),
        priority=priority,
        assigned_to="archivist",
    )
    print(f"  Task {task_id} → archivist: implementation agent prep")


def main():
    print("=" * 60)
    print("MARATHON: 8-hour discussion + synthesis session")
    print(f"Topic: {TOPIC}")
    print("=" * 60)

    # Phase 1: Wait for current research
    wait_for_idle(timeout_mins=30)

    # Phase 2: Discussion rounds (spread over ~6 hours, 8 rounds)
    for round_num in range(1, 9):
        dispatch_discussion_round(round_num, priority=8)
        print(f"  Sleeping 40min before round {round_num + 1}...")
        if round_num < 8:
            time.sleep(40 * 60)  # 40 min between rounds

    # Phase 3: Lead research (runs in parallel during discussion via sales agent)
    dispatch_lead_research(priority=6)

    # Phase 4: Synthesis (after all discussion + lead research)
    print("\nWaiting for discussion + leads to finish before synthesis...")
    wait_for_idle(timeout_mins=60)
    dispatch_synthesis(priority=5)

    print("\n✓ Marathon dispatched. Monitor with: uv run python lead_client.py status")
    print("  Final reports will appear in knowledge/reports/")


if __name__ == "__main__":
    main()
