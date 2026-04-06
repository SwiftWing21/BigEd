use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};

const OLLAMA_DEFAULT_HOST: &str = "http://localhost:11434";

/// GET /api/ollama/status — Ollama process state + loaded models
pub async fn status(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let host = ollama_host(&state).await;

    // Probe Ollama /api/tags to check if it's running
    let running = match reqwest::Client::new()
        .get(format!("{}/api/tags", host))
        .timeout(std::time::Duration::from_secs(3))
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => true,
        _ => false,
    };

    // Get loaded models if running
    let loaded_models = if running {
        match reqwest::Client::new()
            .get(format!("{}/api/ps", host))
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                let body: Value = resp.json().await.unwrap_or(json!({}));
                body.get("models")
                    .cloned()
                    .unwrap_or_else(|| json!([]))
            }
            _ => json!([]),
        }
    } else {
        json!([])
    };

    Ok(Json(json!({
        "running": running,
        "host": host,
        "loaded_models": loaded_models,
    })))
}

/// GET /api/ollama/ps — proxy to Ollama's /api/ps (loaded model details)
pub async fn ps(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let host = ollama_host(&state).await;
    let resp = reqwest::Client::new()
        .get(format!("{}/api/ps", host))
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| AppError(anyhow::anyhow!("Ollama unreachable: {}", e)))?;
    let body: Value = resp
        .json()
        .await
        .map_err(|e| AppError(anyhow::anyhow!("Bad Ollama response: {}", e)))?;
    Ok(Json(body))
}

/// POST /api/ollama/start — start or adopt Ollama
pub async fn start(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let host = ollama_host(&state).await;

    // Check if already running
    if probe_ollama(&host).await {
        return Ok(Json(json!({
            "status": "already_running",
            "host": host,
        })));
    }

    // Find ollama binary
    let ollama_exe = find_ollama_binary();
    let exe = ollama_exe
        .as_deref()
        .ok_or_else(|| AppError(anyhow::anyhow!("Ollama binary not found")))?;

    // Spawn ollama serve with optimization env vars from fleet.toml [models] extras
    let config = state.config.read().await;
    let mut cmd = tokio::process::Command::new(exe);
    cmd.arg("serve");
    // Pass through known Ollama env vars from config extras if present
    if let Some(extras) = config.models.extra.get("flash_attention") {
        if extras.as_bool().unwrap_or(false) {
            cmd.env("OLLAMA_FLASH_ATTENTION", "1");
        }
    }
    if let Some(kv) = config.models.extra.get("kv_cache_type") {
        if let Some(kv_str) = kv.as_str() {
            cmd.env("OLLAMA_KV_CACHE_TYPE", kv_str);
        }
    }

    cmd.stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| AppError(anyhow::anyhow!("Failed to start Ollama: {}", e)))?;

    // Wait briefly for it to come up
    tokio::time::sleep(std::time::Duration::from_secs(3)).await;

    let running = probe_ollama(&host).await;
    Ok(Json(json!({
        "status": if running { "started" } else { "starting" },
        "host": host,
        "exe": exe,
    })))
}

/// POST /api/ollama/stop — unload models (optionally terminate)
pub async fn stop(
    State(state): State<AppState>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let host = ollama_host(&state).await;
    let terminate = payload
        .get("terminate")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // Unload all loaded models via keep_alive=0
    let mut unloaded = Vec::new();
    if let Ok(models) = get_loaded_models(&host).await {
        let client = reqwest::Client::new();
        for model_name in models {
            let _ = client
                .post(format!("{}/api/generate", host))
                .json(&json!({ "model": model_name, "keep_alive": 0 }))
                .timeout(std::time::Duration::from_secs(5))
                .send()
                .await;
            unloaded.push(model_name);
        }
    }

    if terminate {
        #[cfg(windows)]
        {
            let _ = tokio::process::Command::new("taskkill")
                .args(["/F", "/IM", "ollama.exe"])
                .output()
                .await;
        }
        #[cfg(not(windows))]
        {
            let _ = tokio::process::Command::new("pkill")
                .arg("ollama")
                .output()
                .await;
        }
    }

    Ok(Json(json!({
        "status": "ok",
        "unloaded": unloaded,
        "terminated": terminate,
    })))
}

// ── Helpers ──────────────────────────────────────────────────────────────

async fn ollama_host(state: &AppState) -> String {
    let config = state.config.read().await;
    let host = &config.models.ollama_host;
    if host.is_empty() {
        OLLAMA_DEFAULT_HOST.to_string()
    } else {
        host.clone()
    }
}

async fn probe_ollama(host: &str) -> bool {
    reqwest::Client::new()
        .get(format!("{}/api/tags", host))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

async fn get_loaded_models(host: &str) -> anyhow::Result<Vec<String>> {
    let resp = reqwest::Client::new()
        .get(format!("{}/api/ps", host))
        .timeout(std::time::Duration::from_secs(3))
        .send()
        .await?;
    let body: Value = resp.json().await?;
    let models = body
        .get("models")
        .and_then(|m| m.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|m| m.get("name").and_then(|n| n.as_str()).map(String::from))
                .collect()
        })
        .unwrap_or_default();
    Ok(models)
}

fn find_ollama_binary() -> Option<String> {
    // 1. Windows default locations (Git Bash PATH often doesn't include Ollama)
    #[cfg(windows)]
    {
        let candidates = [
            std::env::var("LOCALAPPDATA")
                .ok()
                .map(|p| format!("{}\\Programs\\Ollama\\ollama.exe", p)),
            std::env::var("LOCALAPPDATA")
                .ok()
                .map(|p| format!("{}\\Ollama\\ollama.exe", p)),
            std::env::var("PROGRAMFILES")
                .ok()
                .map(|p| format!("{}\\Ollama\\ollama.exe", p)),
        ];
        for candidate in candidates.into_iter().flatten() {
            if std::path::Path::new(&candidate).exists() {
                return Some(candidate);
            }
        }
    }
    // 2. Fallback: assume "ollama" is on PATH
    Some("ollama".into())
}
