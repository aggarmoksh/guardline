# GuardLine API. No secrets are baked in: configuration comes from the environment (docker compose reads .env).
# Member fixtures (fixtures/raw) are mounted at run time, never copied into the image.
FROM python:3.12-slim
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY guardline ./guardline
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "guardline.api:app", "--host", "0.0.0.0", "--port", "8000"]
