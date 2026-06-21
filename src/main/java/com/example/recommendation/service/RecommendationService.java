package com.example.recommendation.service;

import com.example.recommendation.dto.RecommendationRequest;
import com.example.recommendation.dto.RecommendationResponse;

/**
 * 추천 서비스 경계.
 *
 * <p>인터페이스로 분리해 두는 이유: 나중에 캐싱 데코레이터(예: CachingRecommendationService implements
 * RecommendationService 가 이 구현을 감싸는 형태)를 끼울 수 있는 자리를 남기기 위함이다.
 * 이번 slice 에서는 LLM 생성 구현({@link LlmRecommendationService}) 하나만 존재한다.
 */
public interface RecommendationService {

    RecommendationResponse recommend(RecommendationRequest request);
}
