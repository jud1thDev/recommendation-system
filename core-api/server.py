import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from redis import Redis
except ImportError:
    Redis = None


REDIS_URL = os.environ.get("REDIS_URL")
RECENT_EVENT_LIMIT = int(os.environ.get("RECENT_EVENT_LIMIT", "30"))
REDIS_CLIENT = None


def response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def summarize_clicks(click_history):
    categories = set()
    tags = set()
    for click in click_history:
        if click.get("category"):
            categories.add(click["category"])
        for tag in click.get("tags", []):
            tags.add(tag)
    return categories, tags


def get_redis_client():
    global REDIS_CLIENT
    if not REDIS_URL or Redis is None:
        return None
    if REDIS_CLIENT is None:
        REDIS_CLIENT = Redis.from_url(REDIS_URL, decode_responses=True)
    return REDIS_CLIENT


def event_to_behavior(event):
    product = event.get("product") or {}
    product_id = product.get("productId") or product.get("id")
    if not product_id:
        return None
    return {
        "productId": product_id,
        "category": product.get("category"),
        "tags": product.get("tags", []),
        "priceValue": product.get("priceValue"),
        "eventType": event.get("type"),
        "occurredAt": event.get("occurredAt"),
    }


def read_recent_events(redis, keys):
    events = []
    seen = set()
    for key in keys:
        for raw in redis.lrange(key, 0, RECENT_EVENT_LIMIT - 1):
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_key = event.get("eventId") or raw
            if event_key in seen:
                continue
            seen.add(event_key)
            events.append(event)
    return events[:RECENT_EVENT_LIMIT]


def load_recent_behavior(payload):
    user_id = (payload.get("user") or {}).get("userId")
    session_id = (payload.get("context") or {}).get("sessionId")
    redis = get_redis_client()
    if redis is None or (not user_id and not session_id):
        return {"clickHistory": [], "impressionHistory": []}

    click_keys = []
    impression_keys = []
    if user_id:
        click_keys.append(f"clicks:user:{user_id}")
        impression_keys.append(f"impressions:user:{user_id}")
    if session_id:
        click_keys.append(f"clicks:session:{session_id}")
        impression_keys.append(f"impressions:session:{session_id}")

    try:
        click_history = [event_to_behavior(event) for event in read_recent_events(redis, click_keys)]
        impression_history = [event_to_behavior(event) for event in read_recent_events(redis, impression_keys)]
    except Exception as exc:
        print(f"[core-api] redis behavior unavailable: {exc}")
        return {"clickHistory": [], "impressionHistory": []}

    return {
        "clickHistory": [item for item in click_history if item],
        "impressionHistory": [item for item in impression_history if item],
    }


def with_recent_behavior(payload):
    recent_behavior = load_recent_behavior(payload)
    payload = dict(payload)
    payload["clickHistory"] = [
        *payload.get("clickHistory", []),
        *recent_behavior["clickHistory"],
    ]
    payload["recentBehavior"] = recent_behavior
    return payload


def make_reason(product, preference, trait_matches, recent_category):
    reasons = []
    relation = preference.get("relation")
    occasion = preference.get("occasion")
    if relation and relation in product.get("relations", []):
        reasons.append(f"{relation}에게 무난한 선택")
    if occasion and occasion in product.get("occasions", []):
        reasons.append(f"{occasion} 상황과 잘 맞음")
    if trait_matches:
        reasons.append(f"{', '.join(trait_matches)} 성향 반영")
    if recent_category:
        reasons.append(f"최근 본 {product.get('category')} 선호 반영")
    if not reasons:
        reasons.append("후보군 안에서 인기도와 조건 적합도가 높은 상품")
    return " · ".join(reasons[:2])


def make_badges(product, relation_match, occasion_match, trait_matches):
    badges = []
    if relation_match and occasion_match:
        badges.append("상황적합")
    if trait_matches:
        badges.append(trait_matches[0])
    if product.get("wishCount", 0) >= 100000:
        badges.append("위시상위")
    if not badges and product.get("category"):
        badges.append(product["category"])
    return badges[:3]


def recommend(payload):
    preference = payload.get("preference", {})
    products = payload.get("candidateItems", [])
    click_categories, click_tags = summarize_clicks(payload.get("clickHistory", []))
    impression_categories, impression_tags = summarize_clicks(
        payload.get("recentBehavior", {}).get("impressionHistory", [])
    )
    preferred_traits = set(preference.get("traits", []))

    ranked = []
    for product in products:
        relation_match = preference.get("relation") in product.get("relations", [])
        occasion_match = preference.get("occasion") in product.get("occasions", [])
        trait_matches = [trait for trait in product.get("traits", []) if trait in preferred_traits]
        clicked_category = product.get("category") in click_categories
        clicked_tag = any(tag in click_tags for tag in product.get("tags", []))
        impression_category = product.get("category") in impression_categories
        impression_tag = any(tag in impression_tags for tag in product.get("tags", []))
        wish_score = min(product.get("wishCount", 0) / 100000, 2)
        score = (
            (4 if relation_match else 0)
            + (4 if occasion_match else 0)
            + (len(trait_matches) * 3)
            + (2 if clicked_category else 0)
            + (1.5 if clicked_tag else 0)
            + (0.8 if impression_category else 0)
            + (0.5 if impression_tag else 0)
            + wish_score
        )
        ranked.append(
            {
                "id": product["id"],
                "score": round(min(score / 14, 1), 4),
                "reason": make_reason(
                    product,
                    preference,
                    trait_matches,
                    clicked_category or impression_category,
                ),
                "badges": make_badges(product, relation_match, occasion_match, trait_matches),
                "_sortScore": score,
                "_wishCount": product.get("wishCount", 0),
            }
        )

    limit = int(payload.get("limit") or 8)
    ranked.sort(key=lambda item: (item["_sortScore"], item["_wishCount"]), reverse=True)
    recommendations = []
    for index, item in enumerate(ranked[:limit], start=1):
        item.pop("_sortScore", None)
        item.pop("_wishCount", None)
        item["rank"] = index
        recommendations.append(item)
    return recommendations


