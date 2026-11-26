FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --disable-pip-version-check -r requirements.txt \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid 10001 --no-create-home bot \
    && mkdir -p /app/data /app/config \
    && chown -R bot:bot /app/data
COPY --chown=bot:bot tracker ./tracker
COPY --chown=bot:bot config/carriers.example.json ./config/carriers.example.json
USER bot
CMD ["python", "-m", "tracker"]
