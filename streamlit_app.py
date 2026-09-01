"""
이동평균선(5/10/60/200일) 매수 신호 분석기 — 웹앱 버전
=====================================================

로컬 실행:
    py -m streamlit run streamlit_app.py

배포 후에는 폰/PC 어디서든 브라우저로 URL만 열면 됩니다.
"""

import plotly.graph_objects as go
import streamlit as st

from analyzer import MA_WINDOWS, analyze, fetch_data

st.set_page_config(page_title="이동평균선 매수 신호 분석기", page_icon="📈", layout="wide")

st.title("📈 이동평균선 매수 신호 분석기")
st.caption("5 / 10 / 60 / 200일 이동평균선 규칙 기반 참고용 기술적 분석 도구")

with st.form("ticker_form"):
    ticker = st.text_input(
        "종목코드 또는 티커",
        value="AAPL",
        help="한국 종목은 6자리 코드(예: 005930), 해외는 티커(예: AAPL, TSLA)",
    ).strip()
    submitted = st.form_submit_button("분석하기", use_container_width=True)

if submitted or ticker:
    if not ticker:
        st.warning("종목코드 또는 티커를 입력해주세요.")
        st.stop()

    try:
        with st.spinner(f"'{ticker}' 데이터 조회 중..."):
            df = fetch_data(ticker)
            result = analyze(df)
    except Exception as e:
        st.error(f"오류: {e}")
        st.stop()

    # ---- 요약 카드 ----
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("기준일", result["date"])
    col2.metric("현재가", f"{result['close']:,.2f}")
    col3.metric("종합 점수", f"{result['score']} / {result['max_score']}")
    col4.metric("판단", result["decision"])

    if result["score"] >= 5:
        st.success(f"**{result['decision']}**")
    elif result["score"] >= 2:
        st.warning(f"**{result['decision']}**")
    else:
        st.error(f"**{result['decision']}**")

    # ---- 이동평균선 값 ----
    ma_cols = st.columns(4)
    for c, (name, val) in zip(ma_cols, result["ma"].items()):
        c.metric(name, f"{val:,.2f}")

    # ---- 체크리스트 ----
    st.subheader("체크리스트")
    for desc, ok, pts in result["checks"]:
        mark = "✅" if ok else "➖"
        sign = f"+{pts}" if pts > 0 else f"{pts}"
        st.markdown(f"{mark} `{sign:>3}` {desc}")

    # ---- 차트 (인터랙티브: 핀치/드래그 확대, 커서 위치 가격 표시) ----
    st.subheader("차트")
    plot_df = df.tail(300)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=plot_df.index, y=plot_df["Close"], name="종가",
            line=dict(color="#333333", width=1.6),
        )
    )
    colors = {"MA5": "#e74c3c", "MA10": "#f39c12", "MA60": "#2ecc71", "MA200": "#3498db"}
    for w in MA_WINDOWS:
        col = f"MA{w}"
        fig.add_trace(
            go.Scatter(
                x=plot_df.index, y=plot_df[col], name=col,
                line=dict(color=colors[col], width=1.4),
            )
        )
    fig.update_layout(
        hovermode="x unified",  # 커서 위치의 날짜에서 모든 선의 가격을 한번에 표시
        height=600,
        xaxis_title="날짜",
        yaxis_title="가격",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.update_xaxes(rangeslider_visible=True)  # 하단 미니맵으로도 구간 확대 가능
    st.plotly_chart(fig, use_container_width=True)
    st.caption("차트를 드래그하면 확대, 더블클릭하면 원상복구됩니다. 손가락/마우스를 올리면 해당 날짜의 가격이 표시돼요.")

    st.info(
        "※ 본 결과는 이동평균선 규칙만으로 계산한 참고용 기술적 신호이며 투자 자문이 아닙니다. "
        "거래량, 재무상태, 시장 상황 등을 함께 고려하시고 투자 판단과 책임은 본인에게 있습니다."
    )
