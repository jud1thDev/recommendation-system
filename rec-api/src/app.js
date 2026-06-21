const dataUrl = "/data/gifts.json";
const recommendationUrl = "/v1/recommendations";
const eventUrl = "/v1/events";
const userId = "user-001";
const sessionId = getSessionId();

const state = {
  products: [],
  clicks: JSON.parse(localStorage.getItem("gift-click-history") || "[]"),
  renderSeq: 0,
};

const form = document.querySelector("#preferenceForm");
const cards = document.querySelector("#cards");
const resultCount = document.querySelector("#resultCount");
const resetClicks = document.querySelector("#resetClicks");

const formatWish = new Intl.NumberFormat("ko-KR", {
  notation: "compact",
  maximumFractionDigits: 1,
});

init();

async function init() {
  const response = await fetch(dataUrl);
  const data = await response.json();
  state.products = data.products.map(toLlmItem);

  fillSelect("relation", uniqueFlat("relations"), "친구");
  fillSelect("occasion", uniqueFlat("occasions"), "생일");
  fillChips("traits", uniqueFlat("traits"));
  fillChips("avoidTags", pickAvoidTags());

  form.addEventListener("change", render);
  resetClicks.addEventListener("click", () => {
    state.clicks = [];
    localStorage.removeItem("gift-click-history");
    render();
  });

  render();
}

function toLlmItem(product) {
  return {
    id: product.id,
    name: product.name,
    brand: product.brandName,
    category: product.category,
    price: product.priceText,
    priceValue: product.priceValue,
    wishCount: product.wishCount,
    imageUrl: product.imageUrl,
    url: product.url,
    tags: product.filterTags || [],
    relations: product.relations || [],
    occasions: product.occasions || [],
    traits: product.traits || [],
  };
}

function uniqueFlat(key) {
  return [...new Set(state.products.flatMap((product) => product[key] || []))];
}

function fillSelect(id, values, selected) {
  const select = document.querySelector(`#${id}`);
  select.innerHTML = values
    .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
    .join("");
  select.value = selected;
}

function fillChips(id, values) {
  const node = document.querySelector(`#${id}`);
  node.innerHTML = values
    .map(
      (value) => `
        <label class="chip">
          <input type="checkbox" name="${id}" value="${escapeHtml(value)}" />
          <span>${escapeHtml(value)}</span>
        </label>
      `,
    )
    .join("");
}

function pickAvoidTags() {
  return ["배송상품", "교환권", "고가", "건강/비타민", "뷰티/향수/바디", "디저트/케이크"];
}

function getPreferences() {
  const formData = new FormData(form);
  return {
    relation: formData.get("relation"),
    occasion: formData.get("occasion"),
    budget: Number(formData.get("budget")),
    traits: formData.getAll("traits"),
    avoidTags: formData.getAll("avoidTags"),
    clickHistory: state.clicks,
  };
}

async function render() {
  const seq = ++state.renderSeq;
  const preferences = getPreferences();
  const recommendations = await requestRecommendations(preferences);
  if (seq !== state.renderSeq) return;

  resultCount.textContent = `${recommendations.length}개`;
  cards.innerHTML = recommendations.length
    ? recommendations.map(renderCard).join("")
    : `<div class="empty">조건에 맞는 상품이 없습니다.</div>`;

  recommendations.forEach((product) => collectEvent("product_impression", product));

  cards.querySelectorAll("[data-product-id]").forEach((card) => {
    card.addEventListener("click", () => saveClick(card.dataset.productId));
  });
}

async function requestRecommendations(preferences) {
  try {
    const response = await fetch(recommendationUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        requestId: `rec-${Date.now()}`,
        user: { userId },
        context: { sessionId },
        preference: {
          relation: preferences.relation,
          occasion: preferences.occasion,
          budget: preferences.budget,
          traits: preferences.traits,
          avoidTags: preferences.avoidTags,
        },
        clickHistory: preferences.clickHistory,
        limit: 8,
      }),
    });
    if (!response.ok) throw new Error(`recommendation request failed: ${response.status}`);
    const data = await response.json();
    return mergeRecommendations(data.recommendations || []);
  } catch (error) {
    console.warn(error);
    return recommend(preferences, state.products);
  }
}

function mergeRecommendations(recommendations) {
  const byId = new Map(state.products.map((product) => [product.id, product]));
  return recommendations
    .map((recommendation) => {
      const product = byId.get(recommendation.id);
      if (!product) return null;
      return {
        ...product,
        score: recommendation.score,
        reason: recommendation.reason || "추천 조건과 잘 맞는 상품입니다.",
        badges: (recommendation.badges || ["추천"]).map((label) => ({ label, tone: "strong" })),
      };
    })
    .filter(Boolean);
}

