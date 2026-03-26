"""Analyze task prompts to identify patterns that correlate with success or failure.

Compares prompt characteristics (length, structure, specificity) across
successful vs failed tasks, generates optimization recommendations,
and saves a report to knowledge/prompt_optimization/.
"""

import logging
SKILL_NAME = "prompt_optimize"
DESCRIPTION = "Analyze task prompt patterns and recommend improvements for higher success rates."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = ""
log = logging.getLogger(SKILL_NAME)


def run(payload, config):
    import db
    import json
    import re
    from collections import Counter
    from datetime import date
    from skills._knowledge import get_output_dir
    from skills._report import ReportBuilder

    OUT_DIR = get_output_dir("prompt_optimization")

    hours = payload.get("hours", 48)
    min_samples = payload.get("min_samples", 5)

    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT type, status, payload_json, intelligence_score
            FROM tasks
            WHERE created_at >= datetime('now', ? || ' hours')
              AND type IS NOT NULL
              AND payload_json IS NOT NULL
              AND status IN ('DONE', 'FAILED')
        """, (f"-{hours}",)).fetchall()

    if not rows:
        return {"status": "skip", "message": "No completed tasks to analyze"}

    # --- Extract prompt text from payload_json ---
    def _extract_prompt(payload_str):
        try:
            p = json.loads(payload_str)
            # Common payload shapes: {"task": "..."}, {"prompt": "..."}, plain string
            if isinstance(p, str):
                return p
            for key in ("task", "prompt", "instruction", "query", "input"):
                if key in p and isinstance(p[key], str):
                    return p[key]
            # Fallback: serialize payload as string
            return payload_str
        except (json.JSONDecodeError, TypeError):
            return payload_str or ""

    # --- Prompt feature extraction ---
    def _features(text):
        words = text.split()
        return {
            "length": len(text),
            "word_count": len(words),
            "has_newlines": "\n" in text,
            "has_bullet_list": bool(re.search(r"^[\-\*\d]\s", text, re.MULTILINE)),
            "has_quotes": '"' in text or "'" in text,
            "has_path": "/" in text or "\\" in text,
            "has_code_block": "```" in text,
            "question_marks": text.count("?"),
            "sentence_count": max(1, len(re.findall(r"[.!?]+", text))),
            "avg_word_len": round(sum(len(w) for w in words) / max(len(words), 1), 1),
        }

    # --- Group by skill ---
    skill_data = {}
    for r in rows:
        skill = r["type"]
        prompt = _extract_prompt(r["payload_json"])
        feats = _features(prompt)
        feats["status"] = r["status"]
        feats["iq"] = r["intelligence_score"]
        skill_data.setdefault(skill, []).append(feats)

    # --- Analyze per-skill patterns ---
    insights = []
    recommendations = []

    for skill, entries in sorted(skill_data.items()):
        done = [e for e in entries if e["status"] == "DONE"]
        failed = [e for e in entries if e["status"] == "FAILED"]

        if len(done) + len(failed) < min_samples:
            continue

        analysis = {"skill": skill, "done": len(done), "failed": len(failed)}

        # Compare average features between success/failure
        if done and failed:
            for feat in ("length", "word_count", "sentence_count", "question_marks"):
                avg_done = sum(e[feat] for e in done) / len(done)
                avg_fail = sum(e[feat] for e in failed) / len(failed)
                analysis[f"avg_{feat}_done"] = round(avg_done, 1)
                analysis[f"avg_{feat}_failed"] = round(avg_fail, 1)

                # Generate recommendations based on significant differences
                if feat == "length" and avg_done > avg_fail * 1.5 and avg_fail < 50:
                    recommendations.append(
                        f"**{skill}**: Longer prompts succeed more often "
                        f"(avg {avg_done:.0f} chars vs {avg_fail:.0f} for failures). "
                        f"Add more context to short prompts."
                    )
                elif feat == "word_count" and avg_fail > avg_done * 2:
                    recommendations.append(
                        f"**{skill}**: Overly verbose prompts fail more "
                        f"({avg_fail:.0f} words avg). Aim for ~{avg_done:.0f} words."
                    )

            # Check structural patterns
            done_structured = sum(1 for e in done if e["has_bullet_list"] or e["has_newlines"])
            fail_structured = sum(1 for e in failed if e["has_bullet_list"] or e["has_newlines"])
            done_struct_pct = done_structured / len(done) * 100
            fail_struct_pct = fail_structured / len(failed) * 100

            if done_struct_pct > fail_struct_pct + 20:
                recommendations.append(
                    f"**{skill}**: Structured prompts (bullets/newlines) succeed "
                    f"{done_struct_pct:.0f}% vs {fail_struct_pct:.0f}%. Use lists."
                )

        # IQ score correlation
        iq_scores = [e["iq"] for e in done if e["iq"] is not None]
        if iq_scores:
            analysis["avg_iq"] = round(sum(iq_scores) / len(iq_scores), 3)

        insights.append(analysis)

    # --- Global patterns ---
    all_done = [e for entries in skill_data.values() for e in entries if e["status"] == "DONE"]
    all_failed = [e for entries in skill_data.values() for e in entries if e["status"] == "FAILED"]

    global_stats = {
        "total_analyzed": len(rows),
        "done": len(all_done),
        "failed": len(all_failed),
        "skills_analyzed": len(insights),
    }

    if all_done:
        global_stats["avg_success_length"] = round(
            sum(e["length"] for e in all_done) / len(all_done), 1
        )
    if all_failed:
        global_stats["avg_failure_length"] = round(
            sum(e["length"] for e in all_failed) / len(all_failed), 1
        )

    # --- Build markdown report ---
    rb = (
        ReportBuilder("Prompt Optimization Report")
        .metadata(
            Period=f"Last {hours}h",
            Tasks_Analyzed=len(rows),
            Skills=len(insights),
        )
        .line("---")
        .blank()
    )

    if recommendations:
        rb.section("Recommendations")
        for rec in recommendations:
            rb.line(f"- {rec}")
        rb.blank()

    if insights:
        rb.section("Per-Skill Analysis")
        rb.table(
            ["Skill", "Done", "Failed", "Avg Len (OK)", "Avg Len (Fail)", "Avg IQ"],
            [
                [
                    ins["skill"], ins["done"], ins["failed"],
                    ins.get("avg_length_done", "—"),
                    ins.get("avg_length_failed", "—"),
                    f"{ins['avg_iq']:.3f}" if "avg_iq" in ins else "—",
                ]
                for ins in sorted(insights, key=lambda x: x["failed"], reverse=True)
            ],
        )

    rb.section("Global Statistics")
    for key, val in global_stats.items():
        rb.line(f"- **{key.replace('_', ' ').title()}:** {val}")
    rb.blank()

    report_text = rb.build()
    out_file = OUT_DIR / f"prompt_opt_{date.today().isoformat()}.md"
    out_file.write_text(report_text, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "total_analyzed": len(rows),
        "skills_analyzed": len(insights),
        "recommendations": len(recommendations),
        "insights": insights,
    }
