import json
import math
import mimetypes
import os
import threading
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


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATIC_DIR = APP_DIR / "rec-api"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", DEFAULT_STATIC_DIR if DEFAULT_STATIC_DIR.exists() else APP_DIR))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
CORE_API_URL = os.environ.get("CORE_API_URL", "http://localhost:8001").rstrip("/")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
KAFKA_EVENTS_TOPIC = os.environ.get("KAFKA_EVENTS_TOPIC", "rec-events")
ALLOWED_EVENT_TYPES = {"product_impression", "product_click", "add_to_cart"}
EVENT_FILE_LOCK = threading.Lock()
CANDIDATE_K = int(os.environ.get("CANDIDATE_K", "36"))

KAFKA_PRODUCER = None


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
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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
        "traits": (product.get("recommendationMeta") or {}).get("traits") or product.get("traits", []),
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


def generate_candidates(preference, filtered, k):
    """Select top-K candidates from the filtered pool using relevance + diversity (R4).

    Relevance formula:
        rel = (3 if preference.relation in p.relations else 0)
            + (3 if preference.occasion in p.occasions else 0)
            + 2 * |preference.traits ∩ p.traits|
            + min(p.wishCount / 100000, 2)

    Diversity: cap per-category at cat_cap = max(1, ceil(k * 0.6)).
    If fewer than k items chosen after cap pass, fill remainder ignoring the cap.
    Always returns min(k, len(filtered)) items.
    """
    relation = preference.get("relation", "")
    occasion = preference.get("occasion", "")
    pref_traits = set(preference.get("traits", []))

    def relevance(p):
        rel = 0
        if relation and relation in p.get("relations", []):
            rel += 3
        if occasion and occasion in p.get("occasions", []):
            rel += 3
        rel += 2 * len(pref_traits & set(p.get("traits", [])))
        rel += min(p.get("wishCount", 0) / 100000, 2)
        return rel

    sorted_items = sorted(filtered, key=lambda p: (relevance(p), p.get("wishCount", 0)), reverse=True)

    cat_cap = max(1, math.ceil(k * 0.6))
    chosen = []
    cat_counts = {}

    for p in sorted_items:
        if len(chosen) >= k:
            break
        cat = p.get("category", "")
        if cat_counts.get(cat, 0) < cat_cap:
            chosen.append(p)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # Fill remainder ignoring cap if we still need more
    if len(chosen) < k:
        chosen_ids = {p["id"] for p in chosen}
        for p in sorted_items:
            if len(chosen) >= k:
                break
            if p["id"] not in chosen_ids:
                chosen.append(p)

    return chosen


def build_event(payload):
    request_id = payload.get("requestId") or payload.get("context", {}).get("requestId")
    impression_id = payload.get("impressionId") or payload.get("context", {}).get("impressionId")
    event = {
        "eventId": payload.get("eventId") or f"evt-{uuid4().hex[:12]}",
        "userId": payload.get("userId") or "user-001",
        "type": payload.get("type") or "unknown",
        "occurredAt": payload.get("occurredAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requestId": request_id,
        "impressionId": impression_id,
        "eventKey": payload.get("eventKey") or impression_id or request_id,
        "context": payload.get("context", {}),
        "product": payload.get("product"),
        "properties": payload.get("properties", {}),
    }
    return event


def append_event(event):
    with EVENT_FILE_LOCK:
        events = load_json("user-events.json", {"schemaVersion": "1.0", "events": []})
        events.setdefault("schemaVersion", "1.0")
        events.setdefault("events", []).append(event)
        write_json("user-events.json", events)


def try_append_event(event):
    try:
        append_event(event)
        return True
    except Exception as exc:
        print(f"[rec-api] failed to append event locally: {exc}")
        return False


def get_kafka_producer():
    global KAFKA_PRODUCER
    if not KAFKA_BOOTSTRAP_SERVERS or KafkaProducer is None:
        return None
    if KAFKA_PRODUCER is None:
        KAFKA_PRODUCER = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            key_serializer=lambda value: str(value).encode("utf-8"),
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        )
    return KAFKA_PRODUCER


def kafka_event_key(event):
    return event.get("eventKey") or event.get("impressionId") or event.get("requestId") or event.get("eventId")


def publish_event(event):
    try:
        producer = get_kafka_producer()
        if producer is None:
            return False
        producer.send(KAFKA_EVENTS_TOPIC, key=kafka_event_key(event), value=event).get(timeout=5)
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
    with urllib.request.urlopen(request, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
                event = build_event(read_json(self))
                if event["type"] not in ALLOWED_EVENT_TYPES:
                    send_json(
                        self,
                        400,
                        {
                            "error": {
                                "code": "INVALID_EVENT_TYPE",
                                "message": "type must be one of product_impression, product_click, add_to_cart",
                            }
                        },
                    )
                    return
                kafkaPublished = publish_event(event)
                localPersisted = try_append_event(event)
                send_json(
                    self,
                    201,
                    {"event": event, "kafkaPublished": kafkaPublished, "localPersisted": localPersisted},
                )
                return

            if self.path == "/v1/recommendations":
                incoming = read_json(self)
                preference = incoming.get("preference", {})
                limit = int(incoming.get("limit") or 8)

                filtered = build_candidates(preference)
                if not filtered:
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

                candidates = generate_candidates(preference, filtered, CANDIDATE_K)
                request_id = incoming.get("requestId") or f"rec-{uuid4().hex[:12]}"

                payload = {
                    "requestId": request_id,
                    "user": incoming.get("user") or {"userId": "user-001"},
                    "preference": preference,
                    "clickHistory": incoming.get("clickHistory", []),
                    "candidateItems": candidates,
                    "limit": limit,
                }

                try:
                    result = call_core_api(payload)
                    send_json(self, 200, {**result, "candidateCount": len(candidates)})
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"[rec-api] core-api unavailable, popularity fallback: {exc}")
                    picked = sorted(candidates, key=lambda c: c.get("wishCount", 0), reverse=True)[:limit]
                    recs = [
                        {
                            "id": c["id"],
                            "rank": i,
                            "score": None,
                            "reason": "추천 엔진 연결 전이라 인기도 기준으로 추천했습니다.",
                            "badges": ["인기", c.get("category", "추천")],
                        }
                        for i, c in enumerate(picked, start=1)
                    ]
                    send_json(
                        self,
                        200,
                        {
                            "requestId": request_id,
                            "recommendations": recs,
                            "model": {
                                "provider": "rec-api",
                                "name": "fallback-popularity",
                                "version": "0.2.0",
                            },
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
