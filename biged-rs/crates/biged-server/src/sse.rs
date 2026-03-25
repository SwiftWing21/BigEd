use crate::AppState;
use axum::extract::State;
use axum::response::sse::{Event, KeepAlive, Sse};
use futures::stream::Stream;
use serde_json::json;
use std::convert::Infallible;
use std::time::Duration;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;

/// GET /api/stream — SSE endpoint for live fleet updates
pub async fn stream(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let rx = state.events.subscribe();
    let db = state.db.clone();

    let stream = async_stream::stream! {
        // Send initial connection event
        yield Ok(Event::default().data(json!({"type": "connected"}).to_string()));

        // Send initial status snapshot
        if let Ok(agents) = db.all_agents() {
            if let Ok(tasks) = db.task_counts_by_status() {
                yield Ok(Event::default().data(
                    json!({"type": "status", "data": {"agents": agents, "tasks": tasks}}).to_string()
                ));
            }
        }

        // Forward fleet events as SSE
        let mut event_stream = BroadcastStream::new(rx);
        while let Some(Ok(event)) = event_stream.next().await {
            let event_type = event.event_type();
            if let Ok(data) = serde_json::to_string(&event) {
                yield Ok(Event::default()
                    .event(event_type)
                    .data(data));
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}
