FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
COPY lanshare/ ./lanshare/

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /data /uploads \
    && chown -R appuser:appuser /data /uploads

VOLUME ["/data"]
EXPOSE 8000

USER appuser

ENTRYPOINT ["lanshare"]
CMD ["/data", "--host", "0.0.0.0"]
