import json
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


APP_DIR = Path(__file__).resolve().parent
DEFAULT_STATIC_DIR = APP_DIR / "rec-api"
STATIC_DIR = Path(os.environ.get("STATIC_DIR", DEFAULT_STATIC_DIR if DEFAULT_STATIC_DIR.exists() else APP_DIR))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
CORE_API_URL = os.environ.get("CORE_API_URL", "http://localhost:8001").rstrip("/")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
KAFKA_EVENTS_TOPIC = os.environ.get("KAFKA_EVENTS_TOPIC", "rec-events")
KAFKA_PRODUCER = None


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
    producer = get_kafka_producer()
    if producer is None:
        return False
    producer.send(KAFKA_EVENTS_TOPIC, event).get(timeout=5)
    return True


def call_core_api(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{CORE_API_URL}/v1/recommendations",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fallback_response(payload):
    candidates = payload["candidateItems"]
    limit = payload["limit"]
    picked = random.sample(candidates, k=min(limit, len(candidates)))
    recommendations = [
        {
            "id": item["id"],
            "rank": index,
            "score": None,
            "reason": "inference 응답이 없어 후보군에서 랜덤으로 추천했습니다.",
            "badges": ["fallback", item.get("category", "추천")],
        }
        for index, item in enumerate(picked, start=1)
    ]
    return {
        "requestId": payload["requestId"],
        "recommendations": recommendations,
        "model": {"provider": "rec-api", "name": "fallback-random", "version": "0.1.0"},
    }


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
                payload = {
                    "requestId": incoming.get("requestId") or f"rec-{uuid4().hex[:12]}",
                    "user": incoming.get("user") or {"userId": "user-001"},
                    "preference": preference,
                    "clickHistory": incoming.get("clickHistory", []),
                    "candidateItems": build_candidates(preference),
                    "limit": int(incoming.get("limit") or 8),
                }
                if not payload["candidateItems"]:
                    send_json(
                        self,
                        400,
                        {
                            "error": {
                                "code": "NO_CANDIDATES",
                                "message": "No products remain after rec-api filtering",
                                "requestId": payload["requestId"],
                            }
                        },
                    )
                    return
                try:
                    result = call_core_api(payload)
                except (urllib.error.URLError, TimeoutError):
                    result = fallback_response(payload)
                send_json(self, 200, {**result, "candidateCount": len(payload["candidateItems"])})
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
            raw_path = raw_path[len("/rec-api") :]
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
