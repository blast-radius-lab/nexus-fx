#build
FROM python:3.11.15-slim-trixie AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

#run
FROM python:3.11.15-slim-trixie AS runner

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

COPY . .

EXPOSE 8001

RUN groupadd -r prodgroup && useradd -r -g prodgroup mps
USER mps

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health', timeout=2)" || exit 1
