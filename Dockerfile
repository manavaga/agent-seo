FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

# Persistent data directory for SQLite
RUN mkdir -p /data
ENV AGENT_SEO_DB=/data/agent_seo.db

CMD ["uvicorn", "agent_seo.server:app", "--host", "0.0.0.0", "--port", "8000"]
