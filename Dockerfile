# SynthUsers dashboard + runner in one container.
# Build:  docker build -t synthusers .
# Run:    docker run -p 8700:8700 -e ANTHROPIC_API_KEY=... -e SYNTHUSERS_ADMIN_TOKEN=... \
#           -v synthusers-runs:/app/runs synthusers
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY synthusers ./synthusers
COPY frictionlab ./frictionlab
COPY specs ./specs

RUN pip install --no-cache-dir -e . \
    && playwright install --with-deps chromium \
    && mkdir -p runs

EXPOSE 8700
# Railway/Render/Heroku-style platforms inject PORT; default to 8700 elsewhere.
CMD ["sh", "-c", "python3 -m synthusers serve --host 0.0.0.0 --port ${PORT:-8700}"]
