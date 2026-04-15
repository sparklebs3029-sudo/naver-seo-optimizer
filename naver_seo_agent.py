#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 에이전트
엑셀 파일의 H열 상품명을 네이버 SEO 롱테일 키워드로 최적화합니다.

사용법:
    python naver_seo_agent.py 상품목록.xlsx
"""

import os
import re
import json
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

# ── 시스템 프롬프트 ─────────────────────────────────────────────────
KEYWORD_SYSTEM = """당신은 네이버 쇼핑 검색 전문가입니다.
상품명을 보고 구매자들이 실제로 네이버에서 검색할 키워드 후보를 생성합니다."""

OPTIMIZE_SYSTEM = """당신은 네이버 스마트스토어 SEO 전문가입니다.
네이버 검색 알고리즘과 쇼핑 검색 최적화에 깊은 이해를 가지고 있습니다.

네이버 SEO 상품명 최적화 원칙:
1. 핵심 키워드를 앞쪽에 배치 (검색 노출 우선순위)
2. 브랜드명 + 카테고리 + 세부 특성 + 타겟/용도 조합
3. 제공된 고검색량 키워드를 자연스럽게 포함
4. 100자 이내 (네이버 스마트스토어 제한)
5. 특수문자 일절 사용 금지 (**, [], ##, ~~, /, | 등 모든 기호 제외)
6. 배송 관련 문구 절대 포함 금지 (오늘출발, 당일배송, 빠른배송, 무료배송 등)
7. 답변은 반드시 순수 텍스트 상품명만 출력, 마크다운 서식 사용 금지"""

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


# ── 1단계: 키워드 후보 에이전트 ────────────────────────────────────
def generate_keyword_candidates(original: str, model: genai.GenerativeModel) -> list[str]:
    """원본 상품명에서 검색량 조회할 키워드 후보 10개를 생성합니다."""
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
    """원본 상품명으로 네이버 쇼핑 카테고리 ID를 감지합니다."""
    categories_list = "\n".join(f"- {name}" for name in NAVER_CATEGORIES)
    prompt = (
        "다음 상품명이 네이버 쇼핑의 어떤 카테고리에 속하는지 판단하세요.\n"
        "아래 목록 중 가장 적합한 카테고리명 하나만 정확히 답변하세요:\n\n"
        f"{categories_list}\n\n"
        f"상품명: {original}"
    )
    response = model.generate_content(prompt)
    category_name = response.text.strip()
    return NAVER_CATEGORIES.get(category_name, "50000008")  # 기본값: 생활/건강


# ── 2단계: 데이터랩 조회 ────────────────────────────────────────────
def query_search_trend(keywords: list[str], client_id: str, client_secret: str) -> dict[str, float]:
    """검색어트렌드 API로 키워드별 평균 검색량 지수를 반환합니다."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    results = {}

    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        body = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "timeUnit": "month",
            "keywordGroups": [
                {"groupName": kw, "keywords": [kw]} for kw in batch
            ],
        }
        resp = requests.post(
            "https://openapi.naver.com/v1/datalab/search",
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            ratios = [p["ratio"] for p in result.get("data", [])]
            results[result["title"]] = sum(ratios) / len(ratios) if ratios else 0.0

    return results


def query_shopping_insight(keywords: list[str], category_id: str, client_id: str, client_secret: str) -> dict[str, float]:
    """쇼핑인사이트 API로 키워드별 평균 클릭량 지수를 반환합니다."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    results = {}

    for i in range(0, len(keywords), 5):
        batch = keywords[i:i + 5]
        body = {
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "timeUnit": "month",
            "category": category_id,
            "keyword": [
                {"name": kw, "param": [kw]} for kw in batch
            ],
        }
        resp = requests.post(
            "https://openapi.naver.com/v1/datalab/shopping/category/keywords",
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            ratios = [p["ratio"] for p in result.get("data", [])]
            results[result["title"]] = sum(ratios) / len(ratios) if ratios else 0.0

    return results


def combine_and_select(
    search_scores: dict[str, float],
    shopping_scores: dict[str, float],
    keywords: list[str],
    n: int = 5,
) -> list[str]:
    """검색어트렌드(40%) + 쇼핑인사이트(60%) 합산 후 상위 n개 반환합니다."""
    combined = {}
    for kw in keywords:
        s = search_scores.get(kw, 0.0)
        h = shopping_scores.get(kw, 0.0)
        combined[kw] = s * 0.4 + h * 0.6
    sorted_kws = sorted(combined.items(), key=lambda x: x[1], reverse=True)
    return [kw for kw, _ in sorted_kws[:n]]


# ── 3단계: 최적화 에이전트 ─────────────────────────────────────────
def optimize_name(original: str, top_keywords: list[str], model: genai.GenerativeModel) -> str:
    """고검색량 키워드를 반영해 SEO 최적화 상품명을 생성합니다."""
    keywords_str = ", ".join(top_keywords) if top_keywords else "없음"
    prompt = (
        "다음 상품명을 네이버 SEO에 맞게 롱테일 키워드로 최적화해주세요.\n"
        f"아래 고검색량 키워드를 최대한 자연스럽게 포함하세요: {keywords_str}\n"
        "최적화된 상품명 1개만 순수 텍스트로 답변하세요. 설명이나 부연은 불필요합니다.\n\n"
        f"원본 상품명: {original}"
    )
    response = model.generate_content(prompt)
    return response.text.strip()


# ── 4단계: 검수 (코드 규칙 + AI) ───────────────────────────────────
def clean_by_rules(name: str) -> str:
    """코드 규칙으로 상품명을 1차 정제합니다."""
    name = re.sub(r'[*#~`\[\](){}|/\\]', '', name)
    for term in DELIVERY_TERMS:
        name = name.replace(term, '')
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > 100:
        name = name[:100].rsplit(' ', 1)[0].strip()
    return name


def verify_name(original: str, optimized: str, model: genai.GenerativeModel) -> tuple[str, str | None]:
    """AI로 2차 검수합니다."""
    prompt = (
        "아래 최적화된 상품명을 검수하고 필요시 수정해주세요.\n\n"
        f"원본 상품명: {original}\n"
        f"최적화된 상품명: {optimized}\n\n"
        "검수 기준:\n"
        "1. 의미 없는 수식어 제거 (최고, 대박, 완전, 특가, 강추 등)\n"
        "2. 중복 단어 제거 (예: 이어폰 무선이어폰 블루투스이어폰)\n"
        "3. 원본 상품명에 없는 무관한 브랜드명 제거 (단, 원본에 있는 브랜드는 유지)\n"
        "4. 원본 상품과 관련 없는 키워드 제거\n"
        "5. 네이버 금지 표현 제거\n\n"
        "다음 JSON 형식으로만 답변하세요. 다른 텍스트는 불필요합니다:\n"
        '{"final_name": "최종 상품명", "issues": "수정 사항 설명 (없으면 null)"}'
    )
    response = model.generate_content(prompt)
    raw = response.text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(raw)
    return data["final_name"], data.get("issues") or None


# ── 출력 파일 경로 생성 ─────────────────────────────────────────────
def get_output_path(input_path: str) -> str:
    dir_name = os.path.dirname(os.path.abspath(input_path))
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    today = datetime.now().strftime("%Y%m%d")
    n = 1
    while True:
        candidate = os.path.join(dir_name, f"{base_name}_seo_최적화_{today}_{n}.xlsx")
        if not os.path.exists(candidate):
            return candidate
        n += 1


# ── 메인 ────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 SEO 롱테일 상품명 최적화 에이전트")
    parser.add_argument("input", help="처리할 엑셀 파일 경로 (.xlsx)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[오류] 파일을 찾을 수 없습니다: {args.input}")
        return 1

    config = load_config()
    gemini_key   = os.environ.get("GEMINI_API_KEY")    or config.get("GEMINI_API_KEY")
    naver_id     = os.environ.get("NAVER_CLIENT_ID")   or config.get("NAVER_CLIENT_ID")
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

    print(f"엑셀 파일 로드 중: {args.input}")
    wb = openpyxl.load_workbook(args.input)
    ws = wb.active

    H_COL = 8
    data_rows = []
    for row_idx in range(2, ws.max_row + 1):
        cell_value = ws.cell(row=row_idx, column=H_COL).value
        if cell_value and str(cell_value).strip():
            data_rows.append((row_idx, str(cell_value).strip()))

    print(f"전체 데이터 행: {ws.max_row - 1}개 / 처리 대상: {len(data_rows)}개\n")

    errors = []
    issues_log = []

    for i, (row_idx, original) in enumerate(data_rows, 1):
        preview = original[:35] + ("..." if len(original) > 35 else "")
        print(f"[{i}/{len(data_rows)}] {preview}")

        try:
            # 1단계: 키워드 후보 + 카테고리 감지
            print(f"  [1/4] 키워드 후보 생성 및 카테고리 감지 중...")
            candidates = generate_keyword_candidates(original, keyword_model)
            category_id = detect_category(original, keyword_model)
            category_name = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")
            print(f"  [1/4] 카테고리: {category_name} / 후보 키워드 {len(candidates)}개")

            # 2단계: 검색어트렌드 + 쇼핑인사이트 조회
            print(f"  [2/4] 검색량 조회 중 (검색어트렌드 + 쇼핑인사이트)...")
            search_scores   = query_search_trend(candidates, naver_id, naver_secret)
            shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
            top_keywords    = combine_and_select(search_scores, shopping_scores, candidates, n=5)
            print(f"  [2/4] 상위 키워드: {', '.join(top_keywords)}")

            # 3단계: 최적화
            print(f"  [3/4] 상품명 최적화 중...")
            optimized = optimize_name(original, top_keywords, optimize_model)
            cleaned   = clean_by_rules(optimized)
            print(f"  [3/4] 최적화: {cleaned}")

            # 4단계: AI 검수
            print(f"  [4/4] 검수 중...")
            final_name, issues = verify_name(original, cleaned, verify_model)
            if issues:
                print(f"  [4/4] 수정됨 → {final_name}")
                print(f"  [4/4] 사유: {issues}")
            else:
                print(f"  [4/4] 이상 없음 → {final_name}")

            ws.cell(row=row_idx, column=H_COL).value = final_name

            if issues:
                issues_log.append({
                    "행": row_idx,
                    "원본": original,
                    "최적화": cleaned,
                    "최종": final_name,
                    "수정사항": issues,
                })

        except Exception as e:
            print(f"  ※ 오류 발생 → 원본 유지 ({e})")
            errors.append({"행": row_idx, "원본": original, "오류": str(e)})

    # 저장
    output_path = get_output_path(args.input)
    wb.save(output_path)
    print(f"\n저장 완료: {output_path}")

    # 결과 보고
    print("\n" + "=" * 55)
    print("처리 결과 보고")
    print("=" * 55)
    print(f"처리 완료: {len(data_rows) - len(errors)}건 / 오류(원본 유지): {len(errors)}건")

    if errors:
        print(f"\n[오류 목록] {len(errors)}건")
        for err in errors:
            print(f"  행 {err['행']:>4} | {err['원본'][:40]}")
            print(f"         오류: {err['오류']}")

    if issues_log:
        print(f"\n[검수 수정 목록] {len(issues_log)}건")
        for log in issues_log:
            print(f"  행 {log['행']:>4} | 원본  : {log['원본'][:40]}")
            print(f"         최종  : {log['최종'][:40]}")
            print(f"         사유  : {log['수정사항']}")

    if not errors and not issues_log:
        print("\n특이 사항 없음. 모든 상품명이 정상 최적화되었습니다.")

    print("=" * 55)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
