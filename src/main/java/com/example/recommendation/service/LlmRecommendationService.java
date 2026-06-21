package com.example.recommendation.service;

import com.example.recommendation.dto.RecommendationRequest;
import com.example.recommendation.dto.RecommendationResponse;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

@Service
public class LlmRecommendationService implements RecommendationService {

    @Value("${openai.base-url:https://api.openai.com/v1}")
    private String baseUrl;

    @Value("${openai.model:gpt-4o-mini}")
    private String model;

    @Value("${openai.max-tokens:1024}")
    private int maxTokens;

    private final ObjectMapper objectMapper;

    public LlmRecommendationService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public RecommendationResponse recommend(RecommendationRequest request) {
        if (request.budgetMin() > request.budgetMax()) {
            throw new IllegalArgumentException(
                    "budgetMin (" + request.budgetMin() + ") must not exceed budgetMax (" + request.budgetMax() + ")");
        }

        RecommendationResponse response = callLlm(request);
        if (response == null || response.recommendations() == null || response.recommendations().size() < 3) {
            response = callLlm(request);
            if (response == null || response.recommendations() == null || response.recommendations().size() < 3) {
                throw new RecommendationException("LLM returned fewer than 3 recommendation items after retry");
            }
        }
        return response;
    }

    private RecommendationResponse callLlm(RecommendationRequest request) {
        String apiKey = System.getenv("OPENAI_API_KEY");

        Map<String, Object> requestBody = Map.of(
                "model", model,
                "max_tokens", maxTokens,
                "response_format", Map.of("type", "json_object"),
                "messages", List.of(
                        Map.of("role", "system", "content", buildSystemPrompt(request)),
                        Map.of("role", "user", "content", buildUserPrompt(request))
                )
        );

        String rawJson;
        try {
            String responseBody = RestClient.create()
                    .post()
                    .uri(baseUrl + "/chat/completions")
                    .header("Authorization", "Bearer " + apiKey)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(objectMapper.writeValueAsString(requestBody))
                    .retrieve()
                    .body(String.class);

            JsonNode root = objectMapper.readTree(responseBody);
            rawJson = root.at("/choices/0/message/content").asText();
        } catch (Exception e) {
            throw new RecommendationException("LLM API call failed: " + e.getMessage(), e);
        }

        return parseResponse(rawJson);
    }

    private RecommendationResponse parseResponse(String rawText) {
        String json = stripToJson(rawText);
        try {
            RecommendationResponse response = objectMapper.readValue(json, RecommendationResponse.class);
            if (response == null || response.recommendations() == null || response.recommendations().size() < 3) {
                return null;
            }
            return response;
        } catch (Exception e) {
            return null;
        }
    }

    private String stripToJson(String text) {
        if (text == null) return "";
        String trimmed = text.strip();

        // Strip ```json ... ``` or ``` ... ``` fences
        if (trimmed.startsWith("```")) {
            int firstNewline = trimmed.indexOf('\n');
            if (firstNewline != -1) {
                trimmed = trimmed.substring(firstNewline + 1);
            }
            if (trimmed.endsWith("```")) {
                trimmed = trimmed.substring(0, trimmed.length() - 3).strip();
            }
        }

        // Brace-extract fallback: substring from first { to last }
        int firstBrace = trimmed.indexOf('{');
        int lastBrace = trimmed.lastIndexOf('}');
        if (firstBrace != -1 && lastBrace != -1 && lastBrace > firstBrace) {
            trimmed = trimmed.substring(firstBrace, lastBrace + 1);
        }

        return trimmed;
    }

    private String buildSystemPrompt(RecommendationRequest request) {
        return """
                You are a gift recommendation assistant. Output VALID json ONLY — no prose, no markdown, no code fences.

                Output format (strict):
                {"recommendations":[{"name":string,"category":string,"estimatedPrice":integer,"reason":string}]}

                Rules:
                - Return at least 3 recommendation items.
                - estimatedPrice MUST be an integer (KRW) with NO currency symbol.
                - Every estimatedPrice MUST be within [%d, %d] inclusive.
                - Do not include any text outside the JSON object.
                """.formatted(request.budgetMin(), request.budgetMax());
    }

    private String buildUserPrompt(RecommendationRequest request) {
        StringBuilder sb = new StringBuilder();
        sb.append("Please recommend gifts for the following:\n");
        sb.append("Relationship: ").append(request.relationship()).append("\n");
        sb.append("Interests: ").append(String.join(", ", request.interests())).append("\n");
        sb.append("Budget: ").append(request.budgetMin()).append(" ~ ").append(request.budgetMax()).append(" KRW\n");

        if (request.occasion() != null && !request.occasion().isBlank()) {
            sb.append("Occasion: ").append(request.occasion()).append("\n");
        }
        if (request.gender() != null && !request.gender().isBlank()) {
            sb.append("Gender: ").append(request.gender()).append("\n");
        }
        if (request.ageRange() != null && !request.ageRange().isBlank()) {
            sb.append("Age range: ").append(request.ageRange()).append("\n");
        }

        return sb.toString();
    }
}
