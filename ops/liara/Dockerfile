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
# Persistent disks are attached by Liara at these existing application paths.
# The mounted roots are initialized once by an operator, not by web startup.
RUN mkdir -p /app/staticfiles /app/media /app/private_inventory_price_import_sources \
    && chown -R tilecompany:tilecompany /app/staticfiles /app/media /app/private_inventory_price_import_sources \
    && chmod 0755 /app/staticfiles /app/media /app/private_inventory_price_import_sources /app/ops/liara/start-web.sh

USER tilecompany

EXPOSE 8000

CMD ["/app/ops/liara/start-web.sh"]
