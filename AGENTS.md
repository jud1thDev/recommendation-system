# AGENTS.md

## Project Context

This is a 2-hour hackathon project for a Kakao Gift recommendation system.

Goal:
- Recommend Kakao Gift products based on recipient relation, occasion, budget, taste traits, avoid conditions, and click history.
- The recommendation decision is expected to come from the LLM SDK side.
- The UI should render recommendation results as product cards.
- Current user role: UI/B.
- Teammate role: recommendation logic / LLM SDK.

## Data

Primary data file:
- `data/gifts.json`

User recommendation mock data:
- `data/users.json`

User event mock data:
- `data/user-events.json`

Source:
- Kakao Gift home real-time gift ranking from `https://gift.kakao.com/home`

Current data notes:
- Product count: 200.
- Validated no missing values for `imageUrl`, `priceValue`, `brandName`, `name`, `url`, `category`, `filterTags`, `relations`, and `occasions`.
- Product IDs are deduplicated by `productId`.

Top-level JSON shape:
- `metadata`
- `products`

Useful product fields:
- `id`
- `productId`
- `name`
- `title`
- `brand`
- `brandName`
- `category`
- `productType`
- `deliveryType`
- `price`
- `priceValue`
- `priceText`
- `image`
- `imageUrl`
- `url`
- `wish`
- `wishCount`
- `review`
- `labels`
- `tags`
- `filterTags`
- `relations`
- `occasions`
- `traits`
- `recommendationMeta`
- `raw`

Recommended LLM item mapping:

```js
const llmItems = products.map((p) => ({
  id: p.id,
  name: p.name,
  brand: p.brandName,
  category: p.category,
  price: p.priceText,
  priceValue: p.priceValue,
  wishCount: p.wishCount,
  imageUrl: p.imageUrl,
  url: p.url,
  tags: p.filterTags,
  relations: p.relations,
  occasions: p.occasions,
  traits: p.traits,
}));
```

## User Inputs

Expected recommendation inputs:
- `relation`: recipient relationship
- `occasion`: gifting situation
- `budget`: budget
- `traits`: preferred recipient traits
- `avoidTags`: conditions to avoid
- `clickHistory`: clicked product/category/tag/price information

## Recommendation Result UI

Show these fields in recommendation cards:
- Product image
- Brand name
- Product name
- Price
- Wish count
- Recommendation reason
- Recommendation badges
- Kakao Gift product link

## Current Implementation

The current UI is a static HTML/CSS/JS prototype:
- `rec-api/index.html`
- `rec-api/src/styles.css`
- `rec-api/src/app.js`
- `data/gifts.json`

Service scaffolding:
- `rec-api/server.py`
- `core-api/server.py`
- `docker-compose.yml`

Behavior:
- Loads `data/gifts.json` in the browser.
- Sends recommendation requests to `rec-api` at `/v1/recommendations`.
- `rec-api` sends already-filtered candidate products to `core-api`.
- Collects product click events through `rec-api` at `/v1/events`.
- Renders relation, occasion, budget, preferred-trait, and avoid-tag controls.
- Falls back to temporary local scoring if the API request fails.
- Renders up to 8 recommendation cards.
- Saves click history in `localStorage` under `gift-click-history`.
- Exposes `window.renderGiftRecommendations(recommendations)` for future SDK integration.

Expected SDK integration shape:

```js
window.renderGiftRecommendations([
  {
    id: "kakao-2301047",
    reason: "추천 이유",
    badges: ["상황적합", "실용적"],
  },
]);
```

## Verification

Docker Compose can be used for local verification:

```sh
docker compose up --build
```

Then open:
- `http://localhost:8000`
- `http://localhost:8000/rec-api/`

Last verified behavior:
- `data/gifts.json` contains 200 products.
- `productId` unique count is 200.
- Required UI/LLM fields have no missing values.
- Initial render shows 8 cards.
- Browser console had no errors.
- Budget and trait filters worked.
- Mobile width around 390px had no horizontal overflow.

## Working Guidelines

- Keep changes surgical. This is a hackathon prototype, so prefer small, direct changes over broad refactors.
- Do not edit `data/gifts.json` unless the task specifically asks for data changes.
- Keep UI fields aligned with the LLM item mapping above so the SDK side can connect quickly.
- If replacing temporary local scoring with SDK results, preserve the card rendering contract where possible.
- Existing `git status` may show `D gifts.json` at repository root. That deletion predates the UI work; do not restore or modify it unless explicitly asked.
