import json
import os
import time

from kafka import KafkaConsumer
from redis import Redis


KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_EVENTS_TOPIC = os.environ.get("KAFKA_EVENTS_TOPIC", "rec-events")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
MAX_EVENTS_PER_KEY = int(os.environ.get("MAX_EVENTS_PER_KEY", "200"))
EVENT_KEY_PREFIXES = {
    "product_impression": "impressions",
    "product_click": "clicks",
    "add_to_cart": "carts",
}


def connect_consumer():
    while True:
        try:
            return KafkaConsumer(
                KAFKA_EVENTS_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                group_id="rec-event-worker",
                value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            )
        except Exception as exc:
            print(f"[event-worker] waiting for kafka: {exc}")
            time.sleep(3)


def trim_list(redis, key):
    redis.ltrim(key, 0, MAX_EVENTS_PER_KEY - 1)


def push_event(redis, key, event_json):
    redis.lpush(key, event_json)
    trim_list(redis, key)


def store_event(redis, event):
    event_json = json.dumps(event, ensure_ascii=False)
    user_id = event.get("userId")
    session_id = event.get("context", {}).get("sessionId")
    event_type = event.get("type", "unknown")

    push_event(redis, "events:all", event_json)
    push_event(redis, f"events:type:{event_type}", event_json)

    if user_id:
        push_event(redis, f"events:user:{user_id}", event_json)
        event_prefix = EVENT_KEY_PREFIXES.get(event_type)
        if event_prefix:
            push_event(redis, f"{event_prefix}:user:{user_id}", event_json)

    if session_id:
        push_event(redis, f"events:session:{session_id}", event_json)
        event_prefix = EVENT_KEY_PREFIXES.get(event_type)
        if event_prefix:
            push_event(redis, f"{event_prefix}:session:{session_id}", event_json)


def main():
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    consumer = connect_consumer()
    print(f"[event-worker] consuming {KAFKA_EVENTS_TOPIC} from {KAFKA_BOOTSTRAP_SERVERS}")

    for message in consumer:
        event = message.value
        try:
            store_event(redis, event)
            print(
                "[event-worker] stored "
                f"type={event.get('type')} userId={event.get('userId')} "
                f"sessionId={event.get('context', {}).get('sessionId')}"
            )
        except Exception as exc:
            print(f"[event-worker] failed to store event: {exc}")


if __name__ == "__main__":
    main()
