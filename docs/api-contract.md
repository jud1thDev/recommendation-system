# rec-api / core-inference Contract

This document defines the current HTTP JSON contracts between `rec-api`, `core-api`/`inference`, and the user event pipeline.
The transport may later move to gRPC, so request and response bodies should stay message-like and avoid browser-specific details.

## Service Roles

- `rec-api`: owns the user-facing flow, user context, audience filtering, and candidate product selection.
- `core-api` / `inference`: owns recommendation ranking, LLM reasoning, and response shaping within the candidate set from `rec-api`.
- `event-worker`: consumes user events from Kafka and stores user/session grouped event lists in Redis.
- Authentication and login are assumed to be handled before this contract. `userId` is an already-authenticated internal user identifier.

## Transport

- `rec-api` local base URL: `http://localhost:8000`
- `core-api` / `inference` local base URL: `http://localhost:8001`
- Content type: `application/json; charset=utf-8`
- `rec-api` to `core-api` request timeout target: 10 seconds
- API version: path prefix `/v1`

## Endpoints

### GET /healthz

Checks whether a service is reachable.

`core-api` / `inference` response:

```json
{
  "ok": true,
  "service": "core-api",
  "version": "0.1.0"
}
```

`rec-api` response:

```json
{
  "ok": true,
  "service": "rec-api",
  "coreApiUrl": "http://localhost:8001"
}
```

### POST /v1/recommendations on rec-api

This is the browser/client-facing recommendation endpoint.
The client sends user preference and recent click history.
`rec-api` loads product data, applies audience/filtering rules, builds `candidateItems`, then calls `core-api` / `inference`.

Client request:

```json
{
  "requestId": "rec-20260621-0001",
  "user": {
    "userId": "user-001"
  },
  "preference": {
    "relation": "친구",
    "occasion": "생일",
    "budget": 40000,
    "traits": ["트렌디함", "포장중요"],
    "avoidTags": ["건강/비타민"]
  },
  "clickHistory": [
    {
      "productId": "kakao-6993339",
      "category": "뷰티/향수/바디",
      "tags": ["뷰티/향수/바디", "조말론런던", "배송상품", "PACKAGE_GIFT"],
      "priceValue": 34000,
      "clickedAt": "2026-06-21T12:45:00+09:00"
    }
  ],
  "limit": 8
}
```

Client response:

```json
{
  "requestId": "rec-20260621-0001",
  "recommendations": [
    {
      "id": "kakao-6993339",
      "rank": 1,
      "score": 0.94,
      "reason": "친구에게 무난한 선택 · 생일 상황과 잘 맞음",
      "badges": ["상황적합", "트렌디함"]
    }
  ],
  "model": {
    "provider": "local",
    "name": "candidate-ranker",
    "version": "0.1.0"
  },
  "candidateCount": 50
}
```

Fallback behavior:

- If `core-api` / `inference` does not respond, `rec-api` returns random `limit` items from the already-filtered candidate set.
- Fallback responses use `model.name = "fallback-random"` and `score = null`.

### POST /v1/recommendations on core-api / inference

Returns ranked gift recommendations for one user request.

`rec-api` sends already-filtered candidate products.
Audience filtering stays in `rec-api`; `core-api` / `inference` must rank and explain only within the candidate set it receives.

Internal request from `rec-api`:

```json
{
  "requestId": "rec-20260621-0001",
  "user": {
    "userId": "user-001"
  },
  "preference": {
    "relation": "친구",
    "occasion": "생일",
    "budget": 40000,
    "traits": ["트렌디함", "포장중요"],
    "avoidTags": ["건강/비타민"]
  },
  "clickHistory": [
    {
      "productId": "kakao-6993339",
      "category": "뷰티/향수/바디",
      "tags": ["뷰티/향수/바디", "조말론런던", "배송상품", "PACKAGE_GIFT"],
      "priceValue": 34000,
      "clickedAt": "2026-06-21T12:45:00+09:00"
    }
  ],
  "candidateItems": [
    {
      "id": "kakao-6993339",
      "name": "[단독/각인/선물포장] 코롱 9ML",
      "brand": "조말론런던",
      "category": "뷰티/향수/바디",
      "price": "34,000원",
      "priceValue": 34000,
      "wishCount": 180376,
      "imageUrl": "https://example.com/product.png",
      "url": "https://gift.kakao.com/product/6993339",
      "tags": ["뷰티/향수/바디", "조말론런던", "배송상품"],
      "relations": ["친구", "연인", "배우자", "직장동료"],
      "occasions": ["기념일"],
      "traits": ["트렌디함", "개인화", "포장중요"]
    }
  ],
  "limit": 8
}
```

