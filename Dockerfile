FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=10000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt package.json ./

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && npm install -g vercel

COPY . .

RUN mkdir -p /app/shared/generated /app/shared/output

EXPOSE 10000

CMD ["python", "-m", "src.ui.server"]
