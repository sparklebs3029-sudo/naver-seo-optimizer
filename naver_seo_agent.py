#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 + 상품 소싱 에이전트
"""

import os
import re
import json
import time
import argparse
from datetime import datetime, timedelta
import requests
import google.generativeai as genai
import openpyxl


# ── 네이버 쇼핑 카테고리 ────────────────────────────────────────────
NAVER_CATEGORIES = {
    "패션의류":     "50000000",
    "패션잡화":     "50000001",
    "화장품/미용":  "50000002",
    "디지털/가전":  "50000003",
    "가구/인테리어":"50000004",
    "출산/육아":    "50000005",
    "식품":         "50000006",
    "스포츠/레저":  "50000007",
    "생활/건강":    "50000008",
    "자동차용품":   "50000011",
    "완구/취미":    "50000013",
    "문구/오피스":  "50000014",
    "반려동물용품": "50000015",
    "농수축산물":   "50000016",
}

# ── 배송 관련 금지 문구 ─────────────────────────────────────────────
DELIVERY_TERMS = [
    "오늘출발", "당일배송", "빠른배송", "무료배송", "익일배송",
    "새벽배송", "로켓배송", "총알배송", "당일출발", "빠른출발",
]

# ── 홍보성/주관적 금지 수식어 ───────────────────────────────────────
PROMO_WORDS = [
    "최고", "최저가", "전국최저가", "특가", "최저", "강추", "완전", "대박",
    "개꿀", "인기폭발", "베스트", "핫딜", "할인", "세일", "역대급", "초특가",
    "가성비최고", "품질보장", "정품보장", "100%정품",
]

# ── 소싱 금지 품목 그룹 ─────────────────────────────────────────────
PROHIBITED_GROUPS = {
    "총기/무기류":   ["총기", "권총", "소총", "공기총", "BB탄총", "도검", "폭발물", "폭탄", "화약", "탄약", "수류탄"],
    "마약/향정신성": ["마약", "대마", "필로폰", "코카인", "헤로인", "LSD", "히로뽕", "향정신성"],
    "유해화학물질":  ["독극물", "청산가리", "시안화", "화학무기"],
    "성인용품":      ["성인용품", "음란", "성인토이"],
    "전문의약품":    ["처방전", "전문의약품", "향정신성의약품"],
}

# ── 카테고리별 시드 키워드 ───────────────────────────────────────────
CATEGORY_SEED_KEYWORDS: dict[str, list[str]] = {
    "패션의류":     ["티셔츠", "청바지", "원피스", "코트", "패딩", "니트", "블라우스", "바지", "자켓", "후드티"],
    "패션잡화":     ["가방", "지갑", "벨트", "모자", "선글라스", "시계", "스카프", "신발", "슬리퍼", "백팩"],
    "화장품/미용":  ["마스크팩", "에센스", "크림", "선크림", "립스틱", "파운데이션", "샴푸", "바디로션", "향수", "세럼"],
    "디지털/가전":  ["이어폰", "충전기", "보조배터리", "블루투스스피커", "스마트워치", "마우스", "키보드", "웹캠", "USB허브", "케이블"],
    "가구/인테리어": ["책상", "의자", "침대", "소파", "수납장", "조명", "커튼", "카펫", "선반", "행거"],
    "출산/육아":    ["기저귀", "분유", "유모차", "아기띠", "장난감", "이유식", "아기옷", "젖병", "카시트", "아기침대"],
    "식품":         ["과자", "음료", "라면", "커피", "건강식품", "견과류", "초콜릿", "홍삼", "프로틴", "비타민"],
    "스포츠/레저":  ["운동화", "레깅스", "요가매트", "덤벨", "등산화", "텐트", "낚시", "골프장갑", "수영복", "자전거"],
    "생활/건강":    ["칫솔", "세제", "주방용품", "멀티탭", "마스크", "비타민", "체중계", "공기청정기", "청소기", "욕실용품"],
    "자동차용품":   ["방향제", "블랙박스", "차량충전기", "세차용품", "차량매트", "햇빛가리개", "네비게이션", "타이어", "차량배터리", "열선"],
    "완구/취미":    ["레고", "보드게임", "피규어", "퍼즐", "드론", "RC카", "프라모델", "색연필", "그림도구", "미니어처"],
    "문구/오피스":  ["볼펜", "노트", "스케줄러", "테이프", "가위", "포스트잇", "계산기", "파일", "스탬프", "화이트보드"],
    "반려동물용품": ["사료", "간식", "패드", "장난감", "리드줄", "하네스", "하우스", "캐리어", "영양제", "샴푸"],
    "농수축산물":   ["쌀", "김치", "고구마", "감자", "양파", "마늘", "사과", "배", "한우", "굴비"],
}

# ── 시스템 프롬프트 ─────────────────────────────────────────────────
KEYWORD_SYSTEM = """당신은 네이버 쇼핑 검색 전문가입니다.
상품명을 보고 구매자들이 실제로 네이버에서 검색할 키워드 후보를 생성합니다."""

OPTIMIZE_SYSTEM = """당신은 네이버 스마트스토어 SEO 전문가입니다.
네이버 검색 알고리즘과 쇼핑 검색 최적화에 깊은 이해를 가지고 있습니다.

