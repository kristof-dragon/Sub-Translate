# ---------------------------------------------------------------------------
# Builder stage: compile subtile-ocr (Rust) for VobSub OCR.
#
# subtile-ocr links against tesseract + leptonica via leptess, so the build
# image needs the dev headers + a Rust toolchain. The final binary is a
# single static-ish executable (~5 MB) that we COPY into the runtime image,
# leaving the ~1.5 GB builder behind.
# ---------------------------------------------------------------------------
FROM rust:1-bookworm AS subtile-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        clang \
        libtesseract-dev \
        libleptonica-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Pinned for reproducibility — bump deliberately, not transitively.
# Latest crates.io release as of v1.5.0 cut.
RUN cargo install --root /opt/subtile --version 0.2.6 subtile-ocr


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg ships `ffprobe` (JSON subtitle stream enumeration) and `ffmpeg`
# (stream-copy / codec-convert subtitle extraction). It handles MKV, MP4,
# WebM, AVI, MOV, TS, and more — wider coverage than mkvtoolnix and the
# same binary produces SRT/VTT natively via `-c:s srt`.
#
# tesseract + per-language traineddata + imagemagick are pgsrip's runtime
# deps for OCR'ing PGS bitmap subtitles to SRT. The language pack list
# matches the curated target languages in app/languages.py — extend it
# there if the operator's source content uses a script not covered here.
#
# libtesseract / libleptonica are also subtile-ocr's runtime deps —
# `tesseract-ocr` pulls libtesseract5 transitively, but leptonica needs
# explicit installation since the cli alone doesn't depend on it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-hun \
        tesseract-ocr-spa \
        tesseract-ocr-fra \
        tesseract-ocr-deu \
        tesseract-ocr-ita \
        tesseract-ocr-por \
        tesseract-ocr-rus \
        tesseract-ocr-jpn \
        tesseract-ocr-kor \
        tesseract-ocr-chi-sim \
        tesseract-ocr-chi-tra \
        tesseract-ocr-ara \
        libleptonica-dev \
        imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Pull the subtile-ocr binary out of the Rust builder stage. GPL-3.0 binary
# invoked via subprocess from MIT-licensed code — no derivative-work concern
# per FSF's own analysis of binary-only shell-out.
COPY --from=subtile-builder /opt/subtile/bin/subtile-ocr /usr/local/bin/subtile-ocr

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
