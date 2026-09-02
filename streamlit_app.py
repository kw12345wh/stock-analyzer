"""
이동평균선(5/10/60/200일) 매수 신호 분석기 — 웹앱 버전
=====================================================

로컬 실행:
    py -m streamlit run streamlit_app.py

배포 후에는 폰/PC 어디서든 브라우저로 URL만 열면 됩니다.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analyzer import MA_WINDOWS, analyze, backtest_forward_returns, fetch_data, simulate_position

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

    # ---- ATR 기반 손절선 후보 ----
    st.subheader("🛡️ 변동성 기반 손절선 (ATR)")
    if result.get("stop_loss"):
        st.caption(
            f"최근 14일 평균 변동폭(ATR)은 {result['atr']:,.2f} — 현재가의 {result['atr_pct']:.1f}% 수준이에요. "
            "이 변동성을 기준으로 손절선 후보를 배수별로 계산했습니다. 정답은 없고 "
            "본인의 리스크 허용도에 맞는 배수를 참고용으로 고르시면 됩니다."
        )
        stop_cols = st.columns(3)
        for c, (label, info) in zip(stop_cols, result["stop_loss"].items()):
            c.metric(label, f"{info['price']:,.2f}", f"-{info['pct_below']:.1f}%")
        st.caption(
            "배수가 작을수록(1.5×) 타이트하게 끊어서 손실은 작지만 정상적인 등락에도 자주 걸리고, "
            "배수가 클수록(3×) 여유는 있지만 손실 폭이 커집니다."
        )
    else:
        st.info("변동성(ATR) 계산에 필요한 데이터가 부족합니다.")

    # ---- 포지션 사이징 / 최대손실 계산기 ----
    st.subheader("💰 포지션 사이징 / 최대손실 계산기")
    st.caption(
        "분할매수 계획(1차 매수 + 하락 시 추가매수)을 입력하면 누적 평단가·총 투자금과, "
        "최악의 경우 얼마를 잃는지 미리 계산해드려요."
    )

    total_capital = st.number_input(
        "전체 투자 자산 (선택 — 입력하면 손실이 전체 자산의 몇 %인지도 계산됩니다)",
        min_value=0.0, value=0.0, step=1_000_000.0, format="%.0f",
    )

    st.markdown("**분할매수 계획** (행 추가/삭제 가능 — 하락률 0%는 지금 시점 매수)")
    plan_df = st.data_editor(
        pd.DataFrame({"하락률(%)": [0, 30, 40], "매수금액": [1_000_000, 1_000_000, 1_000_000]}),
        num_rows="dynamic",
        use_container_width=True,
        key="position_plan_editor",
    )

    worst_case_options = {}
    if result.get("stop_loss"):
        for label, info in result["stop_loss"].items():
            worst_case_options[f"{label} 손절선 ({info['price']:,.2f})"] = info["price"]
    worst_case_options["직접 입력"] = None

    wc_choice = st.selectbox("최악 시나리오 기준가 (여기까지 떨어진다고 가정)", list(worst_case_options.keys()))
    if worst_case_options[wc_choice] is None:
        worst_price = st.number_input(
            "최악 시나리오 가격", min_value=0.0, value=float(result["close"]) * 0.7,
        )
    else:
        worst_price = worst_case_options[wc_choice]

    plan = [
        {"drop_pct": row["하락률(%)"], "amount": row["매수금액"]}
        for _, row in plan_df.dropna().iterrows()
    ]
    sim = simulate_position(result["close"], plan, worst_case_price=worst_price, total_capital=total_capital or None)

    if sim["steps"]:
        steps_show = pd.DataFrame(sim["steps"])
        for col in ["매수가", "매수금액", "누적평단가", "누적투자금"]:
            steps_show[col] = steps_show[col].map(lambda v: f"{v:,.2f}")
        for col in ["매수수량", "누적수량"]:
            steps_show[col] = steps_show[col].map(lambda v: f"{v:,.4f}")
        st.dataframe(steps_show, use_container_width=True, hide_index=True)

        r1, r2, r3 = st.columns(3)
        r1.metric("최종 평단가", f"{sim['avg_price']:,.2f}")
        r2.metric("총 투자금", f"{sim['total_invested']:,.0f}")
        r3.metric("총 보유수량", f"{sim['total_shares']:,.4f}")

        if sim.get("worst_case_loss") is not None:
            loss = sim["worst_case_loss"]
            capital_note = (
                f" · 전체 자산 대비 **{sim['worst_case_loss_pct_of_capital']:.1f}%**"
                if "worst_case_loss_pct_of_capital" in sim
                else ""
            )
            if loss > 0:
                st.error(
                    f"**최악 시나리오** — 가격이 {sim['worst_case_price']:,.2f}까지 떨어지면: "
                    f"평가손실 **{loss:,.0f}** (투자금 대비 **-{sim['worst_case_loss_pct']:.1f}%**)"
                    + capital_note
                )
            else:
                st.success(
                    f"입력하신 기준가({sim['worst_case_price']:,.2f})는 최종 평단가({sim['avg_price']:,.2f})보다 "
                    f"높아서, 이 시나리오에서는 오히려 평가이익 **+{abs(loss):,.0f}** "
                    f"(투자금 대비 **+{abs(sim['worst_case_loss_pct']):.1f}%**)입니다. "
                    "분할매수로 평단가를 충분히 낮췄다는 뜻이에요 — 진짜 '최악'을 보려면 "
                    "기준가를 최종 매수 단계보다 더 낮게 입력해보세요."
                )
    else:
        st.info("위 표에 매수금액을 입력하면 결과가 계산됩니다.")

    st.caption(
        "※ 소수 단위 매수 가능 여부는 증권사·시장마다 다릅니다. 이 계산은 참고용 시뮬레이션이며, "
        "실제 체결가/수수료/세금은 반영되지 않았습니다."
    )

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

    # ---- 백테스트: 과거 이 판단이 나왔을 때 실제로 어떻게 됐나 ----
    st.subheader("📊 백테스트 — 과거 이 판단, 실제로 맞았을까?")
    st.caption(
        f"조회 기간({df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}) 동안 "
        "'매수 고려/관망/회피' 판단이 나온 시점마다, 이후 실제 주가가 어떻게 움직였는지 집계한 결과입니다."
    )

    horizon_labels = {5: "1주", 20: "1개월", 60: "3개월", 120: "6개월"}
    with st.spinner("과거 데이터로 신호 검증 중..."):
        bt_results = backtest_forward_returns(df)

    tabs = st.tabs([horizon_labels[h] for h in bt_results])
    for tab, (h, table) in zip(tabs, bt_results.items()):
        with tab:
            show = table.copy()
            for col in ["평균수익률", "승률", "최악", "최선"]:
                show[col] = show[col].apply(lambda v: f"{v*100:+.1f}%" if pd.notna(v) else "표본 없음")
            show = show.rename(columns={
                "평균수익률": f"{horizon_labels[h]} 뒤 평균수익률",
                "승률": "승률(상승 확률)",
                "최악": "최악의 경우",
                "최선": "최선의 경우",
            })
            st.dataframe(show, use_container_width=True, hide_index=True)

    st.warning(
        "※ 표본 구간들이 서로 겹쳐 있어 통계적으로 완전히 독립적인 표본은 아닙니다. "
        "'과거엔 대체로 이랬다' 정도의 참고 자료이며, 과거 성과가 미래 수익을 보장하지 않습니다."
    )
