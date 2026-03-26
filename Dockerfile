# BigEd CC Fleet — Container Image
# Build: docker build -t biged-fleet .
# Run:   docker run -p 5555:5555 -p 8080:8080 biged-fleet

# ── Stage 1: Rust builder ────────────────────────────────────────────
FROM rust:1.82-slim AS rust-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy Rust workspace (layer-cache Cargo files first for dep caching)
COPY biged-rs/Cargo.toml biged-rs/Cargo.lock ./
COPY biged-rs/src/ ./src/
COPY biged-rs/crates/ ./crates/

# Build release binaries: main biged binary (includes serve/supervisor/worker
# subcommands) plus the PyO3 bridge shared library
RUN cargo build --release -p biged-bridge -p biged-server -p biged-supervisor -p biged

# ── Stage 2: Python runtime ──────────────────────────────────────────
FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy compiled Rust artifacts from builder
COPY --from=rust-builder /build/target/release/biged /app/bin/biged
COPY --from=rust-builder /build/target/release/libbiged_bridge.so /app/biged_bridge.so

# Add Rust binaries to PATH
ENV PATH="/app/bin:${PATH}"

# Copy project files
COPY fleet/ ./fleet/
COPY BigEd/ ./BigEd/
COPY requirements*.txt ./

# Install Python dependencies
RUN if [ -f requirements.txt ]; then uv pip install --system -r requirements.txt; fi

# Ports: dashboard (5555) + web launcher (8080)
EXPOSE 5555 8080

# Healthcheck against dashboard API
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5555/api/fleet/health || exit 1

# Default: start supervisor (which starts dashboard + workers)
CMD ["python", "fleet/supervisor.py"]
