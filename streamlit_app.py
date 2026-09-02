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
from plotly.subplots import make_subplots

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

    # 국내 종목(6자리 코드)은 원, 그 외(해외 티커)는 달러로 간주해서 표시 단위를 맞춤
    is_krx = ticker.isdigit() and len(ticker) == 6
    currency_symbol = "원" if is_krx else "$"
    money_fmt = "%,d원" if is_krx else "$%,.2f"
    amount_fmt = "%,d원" if is_krx else "$%,d"

    def fmt_money(v) -> str:
        return f"{v:,.0f}원" if is_krx else f"${v:,.2f}"

    def fmt_money_md(v) -> str:
        # st.write/markdown/error/success처럼 마크다운을 해석하는 곳에 넣을 때 전용.
        # "$"가 두 번 나오면 그 사이를 수식(LaTeX)으로 오인해 통째로 사라지므로 이스케이프한다.
        return fmt_money(v).replace("$", r"\$")

    # ---- 요약 카드 ----
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("기준일", result["date"])
    col2.metric("현재가", f"{result['close']:,.2f}")
    col3.metric("종합 점수", f"{result['score']} / {result['max_score']}")
    col4.metric("판단", result["decision"])
    st.caption(
        "🕒 실시간 시세가 아닙니다. 해외 주식은 미국 장 마감 후 한국시간 새벽에 전일 종가로 갱신되고, "
        "국내 주식은 장중에는 그 시점까지의 잠정 현재가가 표시될 수 있어요. "
        "위 '기준일'이 오늘 날짜라면 아직 마감 전(장중) 데이터일 수 있습니다."
    )

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
        f"전체 투자 자산 (선택 — {currency_symbol} 단위, 입력하면 손실이 전체 자산의 몇 %인지도 계산됩니다)",
        min_value=0.0, value=0.0, step=1_000_000.0, format="%.0f",
    )

    st.markdown(
        "**분할매수 계획** (행 추가/삭제 가능. "
        "맨 위 행에 실제 1차 매수가를 입력하면, 그 아래 행들의 하락률(%)은 **오늘 현재가가 아니라 "
        "이 1차 매수가를 기준으로** 계산돼요. 맨 위 행을 비워두면(0) 오늘 현재가 기준으로 계산합니다. "
        "매수금액은 계산 없이 직접 입력하시면 됩니다.)"
    )
    # 매수가(직접입력)는 0이면 "미입력"으로 취급 (데이터 에디터에서 빈 값이 'None'으로
    # 보기 흉하게 렌더링되는 문제를 피하기 위해 0을 미입력 값으로 사용)
    plan_df = st.data_editor(
        pd.DataFrame({
            "하락률(%)": pd.array([0, 30, 40], dtype="float64"),
            "매수가(직접입력, 0=미입력)": pd.array([0, 0, 0], dtype="float64"),
            "매수금액": pd.array([1_000_000, 1_000_000, 1_000_000], dtype="float64"),
        }),
        num_rows="dynamic",
        use_container_width=True,
        key="position_plan_editor",
        column_config={
            "하락률(%)": st.column_config.NumberColumn("하락률(%)", format="%.1f%%"),
            "매수가(직접입력, 0=미입력)": st.column_config.NumberColumn(
                "매수가(직접입력, 0=미입력)", format=money_fmt,
                help="이미 체결된 실제 매수가가 있으면 여기에 입력하세요. 0으로 두면 하락률로 계산합니다.",
            ),
            "매수금액": st.column_config.NumberColumn("매수금액", format=amount_fmt),
        },
    )

    worst_case_options = {}
    if result.get("stop_loss"):
        for label, info in result["stop_loss"].items():
            worst_case_options[f"{label} 손절선 ({fmt_money(info['price'])})"] = info["price"]
    worst_case_options["직접 입력"] = None

    wc_choice = st.selectbox("최악 시나리오 기준가 (여기까지 떨어진다고 가정)", list(worst_case_options.keys()))
    if worst_case_options[wc_choice] is None:
        worst_price = st.number_input(
            f"최악 시나리오 가격 ({currency_symbol})", min_value=0.0, value=float(result["close"]) * 0.7,
        )
    else:
        worst_price = worst_case_options[wc_choice]

    plan = []
    for _, row in plan_df.iterrows():
        amount = row.get("매수금액")
        if pd.isna(amount) or amount <= 0:
            continue
        buy_price = row.get("매수가(직접입력, 0=미입력)")
        plan.append({
            "drop_pct": row.get("하락률(%)") if pd.notna(row.get("하락률(%)")) else 0,
            "amount": amount,
            "buy_price": buy_price if pd.notna(buy_price) and buy_price > 0 else None,
        })

    # 하락률(%)만 입력한 행의 기준가 — 맨 위 행에 실제 1차 매수가가 있으면 그 가격을 기준으로,
    # 없으면 오늘 현재가를 기준으로 계산 (첫 행 자체는 buy_price가 있으면 그 값을 그대로 씀)
    anchor_price = result["close"]
    if len(plan_df) > 0:
        first_buy_price = plan_df.iloc[0].get("매수가(직접입력, 0=미입력)")
        if pd.notna(first_buy_price) and first_buy_price > 0:
            anchor_price = first_buy_price

    sim = simulate_position(anchor_price, plan, worst_case_price=worst_price, total_capital=total_capital or None)

    if sim["steps"]:
        steps_show = pd.DataFrame(sim["steps"])
        steps_show["하락률(%)"] = steps_show["하락률(%)"].map(lambda v: f"{v:.1f}%")
        for col in ["매수가", "매수금액", "누적평단가", "누적투자금"]:
            steps_show[col] = steps_show[col].map(fmt_money)
        for col in ["매수수량", "누적수량"]:
            steps_show[col] = steps_show[col].map(lambda v: f"{v:,.4f}")
        st.dataframe(steps_show, use_container_width=True, hide_index=True)

        r1, r2, r3 = st.columns(3)
        r1.metric("최종 평단가", fmt_money(sim['avg_price']))
        r2.metric("총 투자금", fmt_money(sim['total_invested']))
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
                    f"**최악 시나리오** — 가격이 {fmt_money_md(sim['worst_case_price'])}까지 떨어지면: "
                    f"평가손실 **{fmt_money_md(loss)}** (투자금 대비 **-{sim['worst_case_loss_pct']:.1f}%**)"
                    + capital_note
                )
            else:
                st.success(
                    f"입력하신 기준가({fmt_money_md(sim['worst_case_price'])})는 최종 평단가({fmt_money_md(sim['avg_price'])})보다 "
                    f"높아서, 이 시나리오에서는 오히려 평가이익 **+{fmt_money_md(abs(loss))}** "
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

    # ---- 거래량 요약 ----
    st.subheader("📊 거래량")
    if result.get("volume") is not None and result.get("vma20") is not None:
        vcol1, vcol2, vcol3 = st.columns(3)
        vcol1.metric("당일 거래량", f"{result['volume']:,.0f}")
        vcol2.metric("20일 평균 거래량", f"{result['vma20']:,.0f}")
        ratio = result["volume_ratio"]
        ratio_note = "급증" if ratio >= 1.5 else ("급감" if ratio <= 0.5 else "평이")
        vcol3.metric("평균 대비", f"{ratio:.2f}배", ratio_note, delta_color="off")
        st.caption(
            "거래량 급증은 그 방향(상승/하락)에 힘이 실렸다는 뜻이고, 거래량 급감 상태의 등락은 "
            "관심이 적어 신뢰도가 낮은 움직임일 수 있어요. 체크리스트의 거래량 항목도 참고하세요."
        )
    else:
        st.info("거래량 평균(VMA20) 계산에 필요한 데이터가 부족합니다.")

    # ---- 차트 (인터랙티브: 핀치/드래그 확대, 커서 위치 가격 표시) — 가격 + 거래량 ----
    st.subheader("차트")
    plot_df = df.tail(300)

    # 기간 버튼 — Plotly 내장 rangeselector는 모바일 폭에서 툴바/범례와 겹치는 문제가 있어
    # 스트림릿 네이티브 버튼으로 대체 (탭 한 번으로 기간 이동, 폭에 맞게 자동으로 줄바꿈됨)
    period_options = {"1개월": 30, "3개월": 91, "6개월": 182, "1년": 365, "전체": None}
    if "chart_period" not in st.session_state:
        st.session_state["chart_period"] = "6개월"
    def _select_period(label: str) -> None:
        st.session_state["chart_period"] = label

    # on_click 콜백을 써야 클릭 즉시(같은 실행 안에서) 눌린 버튼이 강조 표시됨
    # (버튼의 반환값으로만 처리하면 강조 표시가 한 클릭 늦게 반영됨)
    period_cols = st.columns(len(period_options))
    for pcol, label in zip(period_cols, period_options):
        is_selected = st.session_state["chart_period"] == label
        pcol.button(
            label, use_container_width=True,
            type="primary" if is_selected else "secondary",
            key=f"period_{label}", on_click=_select_period, args=(label,),
        )
    days = period_options[st.session_state["chart_period"]]
    default_start = plot_df.index[0] if days is None else max(plot_df.index[0], plot_df.index[-1] - pd.Timedelta(days=days))

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df.index, y=plot_df["Close"], name="종가",
            line=dict(color="#333333", width=1.6),
        ),
        row=1, col=1,
    )
    colors = {"MA5": "#e74c3c", "MA10": "#f39c12", "MA60": "#2ecc71", "MA200": "#3498db"}
    for w in MA_WINDOWS:
        col = f"MA{w}"
        fig.add_trace(
            go.Scatter(
                x=plot_df.index, y=plot_df[col], name=col,
                line=dict(color=colors[col], width=1.4),
            ),
            row=1, col=1,
        )

    # 거래량 바 — 전일 대비 상승/하락에 따라 색상 구분
    prev_close = plot_df["Close"].shift(1)
    volume_colors = ["#e74c3c" if c >= p else "#3498db" for c, p in zip(plot_df["Close"], prev_close)]
    fig.add_trace(
        go.Bar(x=plot_df.index, y=plot_df["Volume"], name="거래량", marker_color=volume_colors, opacity=0.7),
        row=2, col=1,
    )
    if "VMA20" in plot_df:
        fig.add_trace(
            go.Scatter(
                x=plot_df.index, y=plot_df["VMA20"], name="VMA20",
                line=dict(color="#888888", width=1.2, dash="dot"),
            ),
            row=2, col=1,
        )

    fig.update_layout(
        hovermode="x unified",  # 커서 위치의 날짜에서 모든 선의 가격을 한번에 표시
        height=720,
        # 범례를 차트 안쪽 상단에 반투명으로 배치 — 항상 표시되는 툴바(모드바)와 자리를 다투지 않도록
        legend=dict(
            orientation="h", y=0.99, yanchor="top", x=0.01, xanchor="left",
            bgcolor="rgba(255,255,255,0.75)", font=dict(size=11),
        ),
        margin=dict(l=10, r=10, t=36, b=10),
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    fig.update_xaxes(title_text="날짜", row=2, col=1)

    # 하단 슬라이드바 — 손가락으로 좌우 끝을 드래그해 구간 이동/확대 (두 서브플롯이 x축 공유라 함께 움직임)
    fig.update_xaxes(
        rangeslider=dict(visible=True, thickness=0.08, range=[plot_df.index[0], plot_df.index[-1]]),
        range=[default_start, plot_df.index[-1]],  # 기본 화면은 위 기간 버튼 선택값만큼만 (슬라이드바로 전체 펼쳐보기 가능)
        row=2, col=1,
    )
    fig.update_xaxes(range=[default_start, plot_df.index[-1]], row=1, col=1)

    plotly_config = {
        "scrollZoom": True,  # 마우스 휠 / 손가락 핀치로 확대·축소
        "displayModeBar": True,  # 모바일에서도 항상 툴바(확대 버튼 등) 표시
        "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "toggleSpikelines"],
    }
    st.plotly_chart(fig, use_container_width=True, config=plotly_config)
    st.caption(
        "기간 버튼(1개월/3개월/…)으로 빠르게 이동하거나, 하단 슬라이드바 양끝을 드래그해 구간을 조절하세요. "
        "핀치(또는 마우스 휠)로 확대/축소, 더블클릭하면 원상복구됩니다."
    )

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

    decision_style = {
        "매수 고려": ("🟢", "#2ecc71"),
        "관망": ("🟡", "#f39c12"),
        "매수 보류·회피": ("🔴", "#e74c3c"),
    }
    example_amount = 1_000_000 if is_krx else 1_000  # "이 돈을 넣었다면" 예시용 금액

    tabs = st.tabs([horizon_labels[h] for h in bt_results])
    for tab, (h, table) in zip(tabs, bt_results.items()):
        with tab:
            st.caption(f"과거에 이 신호가 떴을 때 샀다고 가정하고, {horizon_labels[h]} 뒤 결과를 봤어요.")
            cols = st.columns(3)
            for c, (_, row) in zip(cols, table.iterrows()):
                label = row["판단"]
                emoji, color = decision_style.get(label, ("⚪", "#888888"))
                with c:
                    st.markdown(f"##### {emoji} {label}")
                    n = row["표본수"]
                    if n == 0 or pd.isna(row["평균수익률"]):
                        st.caption("표본 없음")
                        continue
                    avg, win, worst, best = row["평균수익률"], row["승률"], row["최악"], row["최선"]
                    verb = "올랐어요" if avg >= 0 else "내렸어요"
                    st.markdown(
                        f"<span style='font-size:1.8rem; font-weight:700; color:{color};'>"
                        f"{avg*100:+.1f}%</span> <span style='color:gray;'>{verb}</span>",
                        unsafe_allow_html=True,
                    )
                    st.write(f"과거 {n}번 중 **10번에 {round(win*10)}번꼴**로 상승했어요")
                    result_amount = example_amount * (1 + avg)
                    st.write(f"{fmt_money_md(example_amount)} 넣었다면 → **{fmt_money_md(result_amount)}**")
                    st.caption(f"최악 {worst*100:+.1f}% / 최선 {best*100:+.1f}%")

            # 세 판단의 평균수익률을 막대그래프로 한눈에 비교
            chart_table = table.dropna(subset=["평균수익률"])
            if not chart_table.empty:
                pct = chart_table["평균수익률"] * 100
                fig_bt = go.Figure(
                    go.Bar(
                        x=chart_table["판단"], y=pct,
                        marker_color=["#2ecc71" if v >= 0 else "#e74c3c" for v in pct],
                        text=[f"{v:+.1f}%" for v in pct],
                        textposition="outside",
                    )
                )
                fig_bt.update_layout(
                    height=260, margin=dict(l=10, r=10, t=20, b=10),
                    yaxis_title=f"{horizon_labels[h]} 뒤 평균수익률(%)",
                )
                st.plotly_chart(fig_bt, use_container_width=True, config={"displayModeBar": False})

            with st.expander("🔍 자세한 숫자로 보기 (표본수·승률 등)"):
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
