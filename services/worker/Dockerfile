FROM python:3.13-slim
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY services/worker/pyproject.toml ./pyproject.toml
COPY services/worker/requirements.lock ./requirements.lock
COPY services/worker/app ./app
# Exact versions and hashes, so a rebuild runs what was tested. Refresh with `make lock`.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
USER app
CMD ["python","-m","app.main"]
