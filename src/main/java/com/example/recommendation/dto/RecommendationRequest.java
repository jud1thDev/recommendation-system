package com.example.recommendation.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;

import java.util.List;

/**
 * 추천 요청 DTO. 스키마는 잠정이며 필드 추가/변경이 쉽도록 단순한 record 로 둔다.
 * 필수: relationship, interests, budgetMin, budgetMax.
 * 선택: occasion, gender, ageRange.
 */
public record RecommendationRequest(

        @NotBlank(message = "relationship 은 필수입니다")
        String relationship,

        @NotEmpty(message = "interests 는 최소 1개 이상이어야 합니다")
        List<@NotBlank String> interests,

        @NotNull(message = "budgetMin 은 필수입니다")
        Integer budgetMin,

        @NotNull(message = "budgetMax 은 필수입니다")
        Integer budgetMax,

        String occasion,
        String gender,
        String ageRange
) {
}
