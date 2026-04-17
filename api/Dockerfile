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
        imagemagick \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
