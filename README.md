# recommendation-system

선물 추천 서비스의 오케스트레이션 계층 vertical slice. 사용자 입력 → Claude LLM → 구조화 JSON 추천 응답.

## 요구사항

- **Java 17** 이상
- Gradle (./gradlew 포함)
- Anthropic API 키

## 환경 설정

### 필수: ANTHROPIC_API_KEY

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

SDK가 자동으로 이 환경변수를 읽습니다. 하드코딩하지 마세요.

### 선택: LLM 모델 및 토큰 설정

```bash
export LLM_MODEL=claude-opus-4-8          # 기본값: claude-opus-4-8
export LLM_MAX_TOKENS=1024                # 기본값: 1024
```

참고: `.env.example` 파일에서 템플릿을 확인하세요.

```bash
cp .env.example .env
# .env 파일 편집 후
source .env
```

## 실행

```bash
./gradlew bootRun
```

애플리케이션은 포트 8080에서 실행됩니다.

## API

### POST /recommend

선물 추천을 요청합니다.

**필수 필드:**
- `relationship` (string): 선물을 받는 사람과의 관계 (예: "연인", "친구", "가족")
- `interests` (string[]): 관심사 목록 (최소 1개)
- `budgetMin` (integer): 최소 예산 (KRW)
- `budgetMax` (integer): 최대 예산 (KRW)

**선택 필드:**
- `occasion` (string): 선물 기회/상황 (예: "생일", "기념일")
- `gender` (string): 성별 (예: "남성", "여성")
- `ageRange` (string): 나이 범위 (예: "20대", "30대")

**예시 요청:**

```bash
curl -s -X POST http://localhost:8080/recommend \
  -H 'Content-Type: application/json' \
  -d '{
    "relationship": "연인",
    "interests": ["커피", "영화"],
    "budgetMin": 30000,
    "budgetMax": 50000
  }' | jq .
```

**예시 응답:**

```json
{
  "recommendations": [
    {
      "name": "premium coffee gift set",
      "category": "food & beverage",
      "estimatedPrice": 45000,
      "reason": "연인의 커피 관심을 고려해 고급 원두 세트를 추천합니다."
    },
    {
      "name": "movie ticket + gourmet popcorn box",
      "category": "entertainment",
      "estimatedPrice": 38000,
      "reason": "영화를 좋아하는 연인을 위한 영화 티켓과 프리미엄 팝콘 세트입니다."
    },
    {
      "name": "coffee table book (cinema or coffee culture)",
      "category": "books & art",
      "estimatedPrice": 42000,
      "reason": "커피와 영화 문화를 담은 아트북으로, 두 관심사를 모두 담아냅니다."
    }
  ]
}
```

**응답 보장:**
- `recommendations` 배열은 최소 3개 이상의 추천 항목을 포함합니다.
- 모든 `estimatedPrice`는 요청의 `budgetMin`~`budgetMax` 범위 내입니다.

**에러 응답:**

요청 검증 실패 또는 LLM 호출 실패 시:
```json
{
  "error": "error message",
  "status": 400 | 500
}
```

## 아키텍처 메모

### 현재 구현

이번 slice에서:
- LLM 기반 추천만 구현 (카탈로그 `gifts.json` 미사용)
- 캐싱/프론트엔드 미포함
- 실시간 LLM 생성만 처리

### 확장 포인트

`RecommendationService`는 인터페이스로 분리되어 있어 나중에 캐싱 데코레이터를 끼울 수 있습니다:

```java
// 향후 추가 가능
public class CachingRecommendationService implements RecommendationService {
    private final RecommendationService delegate;
    // 캐싱 로직...
}
```

현재는 `LlmRecommendationService` 구현만 활성 상태입니다.

## 개발 및 테스트

### 빌드

```bash
./gradlew build
```

### 테스트 (미구현)

```bash
./gradlew test
```

## 스택

- **Spring Boot** 3.3.5
- **Java** 17
- **Anthropic SDK** 2.34.0
- **Gradle** (wrapper)