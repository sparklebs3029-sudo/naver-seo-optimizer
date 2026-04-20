#!/usr/bin/env python3
"""
네이버 SEO 상품명 최적화 + 상품 소싱 - Streamlit 웹앱
"""

import io
import json
import os
import pathlib
import threading
import time
import streamlit as st
import google.generativeai as genai
import openpyxl
from openpyxl.utils import get_column_letter
from datetime import datetime
from dataclasses import dataclass, field

from naver_seo_agent import (
    KEYWORD_SYSTEM, CLASSIFY_SYSTEM, OPTIMIZE_SYSTEM, VERIFY_SYSTEM, GEMINI_CONFIG,
    NAVER_CATEGORIES, PROHIBITED_GROUPS,
    get_trending_products,
)
from orchestrator import run_with_orchestration, OrchestratorReport

APP_VERSION = "v1.5.1"  # validate_result 제거 오탐 수정

st.set_page_config(
    page_title="셀러부스트",
    page_icon="🛒",
    layout="centered",
)

st.title("셀러부스트")
st.caption(f"네이버 SEO 상품명 최적화 + 트렌드 상품 소싱  |  {APP_VERSION}")

# ── API 키 영구 저장/로드 (사용자 홈 디렉터리) ──────────────────────
_KEYS_PATH = pathlib.Path.home() / ".sellerboost" / "keys.json"

def _load_saved_keys() -> dict:
    try:
        return json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_keys(gemini: str, naver_id: str, secret: str) -> None:
    _KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEYS_PATH.write_text(
        json.dumps({"gemini_key": gemini, "naver_id": naver_id, "naver_secret": secret}),
        encoding="utf-8",
    )

def _delete_keys() -> None:
    try:
        _KEYS_PATH.unlink()
    except Exception:
        pass

# ── 세션 상태 초기화 (앱 첫 실행 시 저장된 키 자동 로드) ────────────
if "keys_loaded" not in st.session_state:
    # Streamlit Secrets 우선 (클라우드 배포), 없으면 로컬 파일
    _from_secrets = {}
    try:
        _g = st.secrets.get("GEMINI_API_KEY", "")
        _n = st.secrets.get("NAVER_CLIENT_ID", "")
        _s = st.secrets.get("NAVER_CLIENT_SECRET", "")
        if _g or _n:
            _from_secrets = {"gemini_key": _g, "naver_id": _n, "naver_secret": _s}
    except Exception:
        pass

    _saved = _from_secrets or _load_saved_keys()
    st.session_state.gemini_key   = _saved.get("gemini_key",   "")
    st.session_state.naver_id     = _saved.get("naver_id",     "")
    st.session_state.naver_secret = _saved.get("naver_secret", "")
    st.session_state.keys_saved   = bool(st.session_state.gemini_key)
    st.session_state.keys_loaded  = True

if "daily_file_count" not in st.session_state:
    st.session_state.daily_file_count = {}


# ── 배치 처리 상태 클래스 ──────────────────────────────────────────
@dataclass
class _BatchState:
    running:    bool               = False
    stopped:    bool               = False
    done:       bool               = False
    progress:   int                = 0
    total:      int                = 0
    log:        list               = field(default_factory=list)
    status:     str                = ""
    all_reports: list              = field(default_factory=list)
    hard_errors: list              = field(default_factory=list)
    result_buf: bytes | None       = None
    out_name:   str                = ""
    stop_event: threading.Event    = field(default_factory=threading.Event)


