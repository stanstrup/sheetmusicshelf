# Slim rather than alpine: pypdf and psycopg ship manylinux wheels, and musl
# would force a source build of both on a box with four cores to spare.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

# tesseract is here for the on-demand OCR button only -- nothing runs it in
# bulk, by design.  poppler-utils gives us pdftoppm for thumbnails.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng poppler-utils curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# Permissions are set here rather than inherited, because the build context
# reaches this machine over SMB from Windows and arrives mode 770: readable by
# root only. The container runs as an unprivileged user, so every one of these
# files has to be readable by "other" or nothing works -- the entrypoint died
# with "Permission denied" for want of the read bit, and alembic then died the
# same way on pyproject.toml.
#
# a+rX is the safe form: read for everyone, execute only where it already is
# (directories, and the entrypoint below).
RUN chmod 755 /usr/local/bin/entrypoint.sh  && chmod -R a+rX /app

# Never write to the library as root: the source mount is read-only, but the
# managed tree is not, and a stray root-owned file on the NAS is a nuisance.
RUN useradd --uid 1000 --create-home --shell /bin/bash sms

# Own the cache before dropping privileges. Docker seeds a fresh named volume
# from the image's directory, ownership included, so a /cache that does not
# exist here arrives owned by root -- and the unprivileged user cannot write a
# single rendered page into it. Every page request 500s on the first mkdir.
RUN mkdir -p /cache && chown -R sms:sms /cache

USER sms

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["web"]
