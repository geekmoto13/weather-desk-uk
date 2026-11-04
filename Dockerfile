FROM python:3.12-slim
RUN pip install --no-cache-dir tzdata \
 && useradd --create-home --uid 1000 app
WORKDIR /app
COPY --chown=app:app . /app
RUN mkdir -p /app/private && chown -R app:app /app
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/', timeout=4)" || exit 1
CMD ["python", "-X", "utf8", "server.py", "--lan"]