Internal response to `rec-api`:

```json
{
  "requestId": "rec-20260621-0001",
  "recommendations": [
    {
      "id": "kakao-6993339",
      "rank": 1,
      "score": 0.94,
      "reason": "친구에게 부담 없는 가격대이고, 선물포장과 트렌디한 성향을 함께 만족합니다.",
      "badges": ["상황적합", "포장중요", "트렌디함"]
    }
  ],
  "model": {
    "provider": "local",
    "name": "candidate-ranker",
    "version": "0.1.0"
  }
}
```

### POST /v1/events on rec-api

Collects user behavior events. Current event types are product impression, product click, and add to cart.
`rec-api` writes events to `data/user-events.json` for local visibility and, when Kafka is configured, publishes the same event to Kafka topic `rec-events`.

Request:

```json
{
  "eventId": "evt-20260621-0001",
  "userId": "user-001",
  "type": "product_click",
  "occurredAt": "2026-06-21T12:45:00+09:00",
  "context": {
    "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
    "requestId": "rec-20260621-0001"
  },
  "product": {
    "productId": "kakao-6993339",
    "category": "뷰티/향수/바디",
    "tags": ["뷰티/향수/바디", "조말론런던", "배송상품"],
    "priceValue": 34000,
    "rank": 1
  },
  "properties": {}
}
```

Response:

```json
{
  "event": {
    "eventId": "evt-20260621-0001",
    "userId": "user-001",
    "type": "product_click",
    "occurredAt": "2026-06-21T12:45:00+09:00",
    "context": {
      "sessionId": "session-550e8400-e29b-41d4-a716-446655440000",
      "requestId": "rec-20260621-0001"
    },
    "product": {
      "productId": "kakao-6993339",
      "category": "뷰티/향수/바디",
      "tags": ["뷰티/향수/바디", "조말론런던", "배송상품"],
      "priceValue": 34000,
      "rank": 1
    },
    "properties": {}
  },
  "kafkaPublished": true
}
```

## Field Definitions

### RecommendationRequest

Client-to-`rec-api` requests omit `candidateItems`.
`rec-api` adds `candidateItems` before calling `core-api` / `inference`.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `requestId` | string | yes | Generated by `rec-api` for tracing. |
| `user.userId` | string | yes | Authenticated internal user ID. No password, token, email, or phone number. |
| `preference.relation` | string | yes | Recipient relationship. Example: `친구`, `부모님`, `직장동료`. |
| `preference.occasion` | string | yes | Gift occasion. Example: `생일`, `감사`, `기념일`. |
| `preference.budget` | number | yes | Max price in KRW. |
| `preference.traits` | string[] | yes | Preferred recipient/product traits. Empty array is allowed. |
| `preference.avoidTags` | string[] | yes | Product tags or categories to avoid. Empty array is allowed. |
| `clickHistory` | ClickEvent[] | yes | Recent user behavior. Empty array is allowed. |
| `candidateItems` | CandidateItem[] | internal | Added by `rec-api` before calling `core-api` / `inference`. Already-filtered products available for ranking. |
| `limit` | number | no | Defaults to 8 when omitted. |

### ClickEvent

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `productId` | string | yes | Matches `CandidateItem.id`. |
| `category` | string | yes | Product category at click time. |
| `tags` | string[] | yes | Product tags at click time. |
| `priceValue` | number | yes | Product price in KRW at click time. |
| `clickedAt` | string | yes | ISO 8601 timestamp. |

### CandidateItem

This matches the LLM item mapping used by `rec-api`.
Items in this array have already passed `rec-api` audience filtering.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | Product ID. |
| `name` | string | yes | Product display name. |
| `brand` | string | yes | Brand display name. |
| `category` | string | yes | Normalized product category. |
| `price` | string | yes | Display price. |
| `priceValue` | number | yes | Price in KRW for filtering/ranking. |
| `wishCount` | number | yes | Popularity signal. |
| `imageUrl` | string | yes | Rendered by `rec-api`, not interpreted by `core-api`. |
| `url` | string | yes | Kakao Gift product URL. |
| `tags` | string[] | yes | Ranking/filter tags. |
| `relations` | string[] | yes | Suitable recipient relations. |
| `occasions` | string[] | yes | Suitable occasions. |
| `traits` | string[] | yes | Product/recipient taste traits. |

### RecommendationResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `requestId` | string | yes | Echoes request ID. |
| `recommendations` | Recommendation[] | yes | Ordered by rank ascending. |
| `model` | object | no | Debug metadata for the recommendation engine. |
| `candidateCount` | number | rec-api only | Count of products left after `rec-api` filtering. |

### Recommendation

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | Product ID. `rec-api` joins this with local product data for rendering. |
| `rank` | number | yes | 1-based rank. |
| `score` | number | no | Normalized score from 0 to 1 when available. |
| `reason` | string | yes | User-facing Korean recommendation reason. |
| `badges` | string[] | yes | Short labels rendered on product cards. |

### UserEvent

Allowed event types:

| Type | Meaning |
| --- | --- |
| `product_impression` | Product card was shown to the user. |
| `product_click` | User clicked a product card or product link. |
| `add_to_cart` | User added a product to cart. |

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `eventId` | string | no | Generated by `rec-api` when omitted. |
| `userId` | string | yes | Authenticated internal user ID. Events can be grouped by this value. |
| `type` | string | yes | One of `product_impression`, `product_click`, `add_to_cart`. |
| `occurredAt` | string | no | ISO 8601 timestamp. Generated by `rec-api` when omitted. |
| `context.sessionId` | string | yes | Browser session identifier. Events can be grouped by this value. |
| `context.requestId` | string | no | Recommendation request that produced the clicked result, when available. |
| `product.productId` | string | yes | Event product ID. |
| `product.category` | string | yes | Event product category. |
| `product.tags` | string[] | yes | Event product tags. |
| `product.priceValue` | number | yes | Event product price in KRW. |
| `product.rank` | number | no | Rank at impression/click/cart time, when known. |
| `properties` | object | no | Extra event metadata. Receivers should ignore unknown fields. |

## Event Pipeline

Docker Compose event flow:

```text
Browser event -> rec-api POST /v1/events -> Kafka topic rec-events -> event-worker -> Redis
```

Redis keys written by `event-worker`:

| Redis key | Contents |
| --- | --- |
| `events:all` | Recent events across all users and sessions. |
| `events:type:{type}` | Recent events for one event type. |
| `events:user:{userId}` | Recent events grouped by user. |
| `events:session:{sessionId}` | Recent events grouped by session. |
| `impressions:user:{userId}` | Recent `product_impression` events grouped by user. |
| `impressions:session:{sessionId}` | Recent `product_impression` events grouped by session. |
| `clicks:user:{userId}` | Recent `product_click` events grouped by user. |
| `clicks:session:{sessionId}` | Recent `product_click` events grouped by session. |
| `carts:user:{userId}` | Recent `add_to_cart` events grouped by user. |
| `carts:session:{sessionId}` | Recent `add_to_cart` events grouped by session. |

Each key is a Redis list. New events are pushed to the head, and each list is trimmed to `MAX_EVENTS_PER_KEY` items.

## Error Response

Non-2xx responses use this shape:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "candidateItems must not be empty",
    "requestId": "rec-20260621-0001"
  }
}
```

Recommended status codes:

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | Missing or invalid fields. |
| `400` | `NO_CANDIDATES` | No products remain after `rec-api` filtering. |
| `408` | `REQUEST_TIMEOUT` | Recommendation work timed out. |
| `500` | `INTERNAL_ERROR` | Unexpected service failure. |
| `503` | `MODEL_UNAVAILABLE` | LLM or ranking dependency unavailable. |

## Docker Compose Services

| Service | Port | Role |
| --- | --- | --- |
| `rec-api` | `8000` | Static UI, recommendation API, click event API, audience filtering. |
| `core-api` | `8001` | Current local core/inference mock. |
| `kafka` | `9092` | Event transport for click/user events. |
| `redis` | `6379` | Event storage grouped by `userId` and `sessionId`. |
| `event-worker` | none | Kafka consumer that writes event lists into Redis. |

## Compatibility Rules

- Additive optional fields are allowed.
- Required field removal or type changes require a new version path, such as `/v2`.
- Unknown fields should be ignored by receivers.
- String enum values are not strict yet because product tags and relation labels may evolve during the hackathon.
