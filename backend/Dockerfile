FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY sportai ./sportai
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
ENV PYTHONUNBUFFERED=1
# API by default; the worker uses the same image with `python -m sportai.worker`
CMD ["sh", "-c", "alembic upgrade head && python -m sportai.manage init && uvicorn sportai.api.main:app --host 0.0.0.0 --port 8000"]
