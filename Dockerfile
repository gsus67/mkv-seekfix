FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY mkv_seekfix ./mkv_seekfix
RUN pip install --no-cache-dir .

ENV MKV_SEEKFIX_MEDIA=/media \
    MKV_SEEKFIX_CONFIG=/config \
    MKV_SEEKFIX_PORT=8765
VOLUME ["/config"]
EXPOSE 8765
CMD ["mkv-seekfix", "web"]