_LLM_CANDIDATE_CAP = 60


def llm_rank(payload):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    preference = payload.get("preference", {})
    click_history = payload.get("clickHistory", [])
    recent_behavior = payload.get("recentBehavior", {})
    candidates = payload.get("candidateItems", [])
    limit = int(payload.get("limit") or 8)

    # Pre-trim candidates to keep token count sane.
    if len(candidates) > _LLM_CANDIDATE_CAP:
        print(
            f"[core-api] llm_rank: trimming {len(candidates)} candidates to {_LLM_CANDIDATE_CAP} by wishCount"
        )
        candidates = sorted(candidates, key=lambda c: c.get("wishCount", 0), reverse=True)[
            :_LLM_CANDIDATE_CAP
        ]

    candidate_id_set = {c["id"] for c in candidates}

    slim_candidates = [
        {
            "id": c["id"],
            "name": c.get("name", ""),
            "brand": c.get("brand", ""),
            "category": c.get("category", ""),
            "priceValue": c.get("priceValue"),
            "wishCount": c.get("wishCount"),
            "tags": c.get("tags", []),
            "relations": c.get("relations", []),
            "occasions": c.get("occasions", []),
            "traits": c.get("traits", []),
        }
        for c in candidates
    ]

    system_prompt = (
        "You are a gift-recommendation ranker. "
        "Select and rank ONLY from the provided candidate list. "
        "Return valid json ONLY — no prose, no markdown — matching this schema: "
        '{"recommendations": [{"id": "<candidate id>", "reason": "<Korean, user-facing>", '
        '"badges": ["<short label>", ...], "score": <0.0-1.0 or null>}]}. '
        "Order by best fit first. Do not invent ids not in the candidate list."
    )

    user_message = json.dumps(
        {
            "preference": preference,
            "clickHistory": click_history,
            "recentBehavior": recent_behavior,
            "limit": limit,
            "candidates": slim_candidates,
        },
        ensure_ascii=False,
    )

    request_body = json.dumps(
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=18) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    content = raw["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    llm_items = parsed.get("recommendations", [])

    seen_ids = set()
    valid = []
    for item in llm_items:
        item_id = item.get("id")
        if not item_id or item_id not in candidate_id_set or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        # Find the source candidate for default-filling.
        src = next((c for c in candidates if c["id"] == item_id), {})
        reason = item.get("reason")
        if not reason or not isinstance(reason, str):
            reason = src.get("name", item_id)
        badges = item.get("badges")
        if not isinstance(badges, list):
            badges = src.get("traits", [])[:2]
        raw_score = item.get("score")
        score = None
        if raw_score is not None:
            try:
                score = round(max(0.0, min(1.0, float(raw_score))), 4)
            except (TypeError, ValueError):
                score = None
        valid.append({"id": item_id, "reason": reason, "badges": badges, "score": score})
        if len(valid) >= limit:
            break

    if not valid:
        raise ValueError("LLM returned zero valid candidate ids")

    for rank_index, item in enumerate(valid, start=1):
        item["rank"] = rank_index

    return valid, model


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            response(self, 200, {"ok": True, "service": "core-api", "version": "0.1.0"})
            return
        response(self, 404, {"error": {"code": "NOT_FOUND", "message": "not found"}})

    def do_POST(self):
        if self.path != "/v1/recommendations":
            response(self, 404, {"error": {"code": "NOT_FOUND", "message": "not found"}})
            return

        try:
            payload = read_json(self)
            if not payload.get("candidateItems"):
                response(
                    self,
                    400,
                    {
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": "candidateItems must not be empty",
                            "requestId": payload.get("requestId"),
                        }
                    },
                )
                return
            payload = with_recent_behavior(payload)
            try:
                recs, llm_model_name = llm_rank(payload)
                response(
                    self,
                    200,
                    {
                        "requestId": payload.get("requestId"),
                        "recommendations": recs,
                        "model": {"provider": "openai", "name": llm_model_name, "version": "0.1.0"},
                    },
                )
            except Exception as llm_exc:
                print(f"[core-api] llm fallback: {llm_exc}")
                response(
                    self,
                    200,
                    {
                        "requestId": payload.get("requestId"),
                        "recommendations": recommend(payload),
                        "model": {"provider": "local", "name": "candidate-ranker", "version": "0.1.0"},
                    },
                )
        except Exception as exc:
            response(self, 500, {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}})

    def log_message(self, format, *args):
        print(f"[core-api] {self.address_string()} {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"core-api listening on :{port}")
    server.serve_forever()
