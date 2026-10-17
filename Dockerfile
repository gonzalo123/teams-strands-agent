FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app
RUN pip install --no-cache-dir poetry==2.3.2
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction
COPY README.md ./
COPY src/teams_agent ./src/teams_agent
RUN poetry install --only main --no-interaction && useradd --create-home app
USER app
CMD ["teams-agent", "api", "--host", "0.0.0.0"]
