FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg coreutils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
