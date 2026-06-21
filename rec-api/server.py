import json
import math
import mimetypes
import os
import random
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

try:
    from kafka import KafkaProducer
except ImportError:
    KafkaProducer = None

try:
    from redis import Redis
except ImportError:
    Redis = None


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATIC_DIR = APP_DIR / "rec-api"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", DEFAULT_STATIC_DIR if DEFAULT_STATIC_DIR.exists() else APP_DIR))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
CORE_API_URL = os.environ.get("CORE_API_URL", "http://localhost:8001").rstrip("/")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
KAFKA_EVENTS_TOPIC = os.environ.get("KAFKA_EVENTS_TOPIC", "rec-events")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

KAFKA_PRODUCER = None
REDIS_CLIENT = None

# Fusion weights — overridable via env for A/B experiments
W_LLM = float(os.environ.get("W_LLM", "0.5"))
W_RT = float(os.environ.get("W_RT", "0.4"))
W_POP = float(os.environ.get("W_POP", "0.1"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def send_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_file(handler, path):
    content = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def load_json(filename, fallback):
    path = DATA_DIR / filename
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(filename, payload):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def to_candidate(product):
    return {
        "id": product["id"],
        "name": product["name"],
        "brand": product.get("brandName", ""),
        "category": product.get("category", ""),
        "price": product.get("priceText", ""),
        "priceValue": product.get("priceValue", 0),
        "wishCount": product.get("wishCount", 0),
        "imageUrl": product.get("imageUrl", ""),
        "url": product.get("url", ""),
        "tags": product.get("filterTags", []),
        "relations": product.get("relations", []),
        "occasions": product.get("occasions", []),
        "traits": product.get("traits", []),
    }


def build_candidates(preference):
    gifts = load_json("gifts.json", {"products": []})
    budget = int(preference.get("budget") or 999999999)
    avoid_tags = set(preference.get("avoidTags", []))
    candidates = []

    for product in gifts.get("products", []):
        item = to_candidate(product)
        if item["priceValue"] > budget:
            continue
        if any(tag in avoid_tags for tag in item["tags"]):
            continue
        candidates.append(item)

    return candidates


def append_event(payload):
    event = {
        "eventId": payload.get("eventId") or f"evt-{uuid4().hex[:12]}",
        "userId": payload.get("userId") or "user-001",
        "type": payload.get("type") or "unknown",
        "occurredAt": payload.get("occurredAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "context": payload.get("context", {}),
        "product": payload.get("product"),
        "properties": payload.get("properties", {}),
    }
    events = load_json("user-events.json", {"schemaVersion": "1.0", "events": []})
    events.setdefault("schemaVersion", "1.0")
    events.setdefault("events", []).append(event)
    write_json("user-events.json", events)
    return event


def get_kafka_producer():
    global KAFKA_PRODUCER
    if not KAFKA_BOOTSTRAP_SERVERS or KafkaProducer is None:
        return None
    if KAFKA_PRODUCER is None:
        KAFKA_PRODUCER = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        )
    return KAFKA_PRODUCER


def publish_event(event):
    try:
        producer = get_kafka_producer()
        if producer is None:
            return False
        producer.send(KAFKA_EVENTS_TOPIC, event).get(timeout=5)
        return True
    except Exception as exc:
        print(f"[rec-api] failed to publish event to kafka: {exc}")
        return False


def call_core_api(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CORE_API_URL}/v1/recommendations",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Redis / online feature store
# ---------------------------------------------------------------------------

def get_redis_client():
    global REDIS_CLIENT
    if Redis is None:
        return None
    if REDIS_CLIENT is None:
        try:
            REDIS_CLIENT = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        except Exception as exc:
            print(f"[rec-api] redis connect error: {exc}")
            return None
    return REDIS_CLIENT


def load_user_history(user_id, limit=20):
    """Return up to `limit` recent events (newest-first) for `user_id`.

    Combines clicks (weight 1.0) and carts (weight 3.0).  Impressions are
    intentionally omitted here because their weight is so low that the scoring
    function already handles any affinity signal they carry; including them
    would just add Redis round-trips.

    Returns [] on any error or when user_id is falsy.
    """
    if not user_id:
        return []
    client = get_redis_client()
    if client is None:
        return []
    try:
        raw_clicks = client.lrange(f"clicks:user:{user_id}", 0, limit - 1) or []
        raw_carts = client.lrange(f"carts:user:{user_id}", 0, limit - 1) or []
        events = []
        for raw in raw_clicks:
            try:
                ev = json.loads(raw)
                ev["_type_key"] = "click"
                events.append(ev)
            except Exception:
                pass
        for raw in raw_carts:
            try:
                ev = json.loads(raw)
                ev["_type_key"] = "cart"
                events.append(ev)
            except Exception:
                pass
        # Sort newest-first by occurredAt; fall back to stable order on parse fail
        events.sort(key=lambda e: e.get("occurredAt", ""), reverse=True)
        return events[:limit]
    except Exception as exc:
        print(f"[rec-api] redis history error for {user_id}: {exc}")
        return []


# ---------------------------------------------------------------------------
# Realtime scoring
# ---------------------------------------------------------------------------

_TYPE_WEIGHT = {"cart": 3.0, "click": 1.0, "impression": 0.2}


def realtime_scores(candidates, history):
    """Return {candidateId: score 0..1} driven by recent user behavior.

    Recency-decayed affinity is accumulated per category and tag, then summed
    for each candidate.  The most-recent event's category gets a large bonus
    to implement "방금 본 거 확 끌어올림".
    """
    if not history or not candidates:
        return {c["id"]: 0.0 for c in candidates}

    affinity = {}  # key -> accumulated weight

    top_category = None
    top_event_weight = 0.0

    for i, event in enumerate(history):
        event_type = event.get("type", "")
        # Map stored event type names to weight keys
        if event_type == "product_click" or event.get("_type_key") == "click":
            type_weight = _TYPE_WEIGHT["click"]
        elif event_type == "add_to_cart" or event.get("_type_key") == "cart":
            type_weight = _TYPE_WEIGHT["cart"]
        elif event_type == "product_impression":
            type_weight = _TYPE_WEIGHT["impression"]
        else:
            type_weight = _TYPE_WEIGHT["click"]

        w = type_weight * (0.7 ** i)

        product = event.get("product", {})
        category = product.get("category", "")
        tags = product.get("tags", [])

        if category:
            affinity[category] = affinity.get(category, 0.0) + w
            if i == 0:
                top_category = category
                top_event_weight = w

        for tag in tags:
            affinity[tag] = affinity.get(tag, 0.0) + w

    raw_scores = {}
    for c in candidates:
        cid = c["id"]
        cat = c.get("category", "")
        tags = c.get("tags", [])

        raw = affinity.get(cat, 0.0)
        for tag in tags:
            raw += affinity.get(tag, 0.0)

        # Big recency boost for the most-recently viewed category
        if top_category and cat == top_category:
            raw += 5.0 * top_event_weight

        raw_scores[cid] = raw

    # Max-normalize to [0, 1]
    max_raw = max(raw_scores.values()) if raw_scores else 0.0
    if max_raw == 0.0:
        return {cid: 0.0 for cid in raw_scores}
    return {cid: raw / max_raw for cid, raw in raw_scores.items()}


# ---------------------------------------------------------------------------
# Popularity prior
# ---------------------------------------------------------------------------

def pop_prior(candidates):
    """Return {candidateId: score 0..1} based on log1p(wishCount)."""
    if not candidates:
        return {}
    log_wishes = {c["id"]: math.log1p(c.get("wishCount", 0)) for c in candidates}
    max_val = max(log_wishes.values()) if log_wishes else 0.0
    if max_val == 0.0:
        return {cid: 0.0 for cid in log_wishes}
    return {cid: v / max_val for cid, v in log_wishes.items()}


# ---------------------------------------------------------------------------
# Fusion helpers
# ---------------------------------------------------------------------------

def _rt_is_high(rt_score, rt_scores_dict):
    """True when this candidate's rt score is in the top quartile."""
    if not rt_scores_dict:
        return False
    values = list(rt_scores_dict.values())
    threshold = sorted(values, reverse=True)[max(0, len(values) // 4)]
    return rt_score >= threshold and rt_score > 0.0


def fuse_and_rank(candidates, llm_result, rt_scores, pop_scores, limit):
    """Blend LLM, realtime, and popularity scores; return top `limit` items."""
    llm_map = {}   # id -> {score, reason, badges}
    llm_model_name = "openai"

    if llm_result is not None:
        llm_model_name = llm_result.get("model", {}).get("name", "openai")
        for rec in llm_result.get("recommendations", []):
            rid = rec.get("id")
            if rid:
                llm_map[rid] = {
                    "score": rec.get("score") or 0.0,
                    "reason": rec.get("reason", ""),
                    "badges": rec.get("badges", []),
                }

    # Normalize LLM scores to [0,1] — they should already be, but guard anyway
    llm_values = [v["score"] for v in llm_map.values()]
    llm_max = max(llm_values) if llm_values else 1.0
    if llm_max == 0.0:
        llm_max = 1.0

    fused = []
    for c in candidates:
        cid = c["id"]
        llm_entry = llm_map.get(cid)
        llm_s = (llm_entry["score"] / llm_max) if llm_entry else 0.0
        rt_s = rt_scores.get(cid, 0.0)
        pop_s = pop_scores.get(cid, 0.0)

        final = W_LLM * llm_s + W_RT * rt_s + W_POP * pop_s

        # Reason / badges
        if llm_entry and llm_entry.get("reason"):
            reason = llm_entry["reason"]
            badges = list(llm_entry.get("badges") or [])
        else:
            reason = "최근 관심사와 비슷해 실시간 추천"
            badges = ["실시간", c.get("category", "추천")]

        # Append 실시간 badge when realtime signal is strong
        if _rt_is_high(rt_s, rt_scores) and "실시간" not in badges:
            badges.append("실시간")

        fused.append({
            "id": cid,
            "score": round(max(0.0, min(1.0, final)), 4),
            "reason": reason,
            "badges": badges[:3],
            "_final": final,
        })

    fused.sort(key=lambda x: x["_final"], reverse=True)
    results = []
    for rank_idx, item in enumerate(fused[:limit], start=1):
        item.pop("_final")
        item["rank"] = rank_idx
        results.append(item)

    return results, llm_model_name


def realtime_only_rank(candidates, rt_scores, pop_scores, limit):
    """Fallback ranking when core-api is unavailable."""
    ranked = []
    for c in candidates:
        cid = c["id"]
        rt_s = rt_scores.get(cid, 0.0)
        pop_s = pop_scores.get(cid, 0.0)
        final = W_RT * rt_s + W_POP * pop_s
        ranked.append({
            "id": cid,
            "score": round(max(0.0, min(1.0, final)), 4),
            "reason": "실시간 신호 기반 추천",
            "badges": ["실시간", c.get("category", "추천")],
            "_final": final,
        })
    ranked.sort(key=lambda x: x["_final"], reverse=True)
    results = []
    for rank_idx, item in enumerate(ranked[:limit], start=1):
        item.pop("_final")
        item["rank"] = rank_idx
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            send_json(self, 200, {"ok": True, "service": "rec-api", "coreApiUrl": CORE_API_URL})
            return
        if self.path in ("/data/gifts.json", "/data/users.json", "/data/user-events.json"):
            filename = self.path.rsplit("/", 1)[1]
            path = DATA_DIR / filename
            if path.exists():
                send_file(self, path)
                return
            send_json(self, 404, {"error": {"code": "NOT_FOUND", "message": f"{filename} not found"}})
            return
        self.serve_static()

    def do_POST(self):
        try:
            if self.path == "/v1/events":
                event = append_event(read_json(self))
                kafkaPublished = publish_event(event)
                send_json(self, 201, {"event": event, "kafkaPublished": kafkaPublished})
                return

            if self.path == "/v1/recommendations":
                incoming = read_json(self)
                preference = incoming.get("preference", {})
                limit = int(incoming.get("limit") or 8)
                user_info = incoming.get("user") or {"userId": "user-001"}
                user_id = user_info.get("userId", "")

                candidates = build_candidates(preference)
                if not candidates:
                    send_json(
                        self,
                        400,
                        {
                            "error": {
                                "code": "NO_CANDIDATES",
                                "message": "No products remain after rec-api filtering",
                                "requestId": incoming.get("requestId") or f"rec-{uuid4().hex[:12]}",
                            }
                        },
                    )
                    return

                request_id = incoming.get("requestId") or f"rec-{uuid4().hex[:12]}"

                # --- Online feature store: recent user behavior ---
                history = load_user_history(user_id)
                rt_scores = realtime_scores(candidates, history)
                pop_scores = pop_prior(candidates)

                # --- Call core-api with an expanded pool ---
                pool_limit = min(len(candidates), max(limit * 2, 12))
                core_payload = {
                    "requestId": request_id,
                    "user": user_info,
                    "preference": preference,
                    "clickHistory": incoming.get("clickHistory", []),
                    "candidateItems": candidates,
                    "limit": pool_limit,
                }

                try:
                    llm_result = call_core_api(core_payload)
                    recs, llm_model_name = fuse_and_rank(candidates, llm_result, rt_scores, pop_scores, limit)
                    model_info = {
                        "provider": "fusion",
                        "name": f"realtime+{llm_model_name}",
                        "version": "0.2.0",
                        "components": ["realtime", "openai"],
                        "weights": {"llm": W_LLM, "rt": W_RT, "pop": W_POP},
                    }
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"[rec-api] core-api unavailable, realtime fallback: {exc}")
                    recs = realtime_only_rank(candidates, rt_scores, pop_scores, limit)
                    model_info = {
                        "provider": "rec-api",
                        "name": "realtime-fallback",
                        "version": "0.2.0",
                    }

                send_json(
                    self,
                    200,
                    {
                        "requestId": request_id,
                        "recommendations": recs,
                        "model": model_info,
                        "candidateCount": len(candidates),
                    },
                )
                return

            send_json(self, 404, {"error": {"code": "NOT_FOUND", "message": "not found"}})
        except Exception as exc:
            send_json(self, 500, {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}})

    def serve_static(self):
        raw_path = self.path.split("?", 1)[0]
        if raw_path == "/":
            raw_path = "/index.html"
        if raw_path == "/rec-api":
            self.send_response(302)
            self.send_header("Location", "/rec-api/")
            self.end_headers()
            return
        if raw_path.startswith("/rec-api/"):
            raw_path = raw_path[len("/rec-api"):]
            if raw_path == "/":
                raw_path = "/index.html"
        target = (STATIC_DIR / raw_path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or target.is_dir():
            send_json(self, 404, {"error": {"code": "NOT_FOUND", "message": "not found"}})
            return
        send_file(self, target)

    def log_message(self, format, *args):
        print(f"[rec-api] {self.address_string()} {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"rec-api listening on :{port}, core-api={CORE_API_URL}")
    server.serve_forever()
