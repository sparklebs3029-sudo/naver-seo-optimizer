#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 + 상품 소싱 - Streamlit 웹앱
"""

import io
import os
import streamlit as st
import google.generativeai as genai
import openpyxl
from openpyxl.utils import get_column_letter
from datetime import datetime

from naver_seo_agent import (
    KEYWORD_SYSTEM, OPTIMIZE_SYSTEM, VERIFY_SYSTEM, GEMINI_CONFIG,
    NAVER_CATEGORIES, PROHIBITED_GROUPS,
    generate_keyword_candidates, detect_category,
    query_search_trend, query_shopping_insight, combine_and_select,
    optimize_name, clean_by_rules, verify_name, enforce_min_length,
    get_trending_products,
)

st.set_page_config(
    page_title="셀러부스트",
    page_icon="🛒",
    layout="centered",
)

st.title("셀러부스트")
st.caption("네이버 SEO 상품명 최적화 + 트렌드 상품 소싱")

# ── 세션 상태 초기화 ─────────────────────────────────────────────────
for key, default in [("gemini_key", ""), ("naver_id", ""), ("naver_secret", ""), ("keys_saved", False), ("daily_file_count", {}),]:
    if key not in st.session_state:
        st.session_state[key] = default

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
            "3. **데이터랩(검색어트렌드)** + **데이터랩(쇼핑인사이트)** + **검색(쇼핑)** 선택\n"
            "4. Client ID · Secret 복사"
        )

    st.divider()

    gemini_key   = st.text_input("Gemini API Key",      value=st.session_state.gemini_key,   type="password", placeholder="AIzaSy...")
    naver_id     = st.text_input("Naver Client ID",     value=st.session_state.naver_id,     type="password")
    naver_secret = st.text_input("Naver Client Secret", value=st.session_state.naver_secret, type="password")

    keys_ready = bool(gemini_key and naver_id and naver_secret)

    if st.button("API 키 저장", use_container_width=True, type="primary", disabled=not keys_ready):
        st.session_state.gemini_key   = gemini_key
        st.session_state.naver_id     = naver_id
        st.session_state.naver_secret = naver_secret
        st.session_state.keys_saved   = True

    if st.session_state.keys_saved and keys_ready:
        st.success("저장됨 (탭을 닫으면 초기화)")
    elif not keys_ready:
        st.warning("API 키를 모두 입력 후 저장해주세요")

    st.caption("브라우저 자동완성을 사용하면 다음 접속 시 자동 입력됩니다.")


# ── 탭 구성 ──────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["상품명 최적화", "상품 소싱"])


# ════════════════════════════════════════════════════════════════════
# TAB 1: 상품명 최적화
# ════════════════════════════════════════════════════════════════════
with tab1:
    if not keys_ready:
        st.info("왼쪽 사이드바에 API 키를 먼저 입력하고 저장해주세요.")

    input_mode = st.radio("입력 방식", ["엑셀 파일", "개별 입력"], horizontal=True)

    # ── 개별 입력 모드 ────────────────────────────────────────────────
    if input_mode == "개별 입력":
        single_name = st.text_input("상품명 입력", placeholder="최적화할 상품명을 입력하세요")
        single_btn  = st.button("최적화", type="primary",
                                disabled=not keys_ready or not single_name,
                                use_container_width=True)

        if single_btn and single_name:
            genai.configure(api_key=gemini_key)
            keyword_model  = genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM, generation_config=GEMINI_CONFIG)
            optimize_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM, generation_config=GEMINI_CONFIG)
            verify_model   = genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM, generation_config=GEMINI_CONFIG)

            with st.spinner("최적화 중..."):
                try:
                    st.info("1/4 키워드 후보 생성 및 카테고리 감지 중...")
                    candidates  = generate_keyword_candidates(single_name, keyword_model)
                    category_id = detect_category(single_name, keyword_model)
                    cat_name    = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")

                    st.info(f"2/4 검색량 조회 중... (카테고리: {cat_name})")
                    search_scores   = query_search_trend(candidates, naver_id, naver_secret)
                    shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
                    top_keywords    = combine_and_select(search_scores, shopping_scores, candidates, n=5)

                    st.info(f"3/4 최적화 중... (키워드: {', '.join(top_keywords[:3])})")
                    optimized = optimize_name(single_name, top_keywords, optimize_model)
                    cleaned   = clean_by_rules(optimized)

                    st.info("4/4 검수 중...")
                    final_name, issues = verify_name(single_name, cleaned, verify_model)
                    if len(final_name) < 25:
                        final_name = enforce_min_length(final_name, single_name, top_keywords, optimize_model)

                    st.success("최적화 완료!")
                    st.divider()

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown("**원본 상품명**")
                        st.code(single_name, language=None)
                    with col_b:
                        st.markdown("**최적화 상품명**")
                        st.code(final_name, language=None)

                    st.caption(f"글자수: {len(single_name)}자 → {len(final_name)}자  |  적용 키워드: {', '.join(top_keywords)}")

                    if issues:
                        st.warning(f"검수 수정사항: {issues}")

                except Exception as e:
                    st.error(f"오류 발생: {e}")

    # ── 엑셀 파일 모드 ────────────────────────────────────────────────
    else:
        uploaded_file = st.file_uploader(
            "엑셀 파일 업로드 (.xlsx)",
            type=["xlsx"],
            help="1행: 헤더 / 상품명 열은 아래 드롭다운에서 선택합니다.",
            key="optimizer_file",
        )

        if uploaded_file:
            raw_bytes = uploaded_file.read()
            wb_preview = openpyxl.load_workbook(io.BytesIO(raw_bytes))
            ws_preview = wb_preview.active

            col_options = []
            for col in range(1, ws_preview.max_column + 1):
                col_letter  = get_column_letter(col)
                header_val  = ws_preview.cell(row=1, column=col).value
                label       = f"{col_letter}열 - {header_val}" if header_val else f"{col_letter}열"
                col_options.append(label)

            default_idx      = min(7, len(col_options) - 1)
            selected_label   = st.selectbox("상품명 열 선택", col_options, index=default_idx,
                                            help="샵플링: H열 / 플레이오토: 해당 열 직접 선택")
            selected_col_idx = col_options.index(selected_label) + 1

            data_rows_preview = [
                (r, str(ws_preview.cell(row=r, column=selected_col_idx).value).strip())
                for r in range(2, ws_preview.max_row + 1)
                if ws_preview.cell(row=r, column=selected_col_idx).value
                and str(ws_preview.cell(row=r, column=selected_col_idx).value).strip()
            ]
            st.info(f"총 **{len(data_rows_preview)}개** 상품명 감지됨")

            with st.expander("상품명 미리보기 (상위 5개)"):
                for _, name in data_rows_preview[:5]:
                    st.text(name)

            btn_area  = st.empty()
            start_btn = btn_area.button("최적화 시작", type="primary",
                                        disabled=not keys_ready, use_container_width=True)

            if start_btn:
                btn_area.warning("처리 중입니다. 완료될 때까지 기다려주세요...")

                genai.configure(api_key=gemini_key)
                keyword_model  = genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM, generation_config=GEMINI_CONFIG)
                optimize_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM, generation_config=GEMINI_CONFIG)
                verify_model   = genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM, generation_config=GEMINI_CONFIG)

                wb = openpyxl.load_workbook(io.BytesIO(raw_bytes))
                ws = wb.active

                data_rows = [
                    (r, str(ws.cell(row=r, column=selected_col_idx).value).strip())
                    for r in range(2, ws.max_row + 1)
                    if ws.cell(row=r, column=selected_col_idx).value
                    and str(ws.cell(row=r, column=selected_col_idx).value).strip()
                ]

                progress_bar  = st.progress(0, text="처리 준비 중...")
                status_box    = st.empty()
                log_entries:  list[str] = []
                log_box       = st.empty()
                errors:       list[dict] = []
                issues_log:   list[dict] = []

                for i, (row_idx, original) in enumerate(data_rows, 1):
                    pct     = i / len(data_rows)
                    preview = original[:40] + ("..." if len(original) > 40 else "")
                    stage   = ""

                    try:
                        stage = "키워드 후보 생성"
                        progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 1/4 키워드 후보 생성 중...")
                        status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n1/4 키워드 후보 생성 및 카테고리 감지 중...")
                        candidates  = generate_keyword_candidates(original, keyword_model)

                        stage = "카테고리 감지"
                        category_id = detect_category(original, keyword_model)
                        cat_name    = next((k for k, v in NAVER_CATEGORIES.items() if v == category_id), "생활/건강")

                        stage = "검색량 조회"
                        progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 2/4 검색량 조회 중...")
                        status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n2/4 검색량 조회 중 (카테고리: {cat_name})")
                        search_scores   = query_search_trend(candidates, naver_id, naver_secret)
                        shopping_scores = query_shopping_insight(candidates, category_id, naver_id, naver_secret)
                        top_keywords    = combine_and_select(search_scores, shopping_scores, candidates, n=5)

                        stage = "상품명 최적화"
                        progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 3/4 상품명 최적화 중...")
                        status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n3/4 최적화 중... (키워드: {', '.join(top_keywords[:3])})")
                        optimized = optimize_name(original, top_keywords, optimize_model)
                        cleaned   = clean_by_rules(optimized)

                        stage = "검수"
                        progress_bar.progress(pct, text=f"[{i}/{len(data_rows)}] 4/4 검수 중...")
                        status_box.info(f"**[{i}/{len(data_rows)}]** `{preview}`  \n4/4 검수 중...")
                        final_name, issues = verify_name(original, cleaned, verify_model)

                        # 25자 미만이면 재확장
                        if len(final_name) < 25:
                            final_name = enforce_min_length(final_name, original, top_keywords, optimize_model)

                        ws.cell(row=row_idx, column=selected_col_idx).value = final_name

                        if issues:
                            issues_log.append({"행": row_idx, "원본": original,
                                               "최적화": cleaned, "최종": final_name, "수정사항": issues})

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

                    log_box.text_area("처리 로그", "\n\n".join(log_entries[-8:]),
                                      height=300, label_visibility="collapsed")

                progress_bar.progress(1.0, text="완료!")
                status_box.success(f"처리 완료: {len(data_rows) - len(errors)}개 성공 / {len(errors)}개 오류")
                btn_area.empty()

                buf = io.BytesIO()
                wb.save(buf)
                buf.seek(0)

                now          = datetime.now()
                datetime_str = now.strftime("%Y%m%d%H%M")
                today_str    = now.strftime("%Y%m%d")

                # 날짜가 바뀌면 카운터 초기화
                if today_str not in st.session_state.daily_file_count:
                    st.session_state.daily_file_count = {today_str: 0}
                st.session_state.daily_file_count[today_str] += 1
                n = st.session_state.daily_file_count[today_str]

                stem     = os.path.splitext(uploaded_file.name)[0]
                out_name = f"{stem}_최적화_{datetime_str}_{n}.xlsx"

                st.divider()
                st.download_button("결과 엑셀 다운로드", data=buf, file_name=out_name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   type="primary", use_container_width=True)

                c1, c2, c3 = st.columns(3)
                c1.metric("전체", len(data_rows))
                c2.metric("성공", len(data_rows) - len(errors))
                c3.metric("오류", len(errors))

                if errors:
                    st.subheader("오류 목록")
                    for err in errors:
                        st.error(f"행 {err['행']} | {err['원본'][:50]}\n"
                                 f"단계: {err.get('단계', '알 수 없음')} | 오류: {err['오류']}")

                if issues_log:
                    st.subheader("검수 수정 목록")
                    for log in issues_log:
                        st.warning(f"행 {log['행']} | 원본: {log['원본'][:45]}\n"
                                   f"최종: {log['최종'][:45]}\n사유: {log['수정사항']}")

                if not errors and not issues_log:
                    st.success("특이 사항 없음. 모든 상품명이 정상 최적화되었습니다.")

        else:
            st.markdown("---")
            st.markdown(
                "**사용 방법**\n"
                "1. 왼쪽 사이드바에 API 키 입력 후 저장\n"
                "2. 엑셀 파일 업로드\n"
                "3. 상품명 열 선택 (샵플링: H열 기본)\n"
                "4. **최적화 시작** 클릭\n"
                "5. 완료 후 결과 파일 다운로드"
            )


# ════════════════════════════════════════════════════════════════════
# TAB 2: 상품 소싱
# ════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("네이버 쇼핑 트렌드 상품 소싱")
    st.caption("DataLab 쇼핑인사이트 + 쇼핑 검색으로 현재 잘 팔리는 상품을 찾아드립니다.")

    if not keys_ready:
        st.info("왼쪽 사이드바에 API 키를 먼저 입력하고 저장해주세요.")

    col1, col2 = st.columns(2)
    with col1:
        period_label = st.selectbox("조회 기간", ["7일 (주간)", "30일 (월간)"], index=0)
    with col2:
        category_name = st.selectbox("카테고리", list(NAVER_CATEGORIES.keys()), index=8)

    # 금지 품목 설정
    with st.expander("금지 품목 설정", expanded=False):
        st.caption("체크된 항목에 해당하는 상품은 결과에서 자동 제외됩니다.")
        active_prohibited = []
        cols = st.columns(2)
        for idx, group in enumerate(PROHIBITED_GROUPS.keys()):
            with cols[idx % 2]:
                if st.checkbox(group, value=True, key=f"prohibit_{group}"):
                    active_prohibited.append(group)
        extra_input = st.text_input("추가 금지 키워드 (쉼표로 구분)", placeholder="예: 담배, 주류")
        extra_prohibited = [kw.strip() for kw in extra_input.split(",") if kw.strip()]

    st.divider()

    source_btn = st.button("트렌드 조회 시작", type="primary",
                           disabled=not keys_ready, use_container_width=True)

    if source_btn:
        period_days = 7 if "7일" in period_label else 30
        category_id = NAVER_CATEGORIES[category_name]

        with st.spinner(f"{category_name} 카테고리 트렌드를 분석 중입니다..."):
            results = get_trending_products(
                category_name     = category_name,
                category_id       = category_id,
                period_days       = period_days,
                client_id         = naver_id,
                client_secret     = naver_secret,
                active_prohibited = active_prohibited,
                extra_prohibited  = extra_prohibited,
                top_n             = 5,
            )

        if results:
            st.success(f"총 **{len(results)}개** 상품을 찾았습니다.")

            # 트렌드 점수 기준 정렬
            results_sorted = sorted(results, key=lambda x: x["트렌드점수"], reverse=True)

            # 키워드별로 그룹화해서 표시
            current_keyword = None
            for item in results_sorted:
                if item["키워드"] != current_keyword:
                    current_keyword = item["키워드"]
                    st.markdown(f"#### 키워드: {current_keyword}  (트렌드 점수: {item['트렌드점수']})")

                col_a, col_b, col_c = st.columns([4, 2, 2])
                with col_a:
                    st.markdown(f"[{item['상품명']}]({item['링크']})")
                with col_b:
                    st.write(item["최저가"])
                with col_c:
                    st.write(item["쇼핑몰"])

            st.divider()

            # 전체 결과 데이터프레임
            with st.expander("전체 결과 표로 보기"):
                import pandas as pd
                df = pd.DataFrame(results_sorted)[["키워드", "트렌드점수", "상품명", "최저가", "쇼핑몰", "카테고리"]]
                st.dataframe(df, use_container_width=True, hide_index=True)

        else:
            st.warning(
                "결과가 없습니다.\n\n"
                "**확인 사항:**\n"
                "- 네이버 앱에서 **검색(쇼핑)** API가 활성화되어 있는지 확인해주세요.\n"
                "- 네이버 개발자센터 → 내 애플리케이션 → 사용 API에 **검색** 추가 필요"
            )
