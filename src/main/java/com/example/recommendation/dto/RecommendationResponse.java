package com.example.recommendation.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

/**
 * 추천 응답 DTO. LLM 이 생성한 JSON 을 그대로 매핑한다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RecommendationResponse(List<RecommendationItem> recommendations) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record RecommendationItem(
            String name,
            String category,
            int estimatedPrice,
            String reason
    ) {
    }
}
