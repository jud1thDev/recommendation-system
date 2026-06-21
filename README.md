# recommendation-system

Kakao Gift recommendation prototype.

## Services

- `rec-api`: user-facing API/static UI, event collection, user context, audience filtering, and `core-api` calls.
- `core-api`: ranks the filtered candidate set and returns recommendation reasons/badges.

## Run With Docker Compose

```sh
docker compose up --build
```

Open:

- `http://localhost:8000`
- `http://localhost:8000/rec-api/`

Health checks:

- `http://localhost:8000/healthz`
- `http://localhost:8001/healthz`

Shared JSON data lives under `data/`.
