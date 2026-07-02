FROM python:3.12.13 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

RUN python -m venv .venv
COPY requirements.txt ./
RUN /app/.venv/bin/python -m pip install --upgrade pip
RUN /app/.venv/bin/python -m pip install -r requirements.txt
FROM python:3.12.13-slim
WORKDIR /app
COPY --from=builder /app/.venv .venv/
COPY . .
CMD ["/app/.venv/bin/uvicorn", "outreach_engine.tracking.pixel_server:app", "--host", "0.0.0.0", "--port", "8080"]