function recommend(preferences, products) {
  const clicked = summarizeClicks(preferences.clickHistory);

  return products
    .filter((product) => product.priceValue <= preferences.budget)
    .filter((product) => !preferences.avoidTags.some((tag) => product.tags.includes(tag)))
    .map((product) => {
      const relationMatch = product.relations.includes(preferences.relation);
      const occasionMatch = product.occasions.includes(preferences.occasion);
      const traitMatches = product.traits.filter((trait) => preferences.traits.includes(trait));
      const clickedCategory = clicked.categories.has(product.category);
      const clickedTag = product.tags.some((tag) => clicked.tags.has(tag));
      const wishScore = Math.min(product.wishCount / 100000, 2);

      const score =
        (relationMatch ? 4 : 0) +
        (occasionMatch ? 4 : 0) +
        traitMatches.length * 3 +
        (clickedCategory ? 2 : 0) +
        (clickedTag ? 1.5 : 0) +
        wishScore;

      return {
        ...product,
        score,
        reason: makeReason(product, preferences, traitMatches, clickedCategory),
        badges: makeBadges(product, relationMatch, occasionMatch, traitMatches),
      };
    })
    .sort((a, b) => b.score - a.score || b.wishCount - a.wishCount)
    .slice(0, 8);
}

function summarizeClicks(clickHistory) {
  return clickHistory.reduce(
    (summary, click) => {
      summary.categories.add(click.category);
      click.tags.forEach((tag) => summary.tags.add(tag));
      return summary;
    },
    { categories: new Set(), tags: new Set() },
  );
}

function makeReason(product, preferences, traitMatches, clickedCategory) {
  const reasons = [];
  if (product.relations.includes(preferences.relation)) reasons.push(`${preferences.relation}에게 무난한 선택`);
  if (product.occasions.includes(preferences.occasion)) reasons.push(`${preferences.occasion} 상황과 잘 맞음`);
  if (traitMatches.length) reasons.push(`${traitMatches.join(", ")} 성향 반영`);
  if (clickedCategory) reasons.push(`최근 본 ${product.category} 선호 반영`);
  if (!reasons.length) reasons.push("랭킹과 위시 수가 높은 상품");
  return reasons.slice(0, 2).join(" · ");
}

function makeBadges(product, relationMatch, occasionMatch, traitMatches) {
  const badges = [];
  if (relationMatch && occasionMatch) badges.push({ label: "상황적합", tone: "strong" });
  if (traitMatches.length) badges.push({ label: traitMatches[0], tone: "" });
  if (product.wishCount >= 100000) badges.push({ label: "위시상위", tone: "warn" });
  if (!badges.length) badges.push({ label: product.category, tone: "" });
  return badges.slice(0, 3);
}

function renderCard(product, index) {
  return `
    <article class="card" data-product-id="${escapeHtml(product.id)}">
      <div class="image-wrap">
        <img src="${escapeHtml(product.imageUrl)}" alt="${escapeHtml(product.name)}" loading="lazy" />
        <span class="rank">${index + 1}</span>
      </div>
      <div class="card-body">
        <div class="card-head">
          <div>
            <p class="brand">${escapeHtml(product.brand)}</p>
            <h3 class="name">${escapeHtml(product.name)}</h3>
          </div>
        </div>
        <div class="meta-row">
          <p class="price">${escapeHtml(product.price)}</p>
          <p class="wish">위시 ${formatWish.format(product.wishCount)}</p>
        </div>
        <div class="badges">
          ${product.badges
            .map((badge) => `<span class="badge ${badge.tone}">${escapeHtml(badge.label)}</span>`)
            .join("")}
        </div>
        <p class="reason">${escapeHtml(product.reason)}</p>
        <a class="link" href="${escapeHtml(product.url)}" target="_blank" rel="noreferrer">카카오 선물하기</a>
      </div>
    </article>
  `;
}

function saveClick(productId) {
  const product = state.products.find((item) => item.id === productId);
  if (!product) return;

  const click = {
    productId: product.id,
    category: product.category,
    tags: product.tags,
    priceValue: product.priceValue,
    clickedAt: new Date().toISOString(),
  };

  state.clicks = [
    click,
    ...state.clicks.filter((click) => click.productId !== product.id),
  ].slice(0, 12);

  localStorage.setItem("gift-click-history", JSON.stringify(state.clicks));
  collectEvent("product_click", product, click);
}

function collectEvent(type, product, click = {}) {
  fetch(eventUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      userId,
      type,
      occurredAt: click.clickedAt || new Date().toISOString(),
      context: { sessionId },
      product: {
        productId: product.id,
        category: product.category,
        tags: product.tags,
        priceValue: product.priceValue,
      },
    }),
  }).catch((error) => console.warn(error));
}

function getSessionId() {
  const existing = sessionStorage.getItem("gift-session-id");
  if (existing) return existing;
  const next = `session-${crypto.randomUUID()}`;
  sessionStorage.setItem("gift-session-id", next);
  return next;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

window.renderGiftRecommendations = function renderGiftRecommendations(recommendations) {
  const products = mergeRecommendations(recommendations);

  resultCount.textContent = `${products.length}개`;
  cards.innerHTML = products.map(renderCard).join("");
};
