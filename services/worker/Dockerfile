FROM python:3.13-slim
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY services/worker/pyproject.toml ./pyproject.toml
COPY services/worker/app ./app
RUN pip install --no-cache-dir .
USER app
CMD ["python","-m","app.main"]
