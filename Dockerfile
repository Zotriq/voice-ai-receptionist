FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY receptionist ./receptionist
RUN pip install --no-cache-dir .
ENV DATABASE_PATH=/data/receptionist.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "receptionist.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
