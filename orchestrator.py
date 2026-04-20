"""
오케스트레이터 에이전트
4단계 파이프라인을 감독하고, 품질 검증 실패 시 재시도하며, 오류를 분류·보고합니다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from naver_seo_agent import (
    DELIVERY_TERMS, PROMO_WORDS, NAVER_CATEGORIES,
    generate_keyword_candidates, detect_category,
    query_search_trend, query_shopping_insight, combine_and_select,
    classify_keywords, build_guide_name,
    optimize_name, clean_by_rules, verify_name, enforce_min_length,
    fallback_by_shopping_search, strip_product_code,
    build_word_pool, filter_to_pool,
)

ATTRIBUTE_WORDS = [
    "긴팔", "반팔", "민소매", "긴바지", "반바지",
    "면", "레이스", "실크", "니트", "데님", "린넨", "폴리", "쉬폰", "벨벳", "가죽", "플리스", "울", "캐시미어",
    "프릴", "리본", "체크", "스트라이프", "도트",
]

import re as _re

def _final_cleanup(name: str, top_keywords: list[str]) -> str:
    """
    AI 결과와 무관하게 항상 적용되는 규칙 기반 최종 정리.
    - 특수문자 제거 (clean_by_rules보다 넓은 범위)
    - 중복 단어 제거
    - 50자 초과 시 단어 경계 자르기
    - 25자 미만 시 top_keywords 단어를 순서대로 추가
    """
    # 특수문자 제거 (한글, 영문, 숫자, 공백, +, - 만 허용)
    name = _re.sub(r'[^\w\s가-힣ㄱ-ㅎㅏ-ㅣa-zA-Z0-9\+\-]', '', name)
    name = _re.sub(r'\s+', ' ', name).strip()

    # 중복 단어 제거
    seen: set[str] = set()
    deduped: list[str] = []
    for w in name.split():
        if w not in seen:
            seen.add(w)
            deduped.append(w)
    name = ' '.join(deduped)

    # 50자 초과 시 단어 경계에서 자르기
    if len(name) > 50:
        name = name[:50].rsplit(' ', 1)[0].strip()

    # 25자 미만 시 top_keywords 단어 추가
    if len(name) < 25 and top_keywords:
        existing = set(name.split())
        for kw in top_keywords:
            for word in kw.split():
                if word not in existing:
                    candidate = (name + ' ' + word).strip()
                    if len(candidate) <= 50:
                        name = candidate
                        existing.add(word)
                if len(name) >= 25:
                    break
            if len(name) >= 25:
                break

    return name

# ── 데이터 클래스 ────────────────────────────────────────────────────

@dataclass
class ErrorReport:
    stage: str           # 오류 발생 단계
    error_type: str      # 분류: API 한도 초과 / 네트워크 오류 / JSON 파싱 오류 / API 인증 오류 / 기타
    message: str         # 원본 오류 메시지 (200자 이내)
    action_taken: str    # 취한 조치 설명
    resolved: bool       # 자동 해결 여부


@dataclass
class OrchestratorReport:
    original: str
    final_name: str
    attempts: int                           # 총 시도 횟수
    passed_validation: bool                 # 최종 품질 검증 통과 여부
    validation_failures: list[str] = field(default_factory=list)  # 각 시도별 실패 이유
    errors: list[ErrorReport] = field(default_factory=list)       # 발생한 오류 목록
    warning: str | None = None              # max_retries 초과 경고 등


# ── 오류 분류 ────────────────────────────────────────────────────────

def _classify_error(error: Exception) -> tuple[str, str, bool]:
    """
    오류를 분류하고 (error_type, action_description, auto_resolvable) 반환.
    auto_resolvable: True면 재시도로 해결 가능성 있음
    """
    err_str = str(error).lower()

    if isinstance(error, requests.exceptions.HTTPError):
        status = error.response.status_code if error.response is not None else 0
        if status == 429:
            return "API 한도 초과", "5초 대기 후 자동 재시도", True
        if status in (401, 403):
            return "API 인증 오류", "API 키를 확인하세요 (자동 해결 불가)", False
        if status >= 500:
            return "서버 오류", "서버 오류 — 자동 재시도 시도", True
        return "HTTP 오류", f"HTTP {status} 오류 — API 설정 확인 필요", False

    if isinstance(error, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return "네트워크 오류", "네트워크 불안정 — 1초 대기 후 자동 재시도", True

    if isinstance(error, json.JSONDecodeError):
        return "JSON 파싱 오류", "AI 응답 형식 오류 — 재시도 시 해결 가능", True

    if "quota" in err_str or "rate limit" in err_str or "resource_exhausted" in err_str:
        return "API 한도 초과", "API 사용량 한도 초과 — 5초 대기 후 재시도", True

    if "api_key" in err_str or "invalid" in err_str or "unauthorized" in err_str:
        return "API 인증 오류", "API 키 유효성 확인 필요 (자동 해결 불가)", False

    return "알 수 없는 오류", f"예상치 못한 오류: {str(error)[:80]}", True


def analyze_error(stage: str, error: Exception) -> ErrorReport:
    """오류를 분석하고 ErrorReport를 반환합니다. resolved는 False로 초기화됩니다."""
    error_type, action, _ = _classify_error(error)
    return ErrorReport(
        stage=stage,
        error_type=error_type,
        message=str(error)[:200],
        action_taken=action,
        resolved=False,
    )


# ── 품질 검증 ────────────────────────────────────────────────────────

def validate_result(
    original: str,
    final_name: str,
    issues: str | None,
    model,
    word_pool: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    검수 완료된 결과의 품질을 재검증합니다.
    반환: (통과 여부, 실패 이유 목록)
    """
    failures: list[str] = []

    # 0. 원본과 동일 여부
    import re as _re
    if _re.sub(r'\s+', '', final_name).lower() == _re.sub(r'\s+', '', original).lower():
        # 원본이 이미 품질 기준을 통과하면 그대로 수용 (불필요한 재시도 방지)
        length = len(final_name)
        has_promo    = any(w in final_name for w in PROMO_WORDS)
        has_delivery = any(t in final_name for t in DELIVERY_TERMS)
        if 25 <= length <= 50 and not has_promo and not has_delivery:
            return True, []
        failures.append("원본 상품명과 동일: 인기 키워드를 활용해 새롭게 최적화하세요")
        return False, failures

    # 1. 글자 수 검사
    length = len(final_name)
    if length < 25:
        failures.append(f"글자 수 부족: {length}자 (최소 25자 필요)")
    elif length > 50:
        failures.append(f"글자 수 초과: {length}자 (최대 50자)")

    # 2. 홍보성 단어 잔존 여부
    for word in PROMO_WORDS:
        if word in final_name:
            failures.append(f"홍보성 단어 잔존: '{word}'")
            break

    # 3. 배송 관련 문구 잔존 여부
    for term in DELIVERY_TERMS:
        if term in final_name:
            failures.append(f"배송 관련 문구 잔존: '{term}'")
            break

    # 4. 원본에 없는 속성 단어 추가 여부 (word_pool에 있으면 DataLab 검증된 키워드이므로 허용)
    for word in ATTRIBUTE_WORDS:
        if word in final_name and word not in original:
            if word_pool is None or word not in word_pool:
                failures.append(f"원본에 없는 속성 추가: '{word}' — 원본에 있는 속성만 사용하세요")
                break

    # 5. 검수 단계 자체 지적 사항 (규칙 위반 언급이 있을 때만 실패 처리)
    if issues:
        violation_keywords = ("금지", "위반", "제거", "포함", "초과", "미만", "브랜드", "배송", "홍보")
        if any(kw in issues for kw in violation_keywords):
            failures.append(f"검수 지적 사항 미해결: {issues[:80]}")

    # 6. AI 기반 상품 유형 일치 여부 (기본 규칙 통과 시에만 실행하여 비용 절감)
    if not failures:
        try:
            prompt = (
                "다음 두 상품명이 같은 종류의 상품을 설명하는지 판단하세요.\n"
                "원본과 최적화 상품명이 완전히 다른 상품 유형으로 바뀌었다면 'NO', "
                "같은 종류라면 'YES'로만 답하세요.\n\n"
                f"원본: {original}\n"
                f"최적화: {final_name}"
            )
            answer = model.generate_content(prompt).text.strip().upper()
            if answer.startswith("NO"):
                failures.append("상품 유형 변경됨: 원본과 다른 종류의 상품으로 최적화됨")
        except Exception:
            pass  # AI 검사 실패 시 무시 (네트워크 불안정 등)

    return len(failures) == 0, failures