네이버 SEO 상품명 최적화 원칙:
1. 핵심 키워드를 앞쪽에 배치 (검색 노출 우선순위)
2. 상품의 주 구매 타겟(예: 자취생, 부모님선물, 캠핑족, 신혼부부 등)이나 핵심 사용 상황을 파악하여 상품명 가장 앞에 1~2단어로 자연스럽게 배치
3. 브랜드명 + 카테고리 + 세부 특성 + 타겟/용도 조합
4. 제공된 고검색량 키워드를 자연스럽게 포함
5. 반드시 공백 포함 25자 이상 50자 이하 (네이버 SEO 최적 범위)
6. 특수문자 일절 사용 금지 (**, [], ##, ~~, /, | 등 모든 기호 제외)
7. 배송 관련 문구 절대 포함 금지 (오늘출발, 당일배송, 빠른배송, 무료배송 등)
8. 홍보성·주관적 수식어 사용 금지 (최고, 특가, 강추, 대박, 베스트 등)
9. 답변은 반드시 순수 텍스트 상품명만 출력, 마크다운 서식 사용 금지"""

VERIFY_SYSTEM = """당신은 네이버 스마트스토어 상품명 검수 전문가입니다.
최적화된 상품명이 품질 기준을 충족하는지 검토하고 필요시 수정합니다."""


# ── 설정 파일 로드 ──────────────────────────────────────────────────
def load_config() -> dict[str, str]:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
    config = {}
    if not os.path.exists(config_path):
        return config
    with open(config_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if value and not value.startswith("여기에"):
                config[key.strip()] = value
    return config


# ── Naver API 재시도 헬퍼 ────────────────────────────────────────────
def _post_with_retry(url: str, headers: dict, body: dict, max_retries: int = 3) -> dict:
    """POST 요청을 최대 3회 재시도합니다. 호출 사이 0.2초 대기."""
    last_err = None
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(0.2 * attempt)
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            last_err = e
            if e.response is not None and e.response.status_code < 500:
                raise
        except requests.exceptions.RequestException as e:
            last_err = e
    raise last_err


# ── 1단계: 키워드 후보 에이전트 ────────────────────────────────────
def generate_keyword_candidates(original: str, model: genai.GenerativeModel) -> list[str]:
    prompt = (
        "다음 상품명과 관련된 네이버 검색 키워드 후보를 10개 생성해주세요.\n"
        "구매자들이 실제로 네이버에서 검색할 2~5단어 조합의 키워드를 생성하세요.\n"
        "브랜드명, 배송 관련 단어는 제외하세요.\n"
        "JSON 배열 형식으로만 답변하세요: [\"키워드1\", \"키워드2\", ...]\n\n"
        f"상품명: {original}"
    )
    response = model.generate_content(prompt)
    raw = response.text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(raw)


def detect_category(original: str, model: genai.GenerativeModel) -> str:
    categories_list = "\n".join(f"- {name}" for name in NAVER_CATEGORIES)
    prompt = (
        "다음 상품명이 네이버 쇼핑의 어떤 카테고리에 속하는지 판단하세요.\n"
        "아래 목록 중 가장 적합한 카테고리명 하나만 정확히 답변하세요:\n\n"
        f"{categories_list}\n\n"
        f"상품명: {original}"
    )
    response = model.generate_content(prompt)
    category_name = response.text.strip()
    return NAVER_CATEGORIES.get(category_name, "50000008")


# ── 2단계: 데이터랩 조회 ────────────────────────────────────────────
def query_search_trend(keywords: list[str], client_id: str, client_secret: str) -> dict[str, float]:
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=90)
    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type":          "application/json",
    }
    results = {}
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        body  = {
            "startDate":     start_date.strftime("%Y-%m-%d"),
            "endDate":       end_date.strftime("%Y-%m-%d"),
            "timeUnit":      "month",
            "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in batch],
        }
        data = _post_with_retry("https://openapi.naver.com/v1/datalab/search", headers, body)
        for result in data.get("results", []):
            ratios = [p["ratio"] for p in result.get("data", [])]
            results[result["title"]] = sum(ratios) / len(ratios) if ratios else 0.0
        time.sleep(0.2)
    return results


def query_shopping_insight(keywords: list[str], category_id: str, client_id: str, client_secret: str) -> dict[str, float]:
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=90)
    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type":          "application/json",
    }
    results = {}
    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        body  = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate":   end_date.strftime("%Y-%m-%d"),
            "timeUnit":  "month",
            "category":  category_id,
            "keyword":   [{"name": kw, "param": [kw]} for kw in batch],
        }
        data = _post_with_retry(
            "https://openapi.naver.com/v1/datalab/shopping/category/keywords", headers, body
        )
        for result in data.get("results", []):
            ratios = [p["ratio"] for p in result.get("data", [])]
            results[result["title"]] = sum(ratios) / len(ratios) if ratios else 0.0
        time.sleep(0.2)
    return results


def combine_and_select(
    search_scores:   dict[str, float],
    shopping_scores: dict[str, float],
    keywords:        list[str],
    n:               int = 5,
) -> list[str]:
    combined = {
        kw: search_scores.get(kw, 0.0) * 0.4 + shopping_scores.get(kw, 0.0) * 0.6
        for kw in keywords
    }
    return [kw for kw, _ in sorted(combined.items(), key=lambda x: x[1], reverse=True)[:n]]


# ── 3단계: 최적화 에이전트 ─────────────────────────────────────────
def optimize_name(original: str, top_keywords: list[str], model: genai.GenerativeModel) -> str:
    keywords_str = ", ".join(top_keywords) if top_keywords else "없음"

    # 원본에 사이즈/수량 정보 포함 여부 감지
    has_size  = bool(re.search(r'\d+\s*(cm|mm|m|L|ml|g|kg|인치|평|구|포|매|개|장|켤레|족)', original, re.IGNORECASE))
    has_bonus = bool(re.search(r'1\+1|2\+1|증정|사은품', original))

    size_note  = "원본에 사이즈/규격 정보가 있으므로 반드시 유지하고 상품명 뒤쪽에 배치하세요." if has_size  else "원본에 사이즈 정보가 없으므로 임의로 추가하지 마세요."
    bonus_note = "원본에 1+1/증정 정보가 있으므로 반드시 유지하고 핵심키워드 뒤에 배치하세요." if has_bonus else "원본에 1+1/증정 정보가 없으므로 임의로 추가하지 마세요."

    prompt = (
        "다음 상품명을 네이버 SEO에 맞게 롱테일 키워드로 최적화해주세요.\n\n"
        "▶ 상품명 구조 (순서 준수):\n"
        "  [타겟/사용상황] [핵심키워드] [브랜드] [세부특성] [사이즈·수량·1+1]\n\n"
        f"▶ 고검색량 키워드 (최대한 자연스럽게 포함): {keywords_str}\n"
        "▶ 상품의 주 구매 타겟(자취생, 부모님선물, 캠핑족 등)을 파악해 상품명 맨 앞에 1~2단어로 배치하세요.\n"
        f"▶ 사이즈/규격: {size_note}\n"
        f"▶ 1+1/증정: {bonus_note}\n"
        "▶ 반드시 공백 포함 25자 이상 50자 이하로 작성하세요.\n"
        "▶ 최적화된 상품명 1개만 순수 텍스트로 답변하세요. 설명이나 부연은 불필요합니다.\n\n"
        f"원본 상품명: {original}"
    )
    response = model.generate_content(prompt)
    return response.text.strip()


# ── 4단계: 검수 (코드 규칙 + AI) ───────────────────────────────────
def _remove_duplicate_words(text: str) -> str:
    seen:   set[str]  = set()
    result: list[str] = []
    for word in text.split():
        if word not in seen:
            seen.add(word)
            result.append(word)
    return " ".join(result)


def clean_by_rules(name: str) -> str:
    name = re.sub(r'[*#~`\[\](){}|/\\]', '', name)
    for term in DELIVERY_TERMS:
        name = name.replace(term, '')
    for word in PROMO_WORDS:
        name = re.sub(rf'\b{re.escape(word)}\b', '', name)
    name = _remove_duplicate_words(name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > 50:
        name = name[:50].rsplit(' ', 1)[0].strip()
    return name


def enforce_min_length(name: str, original: str, top_keywords: list[str], model: genai.GenerativeModel) -> str:
    """25자 미만인 경우 키워드를 추가해 재확장합니다."""
    if len(name) >= 25:
        return name
    keywords_str = ", ".join(top_keywords)
    prompt = (
        f"다음 상품명이 {len(name)}자로 너무 짧습니다. 반드시 25자 이상 50자 이하로 늘려주세요.\n"
        f"원본 상품명: {original}\n"
        f"현재 상품명: {name}\n"
        f"참고 키워드: {keywords_str}\n"
        "현재 상품명을 기반으로 관련 키워드를 자연스럽게 추가해 25자 이상으로 확장하세요.\n"
        "특수문자, 배송 문구, 홍보 수식어는 사용하지 마세요.\n"
        "순수 텍스트 상품명만 답변하세요."
    )
    result = clean_by_rules(model.generate_content(prompt).text.strip())
    return result if len(result) >= 25 else name


def verify_name(original: str, optimized: str, model: genai.GenerativeModel) -> tuple[str, str | None]:
    length = len(optimized)
    length_note = ""
    if length < 25:
        length_note = f"현재 {length}자로 너무 짧습니다. 관련 키워드를 추가해 25자 이상으로 늘려주세요."
    elif length > 50:
        length_note = f"현재 {length}자로 너무 깁니다. 50자 이하로 줄여주세요."

    prompt = (
        "아래 최적화된 상품명을 검수하고 필요시 수정해주세요.\n\n"
        f"원본 상품명: {original}\n"
        f"최적화된 상품명: {optimized}\n"
        f"{('⚠️ 글자수 조정 필요: ' + length_note) if length_note else ''}\n\n"
        "검수 기준:\n"
        "1. 의미 없는 수식어 제거 (최고, 대박, 완전, 특가, 강추 등)\n"
        "2. 중복 단어 제거\n"
        "3. 원본 상품명에 없는 무관한 브랜드명 제거 (원본에 있는 브랜드는 유지)\n"
        "4. 원본 상품과 관련 없는 키워드 제거\n"
        "5. 네이버 금지 표현 제거\n"
        "6. 공백 포함 25자 이상 50자 이하 유지 — 절대 25자 미만으로 줄이지 말 것\n\n"
        "다음 JSON 형식으로만 답변하세요:\n"
        '{"final_name": "최종 상품명", "issues": "수정 사항 설명 (없으면 null)"}'
    )
    response = model.generate_content(prompt)
    raw = response.text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(raw)
    return data["final_name"], data.get("issues") or None


# ── 소싱 에이전트 ───────────────────────────────────────────────────
def search_naver_shopping(keyword: str, client_id: str, client_secret: str, display: int = 5) -> list[dict]:
    """네이버 쇼핑 검색 API로 인기 상품을 검색합니다."""
    resp = requests.get(
        "https://openapi.naver.com/v1/search/shop.json",
        headers={
            "X-Naver-Client-Id":     client_id,
            "X-Naver-Client-Secret": client_secret,
        },
        params={"query": keyword, "display": display, "sort": "sim"},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    for item in items:
        item["title"] = re.sub(r"<[^>]+>", "", item.get("title", ""))
    return items


def is_prohibited(text: str, active_groups: list[str]) -> bool:
    """텍스트에 금지 키워드가 포함되어 있는지 확인합니다."""
    text_lower = text.lower()
    for group in active_groups:
        for kw in PROHIBITED_GROUPS.get(group, []):
            if kw in text_lower:
                return True
    return False


def get_trending_products(
    category_name:      str,
    category_id:        str,
    period_days:        int,
    client_id:          str,
    client_secret:      str,
    active_prohibited:  list[str],
    extra_prohibited:   list[str],
    top_n:              int = 5,
) -> list[dict]:
    """트렌딩 키워드 조회 후 상품 소싱 결과를 반환합니다."""
    seed_keywords = CATEGORY_SEED_KEYWORDS.get(category_name, [])
    if not seed_keywords:
        return []

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=period_days)
    headers = {
        "X-Naver-Client-Id":     client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type":          "application/json",
    }

    # DataLab 쇼핑인사이트로 시드 키워드 트렌드 점수 조회
    trend_scores: dict[str, float] = {}
    for i in range(0, len(seed_keywords), 5):
        batch = seed_keywords[i:i + 5]
        body  = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate":   end_date.strftime("%Y-%m-%d"),
            "timeUnit":  "date",
            "category":  category_id,
            "keyword":   [{"name": kw, "param": [kw]} for kw in batch],
        }
        try:
            data = _post_with_retry(
                "https://openapi.naver.com/v1/datalab/shopping/category/keywords", headers, body
            )
            for result in data.get("results", []):
                ratios = [p["ratio"] for p in result.get("data", [])]
                trend_scores[result["title"]] = sum(ratios) / len(ratios) if ratios else 0.0
        except Exception:
            pass
        time.sleep(0.2)

    # 상위 N개 트렌딩 키워드
    top_keywords = sorted(trend_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # 각 키워드별 쇼핑 검색 + 금지 필터 적용
    results: list[dict] = []
    for keyword, score in top_keywords:
        try:
            products = search_naver_shopping(keyword, client_id, client_secret, display=5)
            for product in products:
                full_text = " ".join([
                    product.get("title", ""),
                    product.get("category1", ""),
                    product.get("category2", ""),
                    product.get("category3", ""),
                ])
                # 금지 그룹 필터
                if is_prohibited(full_text, active_prohibited):
                    continue
                # 추가 금지 키워드 필터
                if any(kw in full_text for kw in extra_prohibited if kw):
                    continue

                price = product.get("lprice", "")
                results.append({
                    "키워드":     keyword,
                    "트렌드점수": round(score, 1),
                    "상품명":     product.get("title", ""),
                    "최저가":     f"{int(price):,}원" if price else "-",
                    "쇼핑몰":     product.get("mallName", ""),
                    "카테고리":   product.get("category1", ""),
                    "링크":       product.get("link", ""),
                })
            time.sleep(0.2)
        except Exception:
            pass

    return results


# ── 출력 파일 경로 생성 ─────────────────────────────────────────────
def get_output_path(input_path: str) -> str:
    import glob
    dir_name      = os.path.dirname(os.path.abspath(input_path))
    base_name     = os.path.splitext(os.path.basename(input_path))[0]
    now           = datetime.now()
    datetime_str  = now.strftime("%Y%m%d%H%M")   # 연월일시분
    today_str     = now.strftime("%Y%m%d")        # 날짜 (번호 기준)

    # 오늘 생성된 파일 수로 번호 결정 (00시 기준 초기화)
    existing = glob.glob(os.path.join(dir_name, f"*_최적화_{today_str}*.xlsx"))
    n = len(existing) + 1

    candidate = os.path.join(dir_name, f"{base_name}_최적화_{datetime_str}_{n}.xlsx")
    while os.path.exists(candidate):
        n += 1
        candidate = os.path.join(dir_name, f"{base_name}_최적화_{datetime_str}_{n}.xlsx")
    return candidate


# ── 메인 (BAT 파일용) ───────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 SEO 롱테일 상품명 최적화 에이전트")
    parser.add_argument("input", help="처리할 엑셀 파일 경로 (.xlsx)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[오류] 파일을 찾을 수 없습니다: {args.input}")
        return 1

    config       = load_config()
    gemini_key   = os.environ.get("GEMINI_API_KEY")      or config.get("GEMINI_API_KEY")
    naver_id     = os.environ.get("NAVER_CLIENT_ID")     or config.get("NAVER_CLIENT_ID")
    naver_secret = os.environ.get("NAVER_CLIENT_SECRET") or config.get("NAVER_CLIENT_SECRET")

    if not gemini_key:
        print("[오류] GEMINI_API_KEY가 config.txt에 없습니다.")
        return 1
    if not naver_id or not naver_secret:
        print("[오류] NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 config.txt에 없습니다.")
        return 1

    genai.configure(api_key=gemini_key)
    keyword_model  = genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM)
    optimize_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM)
    verify_model   = genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM)

    wb = openpyxl.load_workbook(args.input)
    ws = wb.active
    H_COL = 8

    data_rows = [
        (r, str(ws.cell(row=r, column=H_COL).value).strip())
        for r in range(2, ws.max_row + 1)
        if ws.cell(row=r, column=H_COL).value
        and str(ws.cell(row=r, column=H_COL).value).strip()
    ]
    print(f"처리 대상: {len(data_rows)}개\n")

    errors:      list[dict] = []
    issues_log:  list[dict] = []

    for i, (row_idx, original) in enumerate(data_rows, 1):
        print(f"[{i}/{len(data_rows)}] {original[:40]}")
        stage = ""
        try:
            stage = "키워드 후보 생성"
            candidates  = generate_keyword_candidates(original, keyword_model)
            stage = "카테고리 감지"
            category_id = detect_category(original, keyword_model)
            cat_name    = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")
            print(f"  [1/4] 카테고리: {cat_name}")

            stage = "검색량 조회"
            search_scores   = query_search_trend(candidates, naver_id, naver_secret)
            shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
            top_keywords    = combine_and_select(search_scores, shopping_scores, candidates)
            print(f"  [2/4] 키워드: {', '.join(top_keywords)}")

            stage = "상품명 최적화"
            optimized = optimize_name(original, top_keywords, optimize_model)
            cleaned   = clean_by_rules(optimized)
            print(f"  [3/4] 최적화: {cleaned} ({len(cleaned)}자)")

            stage = "검수"
            final_name, issues = verify_name(original, cleaned, verify_model)
            ws.cell(row=row_idx, column=H_COL).value = final_name
            print(f"  [4/4] 최종: {final_name} ({len(final_name)}자)")

            if issues:
                issues_log.append({"행": row_idx, "원본": original, "최종": final_name, "수정사항": issues})

        except Exception as e:
            print(f"  ※ [{stage}] 오류 ({e})")
            errors.append({"행": row_idx, "원본": original, "단계": stage, "오류": str(e)})

    output_path = get_output_path(args.input)
    wb.save(output_path)
    print(f"\n저장 완료: {output_path}")
    print(f"완료: {len(data_rows) - len(errors)}건 / 오류: {len(errors)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
