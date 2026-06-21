package com.example.recommendation.service;

/**
 * LLM 호출 실패 또는 응답 파싱 실패를 나타낸다. 전역 예외 처리기에서 500 으로 매핑된다.
 */
public class RecommendationException extends RuntimeException {

    public RecommendationException(String message) {
        super(message);
    }

    public RecommendationException(String message, Throwable cause) {
        super(message, cause);
    }
}
