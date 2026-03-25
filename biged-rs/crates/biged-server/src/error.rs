use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use biged_core::error::CoreError;

pub struct AppError(pub anyhow::Error);

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        tracing::error!("Request error: {:?}", self.0);

        let (status, message) = if let Some(core_err) = self.0.downcast_ref::<CoreError>() {
            match core_err {
                CoreError::TaskNotFound(_) => {
                    (StatusCode::NOT_FOUND, "Resource not found")
                }
                CoreError::ConfigNotFound(_) => {
                    (StatusCode::NOT_FOUND, "Configuration not found")
                }
                _ => (StatusCode::INTERNAL_SERVER_ERROR, "Internal server error"),
            }
        } else {
            (StatusCode::INTERNAL_SERVER_ERROR, "Internal server error")
        };

        (status, axum::Json(serde_json::json!({"error": message}))).into_response()
    }
}

impl From<CoreError> for AppError {
    fn from(err: CoreError) -> Self {
        Self(err.into())
    }
}

impl From<r2d2::Error> for AppError {
    fn from(err: r2d2::Error) -> Self {
        Self(err.into())
    }
}

impl From<rusqlite::Error> for AppError {
    fn from(err: rusqlite::Error) -> Self {
        Self(err.into())
    }
}

impl From<anyhow::Error> for AppError {
    fn from(err: anyhow::Error) -> Self {
        Self(err)
    }
}
