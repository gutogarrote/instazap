FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INSTAZAP_DATA=/data OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --python /usr/local/bin/python
RUN groupadd --gid 10001 instazap && useradd --uid 10001 --gid 10001 --no-create-home instazap \
    && mkdir /data && chown 10001:10001 /data
COPY --chown=10001:10001 instazap ./instazap
USER 10001:10001
EXPOSE 8765
CMD [".venv/bin/uvicorn", "instazap.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--limit-concurrency", "16", "--timeout-keep-alive", "5", "--no-access-log", "--no-proxy-headers"]
