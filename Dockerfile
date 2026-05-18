# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder

ARG PACKAGE_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${PACKAGE_VERSION}
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEEPSEEK_BRIDGE=${PACKAGE_VERSION}

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

FROM python:3.14-slim AS runtime

ENV HOME=/nonexistent
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 deepseek-bridge \
    && useradd \
        --system \
        --uid 10001 \
        --gid deepseek-bridge \
        --home-dir /nonexistent \
        --shell /usr/sbin/nologin \
        deepseek-bridge \
    && mkdir -p /etc/deepseek-bridge /data \
    && printf '%s\n' '# Container defaults are supplied by Docker CMD.' \
        > /etc/deepseek-bridge/config.yaml \
    && chown -R 10001:10001 /data \
    && chmod 0444 /etc/deepseek-bridge/config.yaml

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001
WORKDIR /app
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import http.client; c=http.client.HTTPConnection('127.0.0.1', 9000, timeout=2); c.request('GET', '/healthz'); raise SystemExit(0 if c.getresponse().status == 200 else 1)"

ENTRYPOINT ["deepseek-bridge"]
CMD ["--headless", "--tunnel", "none", "--host", "0.0.0.0", "--port", "9000", "--config", "/etc/deepseek-bridge/config.yaml", "--no-log", "--reasoning-content-path", "/data/reasoning_content.sqlite3"]