if "batch" not in st.session_state:
    st.session_state.batch = _BatchState()

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

    col_save, col_clear = st.columns([3, 1])
    with col_save:
        if st.button("API 키 저장", use_container_width=True, type="primary", disabled=not keys_ready):
            st.session_state.gemini_key   = gemini_key
            st.session_state.naver_id     = naver_id
            st.session_state.naver_secret = naver_secret
            st.session_state.keys_saved   = True
            _save_keys(gemini_key, naver_id, naver_secret)
    with col_clear:
        if st.button("삭제", use_container_width=True, disabled=not st.session_state.keys_saved):
            st.session_state.gemini_key   = ""
            st.session_state.naver_id     = ""
            st.session_state.naver_secret = ""
            st.session_state.keys_saved   = False
            _delete_keys()

    if st.session_state.keys_saved and keys_ready:
        st.success("저장됨 — 다음 접속 시 자동 로드됩니다")
    elif not keys_ready:
        st.warning("API 키를 모두 입력 후 저장해주세요")


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
            models = {
                'keyword':  genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM,  generation_config=GEMINI_CONFIG),
                'classify': genai.GenerativeModel("gemini-2.0-flash", system_instruction=CLASSIFY_SYSTEM, generation_config=GEMINI_CONFIG),
                'optimize': genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM, generation_config=GEMINI_CONFIG),
                'verify':   genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM,   generation_config=GEMINI_CONFIG),
            }
            api_keys = {'naver_id': naver_id, 'naver_secret': naver_secret}

            status_ph = st.empty()

            def single_progress(attempt: int, stage: str, detail: str = "") -> None:
                prefix = f"[시도 {attempt}/3] " if attempt > 1 else ""
                msg = f"{prefix}{stage}"
                if detail:
                    msg += f" — {detail}"
                status_ph.info(msg)

            with st.spinner("최적화 중..."):
                final_name, report = run_with_orchestration(
                    single_name, models, api_keys,
                    max_retries=3,
                    progress_callback=single_progress,
                )

            status_ph.empty()
            st.success(f"최적화 완료! (시도 {report.attempts}회)")
            st.divider()

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**원본 상품명**")
                st.code(single_name, language=None)
            with col_b:
                st.markdown("**최적화 상품명**")
                st.code(final_name, language=None)

            st.caption(f"글자수: {len(single_name)}자 → {len(final_name)}자")

            if report.attempts > 1:
                st.info(f"품질 기준 통과까지 {report.attempts}회 시도했습니다.")

            if not report.passed_validation and report.warning:
                st.warning(f"오케스트레이터 경고: {report.warning}")

            if report.errors:
                with st.expander(f"처리 중 오류 {len(report.errors)}건"):
                    for err in report.errors:
                        color = "🔴" if not err.resolved else "🟠"
                        st.markdown(
                            f"{color} **[{err.stage}]** {err.error_type}  \n"
                            f"조치: {err.action_taken}  \n"
                            f"메시지: `{err.message[:100]}`"
                        )

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

            batch = st.session_state.batch

            # ── 시작 버튼 (처리 중이 아닐 때만 표시) ────────────────
            if not batch.running:
                start_btn = st.button("최적화 시작", type="primary",
                                      disabled=not keys_ready, use_container_width=True)
                if start_btn:
                    genai.configure(api_key=gemini_key)
                    models = {
                        'keyword':  genai.GenerativeModel("gemini-2.0-flash", system_instruction=KEYWORD_SYSTEM,  generation_config=GEMINI_CONFIG),
                        'classify': genai.GenerativeModel("gemini-2.0-flash", system_instruction=CLASSIFY_SYSTEM, generation_config=GEMINI_CONFIG),
                        'optimize': genai.GenerativeModel("gemini-2.0-flash", system_instruction=OPTIMIZE_SYSTEM, generation_config=GEMINI_CONFIG),
                        'verify':   genai.GenerativeModel("gemini-2.0-flash", system_instruction=VERIFY_SYSTEM,   generation_config=GEMINI_CONFIG),
                    }
                    api_keys = {'naver_id': naver_id, 'naver_secret': naver_secret}

                    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes))
                    ws = wb.active
                    data_rows = [
                        (r, str(ws.cell(row=r, column=selected_col_idx).value).strip())
                        for r in range(2, ws.max_row + 1)
                        if ws.cell(row=r, column=selected_col_idx).value
                        and str(ws.cell(row=r, column=selected_col_idx).value).strip()
                    ]

                    # 파일명은 메인 스레드에서 미리 계산 (스레드에서 session_state 쓰기 금지)
                    now          = datetime.now()
                    datetime_str = now.strftime("%Y%m%d%H%M")
                    today_str    = now.strftime("%Y%m%d")
                    if today_str not in st.session_state.daily_file_count:
                        st.session_state.daily_file_count = {today_str: 0}
                    st.session_state.daily_file_count[today_str] += 1
                    n        = st.session_state.daily_file_count[today_str]
                    stem     = os.path.splitext(uploaded_file.name)[0]
                    out_name = f"{stem}_최적화_{datetime_str}_{n}.xlsx"

                    new_batch = _BatchState(total=len(data_rows), running=True, out_name=out_name)
                    st.session_state.batch = new_batch

                    def _run_batch(state: _BatchState, drows, _models, _api_keys, _wb, _ws, _col_idx):
                        for i, (row_idx, original) in enumerate(drows, 1):
                            if state.stop_event.is_set():
                                state.status  = f"중단됨 — {i - 1}개 완료"
                                state.stopped = True
                                break

                            def _prog(attempt, stage, detail="", _i=i, _t=len(drows)):
                                prefix = f"[시도{attempt}] " if attempt > 1 else ""
                                state.status = f"[{_i}/{_t}] {prefix}{stage}" + (f" — {detail}" if detail else "")

                            final_name, report = run_with_orchestration(
                                original, _models, _api_keys,
                                max_retries=3,
                                progress_callback=_prog,
                            )
                            state.all_reports.append(report)
                            _ws.cell(row=row_idx, column=_col_idx).value = final_name

                            unresolved = [e for e in report.errors if not e.resolved]
                            if unresolved:
                                state.hard_errors.append({"행": row_idx, "원본": original, "보고서": report})

                            retry_note = f" (재시도 {report.attempts}회)" if report.attempts > 1 else ""
                            warn_note  = " ⚠️" if not report.passed_validation else ""
                            state.log.append(
                                f"[{i}/{len(drows)}]{retry_note}{warn_note}\n"
                                f"  원본 : {original}\n"
                                f"  최종 : {final_name}  ({len(final_name)}자)"
                            )
                            state.progress = i

                        buf = io.BytesIO()
                        _wb.save(buf)
                        buf.seek(0)
                        state.result_buf = buf.read()
                        state.running    = False
                        state.done       = True

                    t = threading.Thread(
                        target=_run_batch,
                        args=(new_batch, data_rows, models, api_keys, wb, ws, selected_col_idx),
                        daemon=True,
                    )
                    t.start()
                    st.rerun()

            # ── 처리 중 UI ────────────────────────────────────────────
            if batch.running:
                pct = batch.progress / batch.total if batch.total else 0
                st.progress(pct, text=batch.status or "처리 준비 중...")

                if st.button("⛔ 중단", type="secondary", use_container_width=True):
                    batch.stop_event.set()

                if batch.log:
                    st.text_area("처리 로그", "\n\n".join(batch.log[-8:]),
                                 height=300, label_visibility="collapsed")
                time.sleep(1)
                st.rerun()

            # ── 완료 / 중단 후 결과 표시 ─────────────────────────────
            if (batch.done or batch.stopped) and batch.result_buf:
                all_reports  = batch.all_reports
                hard_errors  = batch.hard_errors
                success_count = sum(1 for r in all_reports if not [e for e in r.errors if not e.resolved])
                retry_count   = sum(1 for r in all_reports if r.attempts > 1)

                if batch.stopped:
                    st.warning(batch.status)
                else:
                    st.success(
                        f"처리 완료: {success_count}개 성공 / {len(hard_errors)}개 오류"
                        + (f" / {retry_count}개 재시도 발생" if retry_count else "")
                    )

                st.divider()
                st.download_button(
                    "결과 엑셀 다운로드", data=batch.result_buf, file_name=batch.out_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary", use_container_width=True,
                )

                if st.button("새 파일 처리", use_container_width=True):
                    st.session_state.batch = _BatchState()
                    st.rerun()

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("전체",   batch.progress)
                c2.metric("성공",   success_count)
                c3.metric("오류",   len(hard_errors))
                c4.metric("재시도", retry_count)

                if hard_errors:
                    st.subheader("오류 목록")
                    for item in hard_errors:
                        report = item["보고서"]
                        with st.expander(f"🔴 행 {item['행']} | {item['원본'][:50]}"):
                            for err in report.errors:
                                icon = "🟠" if err.resolved else "🔴"
                                st.markdown(
                                    f"{icon} **[{err.stage}]** {err.error_type}  \n"
                                    f"조치: {err.action_taken}  \n"
                                    f"메시지: `{err.message[:120]}`"
                                )
                            if report.warning:
                                st.warning(report.warning)

                retried = [r for r in all_reports if r.attempts > 1 and r.passed_validation]
                if retried:
                    with st.expander(f"🟠 재시도 후 성공 {len(retried)}건"):
                        for r in retried:
                            st.markdown(
                                f"- **{r.original[:45]}** → `{r.final_name[:45]}`  "
                                f"({r.attempts}회 시도)"
                            )
                            if r.validation_failures:
                                st.caption("실패 이유: " + " / ".join(r.validation_failures[:2]))

                if not hard_errors and retry_count == 0 and batch.done:
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
        period_label = st.selectbox("조회 기간", ["1일 (일간)", "7일 (주간)", "30일 (월간)"], index=1)
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
        period_days = 1 if "1일" in period_label else (7 if "7일" in period_label else 30)
        category_id = NAVER_CATEGORIES[category_name]

        with st.spinner(f"{category_name} 카테고리 트렌드를 분석 중입니다..."):
            fetched = get_trending_products(
                category_name     = category_name,
                category_id       = category_id,
                period_days       = period_days,
                client_id         = naver_id,
                client_secret     = naver_secret,
                active_prohibited = active_prohibited,
                extra_prohibited  = extra_prohibited,
                top_n             = 5,
            )

        if fetched:
            st.session_state.trend_results = fetched
        else:
            st.session_state.trend_results = []
            st.warning(
                "결과가 없습니다.\n\n"
                "**확인 사항:**\n"
                "- 네이버 앱에서 **검색(쇼핑)** API가 활성화되어 있는지 확인해주세요.\n"
                "- 네이버 개발자센터 → 내 애플리케이션 → 사용 API에 **검색** 추가 필요"
            )

    def _parse_price(price_str):
        try:
            return int(price_str.replace(",", "").replace("원", ""))
        except (ValueError, AttributeError):
            return -1

    if st.session_state.get("trend_results"):
        results = st.session_state.trend_results
        st.success(f"총 **{len(results)}개** 상품을 찾았습니다.")

        col_caption, col_pv_label, col_dropdown, col_empty = st.columns([2.5, 0.7, 1.8, 1])
        with col_caption:
            st.markdown("<p style='padding-top:7px; margin:0; color:#888; font-size:14px'>정렬 기준을 선택하세요.</p>", unsafe_allow_html=True)
        with col_pv_label:
            st.markdown("<p style='padding-top:7px; margin:0'>판매가</p>", unsafe_allow_html=True)
        with col_dropdown:
            price_sort = st.selectbox(
                "판매가",
                ["높은 가격순", "낮은 가격순"],
                key="price_sort",
                label_visibility="collapsed",
            )

        # 키워드별로 그룹화 (트렌드 점수 내림차순 고정)
        from collections import defaultdict
        keyword_order = []
        keyword_groups = defaultdict(list)
        for item in sorted(results, key=lambda x: x["트렌드점수"], reverse=True):
            if item["키워드"] not in keyword_groups:
                keyword_order.append((item["키워드"], item["트렌드점수"]))
            keyword_groups[item["키워드"]].append(item)

        # 각 그룹 내 판매가 정렬
        results_sorted = []
        for keyword, score in keyword_order:
            group = keyword_groups[keyword]
            if price_sort == "높은 가격순":
                group = sorted(group, key=lambda x: _parse_price(x["최저가"]), reverse=True)
            elif price_sort == "낮은 가격순":
                group = sorted(group, key=lambda x: (_parse_price(x["최저가"]) == -1, _parse_price(x["최저가"])))
            st.markdown(f"#### 키워드: {keyword}  (트렌드 점수: {score})")
            for item in group:
                col_a, col_b, col_c = st.columns([4, 2, 2])
                with col_a:
                    st.markdown(f"[{item['상품명']}]({item['링크']})")
                with col_b:
                    st.write(item["최저가"])
                with col_c:
                    st.write(item["쇼핑몰"])
            results_sorted.extend(group)

        st.divider()

        with st.expander("전체 결과 표로 보기"):
            import pandas as pd
            df = pd.DataFrame(results_sorted)[["키워드", "트렌드점수", "상품명", "최저가", "쇼핑몰", "카테고리"]]
            st.dataframe(df, use_container_width=True, hide_index=True)
