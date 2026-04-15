#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 - Streamlit 웹앱
"""

import io
import os
import streamlit as st
import google.generativeai as genai
import openpyxl
from datetime import datetime

from naver_seo_agent import (
    KEYWORD_SYSTEM,
    OPTIMIZE_SYSTEM,
    VERIFY_SYSTEM,
    NAVER_CATEGORIES,
    generate_keyword_candidates,
    detect_category,
    query_search_trend,
    query_shopping_insight,
    combine_and_select,
    optimize_name,
    clean_by_rules,
    verify_name,
)

st.set_page_config(
    page_title="네이버 SEO 상품명 최적화",
    page_icon="🛒",
    layout="centered",
)

st.title("네이버 SEO 상품명 최적화")
st.caption("엑셀 H열의 상품명을 네이버 검색 데이터 기반으로 자동 최적화합니다.")

# ── 사이드바: API 키 입력 ────────────────────────────────────────────
with st.sidebar:
    st.header("API 키 설정")

    with st.expander("Gemini API 키 발급 방법"):
        st.markdown(
            "1. [Google AI Studio](https://aistudio.google.com) 접속\n"
            "2. 구글 계정으로 로그인\n"
            "3. **Get API key** 클릭 → 무료 발급"
        )

    with st.expander("네이버 API 키 발급 방법"):
        st.markdown(
            "1. [네이버 개발자센터](https://developers.naver.com) 접속\n"
            "2. 애플리케이션 등록\n"
            "3. **데이터랩(검색어트렌드)** + **데이터랩(쇼핑인사이트)** 선택\n"
            "4. Client ID · Secret 복사"
        )

    st.divider()

    gemini_key   = st.text_input("Gemini API Key",      type="password", placeholder="AIzaSy...")
    naver_id     = st.text_input("Naver Client ID",     type="password")
    naver_secret = st.text_input("Naver Client Secret", type="password")

    keys_ready = bool(gemini_key and naver_id and naver_secret)
    if keys_ready:
        st.success("API 키 입력 완료")
    else:
        st.warning("API 키를 모두 입력해주세요")


# ── 메인: 파일 업로드 ────────────────────────────────────────────────
H_COL = 8

if not keys_ready:
    st.info("왼쪽 사이드바에 API 키를 먼저 입력해주세요.")

uploaded_file = st.file_uploader(
    "H열에 상품명이 있는 엑셀 파일 (.xlsx)",
    type=["xlsx"],
    help="1행: 헤더 / H열(8번째 열): 상품명",
)

if uploaded_file:
    raw_bytes = uploaded_file.read()

    # 미리보기용 파싱
    wb_preview = openpyxl.load_workbook(io.BytesIO(raw_bytes))
    ws_preview = wb_preview.active

    data_rows_preview = [
        (r, str(ws_preview.cell(row=r, column=H_COL).value).strip())
        for r in range(2, ws_preview.max_row + 1)
        if ws_preview.cell(row=r, column=H_COL).value
        and str(ws_preview.cell(row=r, column=H_COL).value).strip()
    ]
    total = len(data_rows_preview)

    st.info(f"총 **{total}개** 상품명 감지됨")

    with st.expander("상품명 미리보기 (상위 5개)"):
        for _, name in data_rows_preview[:5]:
            st.text(name)

    start_btn = st.button(
        "최적화 시작",
        type="primary",
        disabled=not keys_ready,
        use_container_width=True,
    )

    if start_btn:
        # ── API 초기화 ───────────────────────────────────────────────
        genai.configure(api_key=gemini_key)
        keyword_model  = genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM)
        optimize_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM)
        verify_model   = genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM)

        # ── 엑셀 로드 ────────────────────────────────────────────────
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes))
        ws = wb.active

        data_rows = [
            (r, str(ws.cell(row=r, column=H_COL).value).strip())
            for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=H_COL).value
            and str(ws.cell(row=r, column=H_COL).value).strip()
        ]

        # ── 진행 UI ──────────────────────────────────────────────────
        progress_bar = st.progress(0, text="처리 준비 중...")
        status_box   = st.empty()
        log_lines: list[str] = []
        log_box = st.empty()

        errors:     list[dict] = []
        issues_log: list[dict] = []

        for i, (row_idx, original) in enumerate(data_rows, 1):
            pct     = i / len(data_rows)
            preview = original[:40] + ("..." if len(original) > 40 else "")

            try:
                # 1단계: 키워드 후보 생성 + 카테고리 감지
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 1/4 키워드 후보 생성 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n1/4 키워드 후보 생성 및 카테고리 감지 중...")
                candidates  = generate_keyword_candidates(original, keyword_model)
                category_id = detect_category(original, keyword_model)
                cat_name    = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")

                # 2단계: 검색량 조회
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 2/4 검색량 조회 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n2/4 검색량 조회 중 (카테고리: {cat_name})")
                search_scores   = query_search_trend(candidates, naver_id, naver_secret)
                shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
                top_keywords    = combine_and_select(search_scores, shopping_scores, candidates, n=5)

                # 3단계: 최적화
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 3/4 상품명 최적화 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n3/4 상품명 최적화 중... (키워드: {', '.join(top_keywords[:3])})")
                optimized = optimize_name(original, top_keywords, optimize_model)
                cleaned   = clean_by_rules(optimized)

                # 4단계: 검수
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 4/4 검수 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n4/4 검수 중...")
                final_name, issues = verify_name(original, cleaned, verify_model)

                ws.cell(row=row_idx, column=H_COL).value = final_name

                if issues:
                    issues_log.append({
                        "행": row_idx, "원본": original,
                        "최적화": cleaned, "최종": final_name, "수정사항": issues,
                    })
                    log_lines.append(f"[{i}] ✅(수정) {original[:25]} → {final_name[:25]}")
                else:
                    log_lines.append(f"[{i}] ✅       {original[:25]} → {final_name[:25]}")

            except Exception as e:
                errors.append({"행": row_idx, "원본": original, "오류": str(e)})
                ws.cell(row=row_idx, column=H_COL).value = original  # 원본 유지
                log_lines.append(f"[{i}] ❌ {original[:25]} (오류: {str(e)[:35]})")

            # 로그 갱신 (최근 20줄)
            log_box.text_area("처리 로그", "\n".join(log_lines[-20:]), height=220, label_visibility="collapsed")

        # ── 완료 처리 ────────────────────────────────────────────────
        progress_bar.progress(1.0, text="완료!")
        status_box.success(f"처리 완료: {len(data_rows) - len(errors)}개 성공 / {len(errors)}개 오류(원본 유지)")

        # ── 결과 다운로드 ─────────────────────────────────────────────
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        today    = datetime.now().strftime("%Y%m%d")
        stem     = os.path.splitext(uploaded_file.name)[0]
        out_name = f"{stem}_seo_최적화_{today}.xlsx"

        st.divider()
        st.download_button(
            label="결과 엑셀 다운로드",
            data=buf,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )

        # ── 결과 요약 ─────────────────────────────────────────────────
        c1, c2, c3 = st.columns(3)
        c1.metric("전체", len(data_rows))
        c2.metric("성공", len(data_rows) - len(errors))
        c3.metric("오류", len(errors))

        if errors:
            st.subheader("오류 목록")
            for err in errors:
                st.error(
                    f"행 {err['행']} | {err['원본'][:50]}\n"
                    f"오류: {err['오류']}"
                )

        if issues_log:
            st.subheader("검수 수정 목록")
            for log in issues_log:
                st.warning(
                    f"행 {log['행']} | 원본: {log['원본'][:45]}\n"
                    f"최종: {log['최종'][:45]}\n"
                    f"사유: {log['수정사항']}"
                )

        if not errors and not issues_log:
            st.success("특이 사항 없음. 모든 상품명이 정상 최적화되었습니다.")

else:
    st.markdown("---")
    st.markdown("**사용 방법**")
    st.markdown(
        "1. 왼쪽 사이드바에 API 키 3개 입력\n"
        "2. 엑셀 파일 업로드 (H열 = 상품명, 1행 = 헤더)\n"
        "3. **최적화 시작** 버튼 클릭\n"
        "4. 완료 후 결과 파일 다운로드"
    )
