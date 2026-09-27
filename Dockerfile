FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic anthropic
COPY bot.py ./
COPY vera ./vera
EXPOSE 8080
# Single worker: state (contexts, conversations) is in-process memory.
CMD ["sh", "-c", "uvicorn bot:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
