# Socrates: the web app plus both extractors, with Chromium for the browser logins.
# Built and published by .github/workflows/docker.yml; run it with compose.yaml.

FROM python:3.12-slim-trixie

# Chromium and its driver from Debian (amd64 and arm64), fonts for the array screenshots,
# and tini as PID 1 so stop signals reach the app and finished Chromium processes are reaped.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/*

# One Python environment for the app and both extractors, installed at build time.
# Copied before the code so a code change doesn't reinstall every package.
COPY socrates-app/requirements.txt /tmp/req/socrates-app.txt
COPY ae-blog/requirements.txt /tmp/req/ae-blog.txt
COPY socrates-arrays-extractor/requirements.txt /tmp/req/socrates-arrays-extractor.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
        -r /tmp/req/socrates-app.txt -r /tmp/req/ae-blog.txt \
        -r /tmp/req/socrates-arrays-extractor.txt \
    && rm -rf /tmp/req

COPY socrates-app /app/socrates-app
COPY ae-blog /app/ae-blog
COPY socrates-arrays-extractor /app/socrates-arrays-extractor

# The extractors' wrapper scripts use the venv next to them: point both at the shared one.
# /data is writable for any user ID because the container runs as PUID:PGID, which isn't
# known at build time; a new named volume copies this folder's permissions.
# Version shown at the bottom of the app's menu, passed in by the release workflow.
# A file, not an environment variable, so a setting in .env can't change it.
ARG VERSION=dev
ARG REVISION=
ARG REPO_URL=https://github.com/FatoYo/socrates
RUN printf '{"version": "%s", "revision": "%s", "repo": "%s"}\n' "$VERSION" "$REVISION" "$REPO_URL" \
        > /app/socrates-app/build-info.json

RUN ln -s /opt/venv /app/ae-blog/venv \
    && ln -s /opt/venv /app/socrates-arrays-extractor/venv \
    && chmod 755 /app/socrates-app/socrates.sh /app/ae-blog/ae-blog.sh /app/socrates-arrays-extractor/socrates-arrays.sh \
    && mkdir -p /data && chmod 0777 /data

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOCRATES_DATA_DIR=/data \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_NO_SANDBOX=1 \
    SE_AVOID_STATS=true \
    SE_OFFLINE=true

# Any non-root user works; compose.yaml sets PUID:PGID.
USER 65534:65534
WORKDIR /app/socrates-app
EXPOSE 3141
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3141/api/jobs', timeout=5)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "socrates.py", "--production", "--host", "0.0.0.0", "--port", "3141"]
