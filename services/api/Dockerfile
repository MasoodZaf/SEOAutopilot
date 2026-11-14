FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY services/api/pyproject.toml ./pyproject.toml
COPY services/api/requirements.lock ./requirements.lock
COPY services/api/app ./app
# The schema this image expects, so `python -m app.cli.migrate` can apply what a
# database is missing without the repository being present on the host.
COPY infra/migrations ./migrations
# Exact versions and hashes, so a rebuild runs what was tested. Refresh with `make lock`.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
USER app
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
