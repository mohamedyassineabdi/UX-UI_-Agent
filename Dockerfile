FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=10000 \
    AUDIT_BROWSER_HEADLESS=1 \
    AUDIT_PAGE_CONCURRENCY=2 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt package.json ./

RUN apt-get update \
    && apt-get install -y ca-certificates curl gnupg \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && npm install -g vercel \
    && node --version \
    && vercel --version

COPY . .

RUN mkdir -p /app/shared/generated /app/shared/output

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"10000\")}/health', timeout=5).read()"

CMD ["python", "-m", "src.ui.server"]
