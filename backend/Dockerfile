# Gam'eya API image (Person 1 / P5 integration). Includes the golden fixtures so
# the M1 fixture-backed API works with zero credentials; PayPal env vars come
# from the platform (never baked in).
FROM python:3.11-slim

WORKDIR /srv

COPY backend/requirements.txt /srv/backend/requirements.txt
RUN pip install --no-cache-dir -r /srv/backend/requirements.txt

COPY shared/fixtures /srv/shared/fixtures
COPY backend/app /srv/backend/app
COPY backend/pyproject.toml /srv/backend/pyproject.toml

ENV PYTHONPATH=/srv/backend \
    FIXTURES_DIR=/srv/shared/fixtures/json \
    PYTHONUNBUFFERED=1

WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('PORT','8000'))" || exit 1

# Build context is the repo root (render.yaml dockerContext / compose context).
# Render injects PORT; locally it defaults to 8000.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
