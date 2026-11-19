# Image du tableau de bord et du moteur de pipeline (profil démonstration et profil Big Data).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FUSION_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY fusion ./fusion
RUN pip install --no-cache-dir ".[kafka]" \
 && useradd --system --uid 10001 --home /app fusion \
 && mkdir -p /data && chown fusion /data

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER fusion
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/sante', timeout=2).status == 200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "fusion", "serve", "--host", "0.0.0.0", "--port", "8000"]
