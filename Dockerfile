FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    libheif1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aperture/ ./aperture/

# The process writes into /data (index, control channel, console state) and
# into /thumbnails and /previews. Nothing else needs to be writable, and
# nothing at all needs root.
RUN useradd --system --uid 10001 --no-create-home aperture \
    && mkdir -p /data /thumbnails /previews \
    && chown -R aperture:aperture /data /thumbnails /previews
USER 10001

ENV PHOTOS_DIR=/photos \
    THUMBS_DIR=/thumbnails \
    PREVIEWS_DIR=/previews \
    DATA_DIR=/data \
    APERTURE_ROLE=all \
    PYTHONUNBUFFERED=1

# 8000 is the gallery, 8090 the console. Which of them actually opens is
# decided by APERTURE_ROLE at runtime, not here.
EXPOSE 8000 8090

CMD ["python", "-m", "aperture"]
