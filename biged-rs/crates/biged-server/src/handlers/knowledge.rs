use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};
use std::collections::HashSet;

/// GET /api/knowledge/wiki/graph — wiki pages as graph nodes + edges
///
/// Parses knowledge/wiki/*.md for pages and cross-links.
/// Returns {nodes, edges, page_count} for the graph visualization.
pub async fn wiki_graph(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let wiki_dir = state.fleet_dir.join("knowledge").join("wiki");

    if !wiki_dir.exists() {
        return Ok(Json(json!({"nodes": [], "edges": [], "page_count": 0})));
    }

    let mut nodes = Vec::new();
    let mut edges = Vec::new();

    let mut entries: Vec<_> = std::fs::read_dir(&wiki_dir)
        .map_err(|e| AppError(anyhow::anyhow!("Cannot read wiki dir: {}", e)))?
        .filter_map(|e| e.ok())
        .filter(|e| {
            e.path()
                .extension()
                .map(|ext| ext == "md")
                .unwrap_or(false)
        })
        .collect();
    entries.sort_by_key(|e| e.file_name());

    let page_names: HashSet<String> = entries
        .iter()
        .map(|e| e.path().file_stem().unwrap_or_default().to_string_lossy().to_string())
        .collect();

    for entry in &entries {
        let path = entry.path();
        let page_name = path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let node_id = format!("wiki:{}", page_name);

        let content = std::fs::read_to_string(&path).unwrap_or_default();
        let label = content
            .lines()
            .next()
            .unwrap_or(&page_name)
            .trim_start_matches('#')
            .trim()
            .to_string();

        nodes.push(json!({
            "id": node_id,
            "label": label,
            "type": "wiki_page",
            "name": page_name,
            "status": "active",
        }));

        // Parse markdown links: [text](target)
        for cap in regex_lite::Regex::new(r"\[([^\]]+)\]\(([^)]+)\)")
            .unwrap()
            .captures_iter(&content)
        {
            let link_target = &cap[2];

            // Wiki page cross-links (e.g. "security.md")
            if link_target.ends_with(".md") && !link_target.contains('/') {
                let target_name = link_target.trim_end_matches(".md");
                if page_names.contains(target_name) {
                    edges.push(json!({
                        "source": node_id,
                        "target": format!("wiki:{}", target_name),
                        "type": "links_to",
                    }));
                }
            }
            // Summarizes folder links (e.g. "../security/")
            else if link_target.starts_with("../") && link_target.ends_with('/') {
                let folder = link_target
                    .trim_start_matches("../")
                    .trim_end_matches('/');
                if !folder.is_empty() {
                    edges.push(json!({
                        "source": node_id,
                        "target": format!("folder:{}", folder),
                        "type": "summarizes",
                    }));
                }
            }
        }
    }

    Ok(Json(json!({
        "nodes": nodes,
        "edges": edges,
        "page_count": nodes.len(),
    })))
}

/// GET /api/knowledge/universe — combined universe + wiki graph
///
/// Merges agent/skill/task nodes from DB with wiki page nodes from filesystem.
/// Single endpoint for the full knowledge graph visualization.
pub async fn universe_graph(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let mut all_nodes = Vec::new();
    let mut all_edges = Vec::new();

    // ── Agent nodes ──────────────────────────────────────────────
    let agents = state.db.all_agents()?;
    for agent in &agents {
        all_nodes.push(json!({
            "id": format!("agent:{}", agent.name),
            "label": agent.name,
            "type": "agent",
            "status": agent.status,
        }));
    }

    // ── Skill nodes (from recent task types) ─────────────────────
    let skill_counts = state.db.task_counts_by_skill()?;
    for (skill, counts) in &skill_counts {
        let total: i64 = counts.values().sum();
        all_nodes.push(json!({
            "id": format!("skill:{}", skill),
            "label": skill,
            "type": "skill",
            "metrics": { "count": total },
        }));

        // Edge: skill was run by agents (from recent tasks)
        if let Some(done) = counts.get("DONE") {
            if *done > 0 {
                // Find which agent ran this skill most recently
                // (simplified: connect skill to all active agents)
                for agent in &agents {
                    if agent.status != "DISABLED" {
                        all_edges.push(json!({
                            "source": format!("agent:{}", agent.name),
                            "target": format!("skill:{}", skill),
                            "type": "runs",
                        }));
                    }
                }
            }
        }
    }

    // ── Knowledge folder nodes ───────────────────────────────────
    let knowledge_dir = state.fleet_dir.join("knowledge");
    if knowledge_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&knowledge_dir) {
            for entry in entries.filter_map(|e| e.ok()) {
                if entry.path().is_dir() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name == "wiki" || name.starts_with('.') {
                        continue;
                    }
                    let file_count = std::fs::read_dir(entry.path())
                        .map(|d| d.count())
                        .unwrap_or(0);
                    all_nodes.push(json!({
                        "id": format!("folder:{}", name),
                        "label": name,
                        "type": "folder",
                        "metrics": { "file_count": file_count },
                    }));
                }
            }
        }
    }

    // ── Wiki page nodes + edges (overlay) ────────────────────────
    let wiki_dir = knowledge_dir.join("wiki");
    if wiki_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&wiki_dir) {
            for entry in entries.filter_map(|e| e.ok()) {
                let path = entry.path();
                if path.extension().map(|e| e == "md").unwrap_or(false) {
                    let page_name = path
                        .file_stem()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_string();
                    let content = std::fs::read_to_string(&path).unwrap_or_default();
                    let label = content
                        .lines()
                        .next()
                        .unwrap_or(&page_name)
                        .trim_start_matches('#')
                        .trim()
                        .to_string();

                    all_nodes.push(json!({
                        "id": format!("wiki:{}", page_name),
                        "label": label,
                        "type": "wiki_page",
                    }));

                    // Parse cross-links
                    for cap in regex_lite::Regex::new(r"\[([^\]]+)\]\(([^)]+)\)")
                        .unwrap()
                        .captures_iter(&content)
                    {
                        let target = &cap[2];
                        if target.ends_with(".md") && !target.contains('/') {
                            all_edges.push(json!({
                                "source": format!("wiki:{}", page_name),
                                "target": format!("wiki:{}", target.trim_end_matches(".md")),
                                "type": "links_to",
                            }));
                        } else if target.starts_with("../") && target.ends_with('/') {
                            let folder = target.trim_start_matches("../").trim_end_matches('/');
                            if !folder.is_empty() {
                                all_edges.push(json!({
                                    "source": format!("wiki:{}", page_name),
                                    "target": format!("folder:{}", folder),
                                    "type": "summarizes",
                                }));
                            }
                        }
                    }
                }
            }
        }
    }

    Ok(Json(json!({
        "nodes": all_nodes,
        "edges": all_edges,
        "node_count": all_nodes.len(),
        "edge_count": all_edges.len(),
    })))
}
