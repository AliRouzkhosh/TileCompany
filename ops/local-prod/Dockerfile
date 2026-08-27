FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt \
    && python -m pip check

RUN addgroup --system --gid 10001 tilecompany \
    && adduser --system --uid 10001 --ingroup tilecompany --home /app tilecompany

COPY --chown=tilecompany:tilecompany . /app/
# These are the only writable application data directories.  /app itself stays
# root-owned; the media directory is also initialized explicitly by the
# Compose one-shot service for named-volume mounts.
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R tilecompany:tilecompany /app/staticfiles /app/media \
    && chmod 0755 /app/staticfiles /app/media

USER tilecompany
