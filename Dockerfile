# Builder stage
FROM python:3.12.13-slim AS builder

WORKDIR /app

RUN python -m venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final runtime stage
FROM python:3.12.13-slim

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv
COPY . .

CMD ["uvicorn", "outreach_engine.tracking.pixel_server:app", "--host", "0.0.0.0", "--port", "8080"]
