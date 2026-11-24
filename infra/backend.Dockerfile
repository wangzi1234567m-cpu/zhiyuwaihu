FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
ARG INSTALL_REALTIME=false
COPY backend/requirements-rtc.lock ./requirements-rtc.lock
RUN if [ "$INSTALL_REALTIME" = "true" ]; then pip install --no-cache-dir -r requirements-rtc.lock; fi
COPY backend/ ./
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
