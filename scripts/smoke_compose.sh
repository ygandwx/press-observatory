#!/bin/sh
# Run only in a disposable CI workspace, with a fresh Compose project and database.
set -eu
export COMPOSE_PROJECT_NAME=observatory-ci
export APP_TAG=ci
test ! -e .env
cp .env.example .env
trap 'docker compose logs --no-color --tail=30 init worker; docker compose down -v; rm -f .env' EXIT
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose up --no-build -d
for attempt in $(seq 1 45); do
    if curl --silent --fail http://localhost:8080/api/v1/ready >/dev/null; then
        break
    fi
    sleep 2
done
curl --fail http://localhost:8080/api/v1/ready
curl --silent --fail http://localhost:8080/ | grep -q '报业观察站'
docker compose exec -T web python manage.py shell <<'PY'
from desk.collection import enqueue
from desk.models import Source
item = Source.objects.create(name="CI 隔离巡检验证",adapter="人工",url="https://example.org/")
enqueue("CI","ci-check",[item.pk])
PY
docker compose exec -T web python manage.py shell <<'PY'
import time
from desk.models import Run
for attempt in range(30):
    if Run.objects.filter(trigger="CI",state="manual").exists():
        print("Worker acknowledged the isolated test job.")
        break
    time.sleep(1)
else:
    raise RuntimeError("Worker did not consume the queued job.")
PY
