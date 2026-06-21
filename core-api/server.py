import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


def make_reason(product, preference, trait_matches, clicked_category):
    reasons = []
    relation = preference.get("relation")
    occasion = preference.get("occasion")
    if relation and relation in product.get("relations", []):
        reasons.append(f"{relation}에게 무난한 선택")
    if occasion and occasion in product.get("occasions", []):
        reasons.append(f"{occasion} 상황과 잘 맞음")
    if trait_matches:
        reasons.append(f"{', '.join(trait_matches)} 성향 반영")
    if clicked_category:
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
    preferred_traits = set(preference.get("traits", []))

    ranked = []
    for product in products:
        relation_match = preference.get("relation") in product.get("relations", [])
        occasion_match = preference.get("occasion") in product.get("occasions", [])
        trait_matches = [trait for trait in product.get("traits", []) if trait in preferred_traits]
        clicked_category = product.get("category") in click_categories
        clicked_tag = any(tag in click_tags for tag in product.get("tags", []))
        wish_score = min(product.get("wishCount", 0) / 100000, 2)
        score = (
            (4 if relation_match else 0)
            + (4 if occasion_match else 0)
            + (len(trait_matches) * 3)
            + (2 if clicked_category else 0)
            + (1.5 if clicked_tag else 0)
            + wish_score
        )
        ranked.append(
            {
                "id": product["id"],
                "score": round(min(score / 14, 1), 4),
                "reason": make_reason(product, preference, trait_matches, clicked_category),
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