# ── 오케스트레이션 메인 ──────────────────────────────────────────────

def run_with_orchestration(
    original: str,
    models: dict,
    api_keys: dict,
    max_retries: int = 3,
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> tuple[str, OrchestratorReport]:
    """
    오케스트레이터가 4단계 파이프라인을 실행하고 품질을 검증합니다.
    검증 실패 시 피드백을 주입해 최대 max_retries회 재시도합니다.

    Args:
        original: 원본 상품명
        models: {'keyword': ..., 'optimize': ..., 'verify': ...}
        api_keys: {'naver_id': ..., 'naver_secret': ...}
        max_retries: 최대 시도 횟수 (기본 3)
        progress_callback: progress_callback(attempt, stage, detail) 형태의 콜백

    Returns:
        (final_name, OrchestratorReport)
    """
    keyword_model  = models['keyword']
    classify_model = models['classify']
    optimize_model = models['optimize']
    verify_model   = models['verify']
    naver_id       = api_keys['naver_id']
    naver_secret   = api_keys['naver_secret']

    def _progress(attempt: int, stage: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(attempt, stage, detail)

    all_validation_failures: list[str] = []
    all_errors: list[ErrorReport] = []
    original_clean = strip_product_code(original)
    last_final_name = original_clean
    word_pool: set[str] = set(original_clean.split())
    feedback = ""
    prev_same_as_original = False  # 이전 시도가 "원본과 동일" 실패였는지 추적

    for attempt in range(1, max_retries + 1):
        stage = ""
        try:
            # Stage 1: 키워드 생성
            stage = "키워드 후보 생성"
            _progress(attempt, "1/4 키워드 후보 생성 및 카테고리 감지 중...", feedback and f"피드백 반영: {feedback[:40]}")
            candidates = generate_keyword_candidates(original_clean, keyword_model, feedback=feedback)
            word_pool  = build_word_pool(original_clean, candidates)  # DataLab 실패해도 풀 확보

            stage = "카테고리 감지"
            category_id = detect_category(original_clean, keyword_model)
            cat_name = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")

            # Stage 2: 트렌드 분석
            stage = "검색량 조회"
            _progress(attempt, f"2/4 검색량 조회 중...", f"카테고리: {cat_name}")
            search_scores   = query_search_trend(candidates, naver_id, naver_secret)
            shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
            top_keywords    = combine_and_select(search_scores, shopping_scores, candidates)
            word_pool       = build_word_pool(original_clean, top_keywords)  # DataLab 결과로 풀 갱신

            # Stage 2.5: 키워드 분류
            stage = "키워드 분류"
            core_keywords, aux_words = classify_keywords(top_keywords, original_clean, classify_model)
            _progress(attempt, "2.5/4 키워드 분류 완료", f"핵심: {core_keywords} / 보조: {aux_words}")

            # Stage 3: 최적화
            # 이전 시도가 "원본과 동일" 실패였으면 pool 필터를 건너뜀
            # (filter_to_pool이 새 키워드를 제거해 원본으로 돌아가는 악순환 방지)
            stage = "상품명 최적화"
            _progress(attempt, "3/4 상품명 최적화 중...", f"핵심키워드: {', '.join(core_keywords)}")
            optimized = optimize_name(original_clean, core_keywords, aux_words, optimize_model)
            rule_cleaned = clean_by_rules(optimized, original_clean)
            cleaned = rule_cleaned if prev_same_as_original else filter_to_pool(rule_cleaned, word_pool)

            # Stage 4: 검수
            stage = "검수"
            _progress(attempt, "4/4 검수 중...")
            final_name, issues = verify_name(original_clean, cleaned, verify_model, allowed_keywords=top_keywords)
            if not prev_same_as_original:
                final_name = filter_to_pool(final_name, word_pool)
            if len(final_name) < 25:
                # enforce_min_length는 단어를 추가하는 목적이므로 filter_to_pool 미적용
                final_name = enforce_min_length(final_name, original_clean, top_keywords, optimize_model)

            last_final_name = final_name

            # 오케스트레이터 품질 검증
            passed, failures = validate_result(original_clean, final_name, issues, verify_model, word_pool)

            if passed:
                final_name = _final_cleanup(final_name, top_keywords)
                return final_name, OrchestratorReport(
                    original=original,
                    final_name=final_name,
                    attempts=attempt,
                    passed_validation=True,
                    validation_failures=all_validation_failures,
                    errors=all_errors,
                )

            # 실패: 다음 재시도를 위해 상태 업데이트
            failure_summary = "; ".join(failures)
            all_validation_failures.extend([f"[시도{attempt}] {f}" for f in failures])
            feedback = failure_summary
            prev_same_as_original = any("원본 상품명과 동일" in f for f in failures)

        except Exception as e:
            err_report = analyze_error(stage, e)
            _, _, auto_resolvable = _classify_error(e)

            if auto_resolvable:
                wait = 5 if err_report.error_type == "API 한도 초과" else 1
                time.sleep(wait)
                err_report.resolved = True  # 재시도로 해결 시도함

            all_errors.append(err_report)

            if not auto_resolvable:
                # 자동 해결 불가능한 오류 (인증 오류 등) — 즉시 종료
                break

    # 모든 시도 소진 — 네이버 쇼핑 검색 폴백
    import re as _re
    last_normalized = _re.sub(r'\s+', '', last_final_name).lower()
    clean_normalized = _re.sub(r'\s+', '', original_clean).lower()
    if last_normalized == clean_normalized or len(last_final_name) < 25:
        try:
            fallback = fallback_by_shopping_search(
                original_clean, naver_id, naver_secret, optimize_model, classify_model
            )
            if fallback and _re.sub(r'\s+', '', fallback).lower() != clean_normalized:
                if len(fallback) < 25:
                    fallback = enforce_min_length(fallback, original_clean, [], optimize_model)
                if len(fallback) >= 25:
                    last_final_name = fallback
        except Exception:
            pass

    last_final_name = _final_cleanup(last_final_name, top_keywords)

    failure_desc = (
        f" 미해결 품질 이슈: {'; '.join(all_validation_failures[-3:])}"
        if all_validation_failures else ""
    )
    warning = f"최대 재시도 횟수({max_retries})를 초과했습니다. 마지막 결과를 사용합니다.{failure_desc}"

    return last_final_name, OrchestratorReport(
        original=original,
        final_name=last_final_name,
        attempts=max_retries,
        passed_validation=False,
        validation_failures=all_validation_failures,
        errors=all_errors,
        warning=warning,
    )
