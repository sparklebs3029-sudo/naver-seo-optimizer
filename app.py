#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 - Streamlit 웹앱
"""

import io
import os
import streamlit as st
import google.generativeai as genai
import openpyxl
from openpyxl.utils import get_column_letter
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
    page_title="상품명 최적화",
    page_icon="🛒",
    layout="centered",
)

st.title("네이버 SEO 상품명 최적화")
st.caption("엑셀의 상품명을 네이버 검색 데이터 기반으로 자동 최적화합니다.")

# ── 세션 내 API 키 유지 ──────────────────────────────────────────────
if "gemini_key" not in st.session_state:
    st.session_state.gemini_key   = ""
if "naver_id" not in st.session_state:
    st.session_state.naver_id     = ""
if "naver_secret" not in st.session_state:
    st.session_state.naver_secret = ""

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

    gemini_key   = st.text_input("Gemini API Key",      value=st.session_state.gemini_key,   type="password", placeholder="AIzaSy...")
    naver_id     = st.text_input("Naver Client ID",     value=st.session_state.naver_id,     type="password")
    naver_secret = st.text_input("Naver Client Secret", value=st.session_state.naver_secret, type="password")

    # 입력값을 세션에 즉시 반영
    st.session_state.gemini_key   = gemini_key
    st.session_state.naver_id     = naver_id
    st.session_state.naver_secret = naver_secret

    keys_ready = bool(gemini_key and naver_id and naver_secret)
    if keys_ready:
        st.success("API 키 입력 완료")
    else:
        st.warning("API 키를 모두 입력해주세요")

    st.caption("💡 브라우저의 비밀번호 저장 기능을 사용하면 다음 접속 시 자동 입력됩니다.")


# ── 메인 ─────────────────────────────────────────────────────────────
if not keys_ready:
    st.info("왼쪽 사이드바에 API 키를 먼저 입력해주세요.")

uploaded_file = st.file_uploader(
    "엑셀 파일 업로드 (.xlsx)",
    type=["xlsx"],
    help="1행: 헤더 / 상품명 열은 아래 드롭다운에서 선택합니다.",
)

if uploaded_file:
    raw_bytes = uploaded_file.read()

    # ── 열 선택 드롭다운 ─────────────────────────────────────────────
    wb_preview = openpyxl.load_workbook(io.BytesIO(raw_bytes))
    ws_preview = wb_preview.active

    col_options = []
    for col in range(1, ws_preview.max_column + 1):
        col_letter = get_column_letter(col)
        header_val = ws_preview.cell(row=1, column=col).value
        label = f"{col_letter}열 - {header_val}" if header_val else f"{col_letter}열"
        col_options.append(label)

    default_idx = min(7, len(col_options) - 1)  # H열 기본값
    selected_label = st.selectbox(
        "상품명 열 선택",
        col_options,
        index=default_idx,
        help="상품명이 있는 열을 선택하세요. 샵플링: H열 / 플레이오토: 해당 열 직접 선택",
    )
    selected_col_idx = col_options.index(selected_label) + 1  # 1-based

    # ── 미리보기 ──────────────────────────────────────────────────────
    data_rows_preview = [
        (r, str(ws_preview.cell(row=r, column=selected_col_idx).value).strip())
        for r in range(2, ws_preview.max_row + 1)
        if ws_preview.cell(row=r, column=selected_col_idx).value
        and str(ws_preview.cell(row=r, column=selected_col_idx).value).strip()
    ]
    total = len(data_rows_preview)
    st.info(f"총 **{total}개** 상품명 감지됨")

    with st.expander("상품명 미리보기 (상위 5개)"):
        for _, name in data_rows_preview[:5]:
            st.text(name)

    # ── 시작 버튼 (처리 중 숨김) ─────────────────────────────────────
    btn_area = st.empty()
    start_btn = btn_area.button(
        "최적화 시작",
        type="primary",
        disabled=not keys_ready,
        use_container_width=True,
    )

    if start_btn:
        btn_area.warning("처리 중입니다. 완료될 때까지 기다려주세요...")

        # ── API 초기화 ───────────────────────────────────────────────
        genai.configure(api_key=gemini_key)
        keyword_model  = genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM)
        optimize_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM)
        verify_model   = genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM)

        # ── 엑셀 로드 ────────────────────────────────────────────────
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes))
        ws = wb.active

        data_rows = [
            (r, str(ws.cell(row=r, column=selected_col_idx).value).strip())
            for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=selected_col_idx).value
            and str(ws.cell(row=r, column=selected_col_idx).value).strip()
        ]

        # ── 진행 UI ──────────────────────────────────────────────────
        progress_bar = st.progress(0, text="처리 준비 중...")
        status_box   = st.empty()
        log_entries: list[str] = []
        log_box = st.empty()

        errors:      list[dict] = []
        issues_log:  list[dict] = []
        keyword_log: list[tuple[int, list[str]]] = []

        for i, (row_idx, original) in enumerate(data_rows, 1):
            pct     = i / len(data_rows)
            preview = original[:40] + ("..." if len(original) > 40 else "")
            stage   = ""

            try:
                # 1단계
                stage = "키워드 후보 생성"
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 1/4 키워드 후보 생성 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n1/4 키워드 후보 생성 및 카테고리 감지 중...")
                candidates  = generate_keyword_candidates(original, keyword_model)

                stage = "카테고리 감지"
                category_id = detect_category(original, keyword_model)
                cat_name    = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")

                # 2단계
                stage = "검색량 조회"
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 2/4 검색량 조회 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n2/4 검색량 조회 중 (카테고리: {cat_name})")
                search_scores   = query_search_trend(candidates, naver_id, naver_secret)
                shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
                top_keywords    = combine_and_select(search_scores, shopping_scores, candidates, n=5)

                # 3단계
                stage = "상품명 최적화"
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 3/4 상품명 최적화 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n3/4 상품명 최적화 중... (키워드: {', '.join(top_keywords[:3])})")
                optimized = optimize_name(original, top_keywords, optimize_model)
                cleaned   = clean_by_rules(optimized)

                # 4단계
                stage = "검수"
                progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 4/4 검수 중...")
                status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n4/4 검수 중...")
                final_name, issues = verify_name(original, cleaned, verify_model)

                ws.cell(row=row_idx, column=selected_col_idx).value = final_name
                keyword_log.append((row_idx, top_keywords))

                if issues:
                    issues_log.append({
                        "행": row_idx, "원본": original,
                        "최적화": cleaned, "최종": final_name, "수정사항": issues,
                    })

                log_entries.append(
                    f"[{i}/{len(data_rows)}]\n"
                    f"  원본 : {original}\n"
                    f"  최종 : {final_name}  ({len(final_name)}자)"
                )

            except Exception as e:
                errors.append({"행": row_idx, "원본": original, "단계": stage, "오류": str(e)})
                ws.cell(row=row_idx, column=selected_col_idx).value = original
                log_entries.append(
                    f"[{i}/{len(data_rows)}]\n"
                    f"  원본 : {original}\n"
                    f"  오류 : [{stage}] {str(e)[:55]}"
                )

            # 로그 갱신 (최근 8개 항목)
            log_box.text_area(
                "처리 로그",
                "\n\n".join(log_entries[-8:]),
                height=300,
                label_visibility="collapsed",
            )

        # 핵심 키워드 컬럼 추가
        kw_col = ws.max_column + 1
        ws.cell(row=1, column=kw_col).value = "선정된 핵심 키워드"
        for kw_row, kws in keyword_log:
            ws.cell(row=kw_row, column=kw_col).value = ", ".join(kws)

        # ── 완료 ─────────────────────────────────────────────────────
        progress_bar.progress(1.0, text="완료!")
        status_box.success(f"처리 완료: {len(data_rows) - len(errors)}개 성공 / {len(errors)}개 오류(원본 유지)")
        btn_area.empty()

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
                    f"단계: {err.get('단계', '알 수 없음')} | 오류: {err['오류']}"
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
        "2. 엑셀 파일 업로드\n"
        "3. 상품명이 있는 열 선택 (샵플링: H열 / 플레이오토: 해당 열 직접 선택)\n"
        "4. **최적화 시작** 버튼 클릭\n"
        "5. 완료 후 결과 파일 다운로드"
    )
