# BigEd CC Fleet — Container Image
# Build: docker build -t biged-fleet .
# Run:   docker run -p 5555:5555 -p 8080:8080 biged-fleet

FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

WORKDIR /app

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
