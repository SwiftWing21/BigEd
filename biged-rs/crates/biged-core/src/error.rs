use thiserror::Error;

#[derive(Error, Debug)]
pub enum CoreError {
    #[error("database error: {0}")]
    Db(#[from] rusqlite::Error),
    #[error("connection pool error: {0}")]
    Pool(#[from] r2d2::Error),
    #[error("config parse error: {0}")]
    Config(String),
    #[error("config file not found: {0}")]
    ConfigNotFound(std::path::PathBuf),
    #[error("task queue full")]
    QueueFull,
    #[error("task not found: {0}")]
    TaskNotFound(i64),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, CoreError>;
