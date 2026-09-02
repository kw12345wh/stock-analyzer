"""
이동평균선(5/10/60/200일) 기반 매수 신호 분석기
=================================================

주의: 이 스크립트는 과거 가격 데이터를 이용한 "기술적 지표 규칙"을 계산해서
보여주는 도구일 뿐, 투자 자문이나 매수/매도 확정 신호가 아닙니다.
이동평균선은 후행지표이며, 거래량/재무/뉴스 등 다른 요인은 전혀 반영하지 않습니다.
최종 투자 판단과 책임은 본인에게 있습니다.

사용 예시
---------
    py -m pip install -r requirements.txt
    py analyzer.py 005930          # 삼성전자 (KRX, 6자리 코드)
    py analyzer.py AAPL            # 애플 (미국 티커)
    py analyzer.py TSLA --show     # 차트 창을 직접 띄워서 보기
    py analyzer.py 005930 --out samsung.png
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

MA_WINDOWS = [5, 10, 60, 200]


def fetch_data(ticker: str, start: str | None = None) -> pd.DataFrame:
    """FinanceDataReader로 시세를 가져온다.

    - 6자리 숫자 코드(예: 005930)면 한국 KRX 종목으로 처리
    - 그 외(AAPL, TSLA 등)는 해외 티커로 그대로 조회
    FinanceDataReader가 두 경우 모두 알아서 처리해준다.
    """
    if start is None:
        # MA200 계산 + 백테스트용 표본 확보를 위해 기본 6년치 조회
        # (최근 상장 종목은 FinanceDataReader가 있는 만큼만 알아서 반환함)
        start = (datetime.today() - timedelta(days=2200)).strftime("%Y-%m-%d")

    df = fdr.DataReader(ticker, start)
    if df is None or df.empty:
        raise ValueError(f"'{ticker}' 데이터를 가져오지 못했습니다. 티커/종목코드를 확인하세요.")

    df = df.sort_index()
    for w in MA_WINDOWS:
        df[f"MA{w}"] = df["Close"].rolling(window=w).mean()
    # 장중이라 당일 고가/저가가 아직 확정 안 된 경우를 대비해 직전 유효값으로 채움
    df["ATR14"] = compute_atr(df, period=14).ffill()
    df["VMA20"] = df["Volume"].rolling(window=20).mean()

    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(Average True Range, 평균 변동폭)을 계산한다.

    True Range = 다음 중 최댓값
      - 당일 고가 - 당일 저가
      - |당일 고가 - 전일 종가|
      - |당일 저가 - 전일 종가|
    ATR은 이 True Range의 period일 이동평균. 값이 클수록 변동성이 큰 종목이다.
    """
    high, low, close = df.get("High"), df.get("Low"), df["Close"]
    prev_close = close.shift(1)

    has_hl = (
        high is not None and low is not None
        and high.notna().sum() > period and low.notna().sum() > period
    )
    if has_hl:
        true_range = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
    else:
        # 고가/저가 데이터가 부실한 데이터 소스면 종가 변동폭으로 대체 계산
        true_range = close.diff().abs()

    return true_range.rolling(window=period).mean()


def crossed_above(fast: pd.Series, slow: pd.Series, lookback: int) -> bool:
    """최근 lookback 거래일 안에 fast선이 slow선을 아래에서 위로 돌파(골든크로스)했는지."""
    recent_fast, recent_slow = fast.tail(lookback + 1), slow.tail(lookback + 1)
    prev_below = recent_fast.shift(1) < recent_slow.shift(1)
    now_above = recent_fast > recent_slow
    return bool((prev_below & now_above).iloc[1:].any())


def crossed_below(fast: pd.Series, slow: pd.Series, lookback: int) -> bool:
    """최근 lookback 거래일 안에 데드크로스가 발생했는지."""
    recent_fast, recent_slow = fast.tail(lookback + 1), slow.tail(lookback + 1)
    prev_above = recent_fast.shift(1) > recent_slow.shift(1)
    now_below = recent_fast < recent_slow
    return bool((prev_above & now_below).iloc[1:].any())


def analyze(df: pd.DataFrame) -> dict:
    """이동평균선 4개를 규칙 기반으로 채점해서 매수/관망/회피 판단을 만든다."""
    last = df.iloc[-1]
    close = last["Close"]
    ma5, ma10, ma60, ma200 = last["MA5"], last["MA10"], last["MA60"], last["MA200"]

    if pd.isna(ma200):
        raise ValueError("MA200 계산에 필요한 데이터가 부족합니다. 더 긴 기간의 데이터가 필요합니다.")

    checks = []  # (설명, 충족여부, 배점)

    # 1) 정배열 / 역배열 — 추세 구조
    bullish_align = ma5 > ma10 > ma60 > ma200
    bearish_align = ma5 < ma10 < ma60 < ma200
    checks.append(("정배열 (MA5>MA10>MA60>MA200) — 강한 상승 추세 구조", bullish_align, 2))
    checks.append(("역배열 (MA5<MA10<MA60<MA200) — 강한 하락 추세 구조", bearish_align, -2))

    # 2) 장기추세 필터 — 현재가와 MA200
    price_above_200 = close > ma200
    checks.append(("현재가가 MA200(장기추세선) 위에 위치", price_above_200, 1))
    checks.append(("현재가가 MA200 아래에 위치 (장기 약세 구간)", not price_above_200, -1))

    # 3) MA200 기울기 — 장기추세 방향
    ma200_slope_up = None
    if len(df) > 220 and not pd.isna(df["MA200"].iloc[-21]):
        ma200_slope_up = ma200 > df["MA200"].iloc[-21]
        checks.append(("MA200이 20거래일 전보다 상승 중 (장기추세 우상향)", ma200_slope_up, 1))

    # 4) 단기/중기 골든크로스·데드크로스 (최근 발생 여부)
    gc_5_10 = crossed_above(df["MA5"], df["MA10"], lookback=5)
    dc_5_10 = crossed_below(df["MA5"], df["MA10"], lookback=5)
    checks.append(("최근 5거래일 내 MA5-MA10 골든크로스 발생", gc_5_10, 1))
    checks.append(("최근 5거래일 내 MA5-MA10 데드크로스 발생", dc_5_10, -1))

    gc_10_60 = crossed_above(df["MA10"], df["MA60"], lookback=20)
    dc_10_60 = crossed_below(df["MA10"], df["MA60"], lookback=20)
    checks.append(("최근 20거래일 내 MA10-MA60 골든크로스 발생", gc_10_60, 1))
    checks.append(("최근 20거래일 내 MA10-MA60 데드크로스 발생", dc_10_60, -1))

    # 5) 단기 모멘텀 — 현재가가 단기 이평선 위/아래
    momentum_up = close > ma5 and close > ma10
    momentum_down = close < ma5 and close < ma10
    checks.append(("현재가가 MA5, MA10 모두 위 (단기 모멘텀 양호)", momentum_up, 1))
    checks.append(("현재가가 MA5, MA10 모두 아래 (단기 모멘텀 약화)", momentum_down, -1))

    # 6) 거래량 — 20일 평균 대비 급증 여부와 당일 가격 방향을 함께 확인
    volume = last.get("Volume")
    vma20 = last.get("VMA20")
    volume_ratio = (volume / vma20) if pd.notna(volume) and pd.notna(vma20) and vma20 > 0 else None
    price_up_today = (len(df) > 1) and (close > df["Close"].iloc[-2])
    price_down_today = (len(df) > 1) and (close < df["Close"].iloc[-2])
    volume_spike = volume_ratio is not None and volume_ratio >= 1.5
    volume_dry = volume_ratio is not None and volume_ratio <= 0.5

    checks.append(
        ("거래량 급증(20일 평균 대비 1.5배 이상) + 상승 마감 — 매수세 유입 신호", volume_spike and price_up_today, 1)
    )
    checks.append(
        ("거래량 급증(20일 평균 대비 1.5배 이상) + 하락 마감 — 매도세 출회 신호", volume_spike and price_down_today, -1)
    )
    checks.append(
        ("거래량이 20일 평균의 절반 이하로 급감 — 관심 저조, 다른 신호의 신뢰도가 낮아짐", volume_dry, 0)
    )

    score = sum(pts for _, ok, pts in checks if ok)
    max_score = sum(pts for _, _, pts in checks if pts > 0)

    if score >= 5:
        decision = "매수 고려 (기술적 신호 긍정적)"
    elif score >= 2:
        decision = "관망 / 조건부 (신호 혼조)"
    else:
        decision = "매수 보류·회피 (기술적 신호 부정적)"

    atr = last.get("ATR14")
    atr_pct = (atr / close * 100) if pd.notna(atr) else None
    stop_loss = suggest_stop_loss(close, atr) if pd.notna(atr) else None

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "ma": {"MA5": ma5, "MA10": ma10, "MA60": ma60, "MA200": ma200},
        "checks": checks,
        "score": score,
        "max_score": max_score,
        "decision": decision,
        "atr": atr if pd.notna(atr) else None,
        "atr_pct": atr_pct,
        "stop_loss": stop_loss,
        "volume": volume if pd.notna(volume) else None,
        "vma20": vma20 if pd.notna(vma20) else None,
        "volume_ratio": volume_ratio,
    }


def suggest_stop_loss(close: float, atr: float) -> dict:
    """ATR(변동성) 기반으로 손절 후보선을 배수별로 제시한다.

    배수가 작을수록(1.5x) 타이트하게 끊는 대신 정상적인 변동에도 자주 걸리고,
    배수가 클수록(3x) 여유는 있지만 손실 폭이 커진다. 정답은 없고
    본인의 리스크 허용도에 맞춰 고르는 참고용 기준선이다.
    """
    multipliers = {"보수적 (1.5×ATR)": 1.5, "표준 (2×ATR)": 2.0, "여유있게 (3×ATR)": 3.0}
    return {
        label: {"price": close - m * atr, "pct_below": (m * atr) / close * 100}
        for label, m in multipliers.items()
    }


def simulate_position(
    current_price: float,
    plan: list[dict],
    worst_case_price: float | None = None,
    total_capital: float | None = None,
) -> dict:
    """분할매수 계획을 시뮬레이션해서 누적 평단가/투자금과, 최악 시나리오 손실을 계산한다.

    plan: [{"drop_pct": 하락률(%), "amount": 매수금액}, ...]
          drop_pct=0이면 현재가에서 매수. amount<=0인 행은 무시.
    worst_case_price: 이 가격까지 떨어졌다고 가정했을 때의 평가손실을 계산 (None이면 생략)
    total_capital: 전체 투자 자산 (입력하면 손실이 전체 자산의 몇 %인지도 계산)
    """
    rows = []
    cum_invested = 0.0
    cum_shares = 0.0

    for step in plan:
        amount = step.get("amount", 0) or 0
        if amount <= 0:
            continue
        drop = step.get("drop_pct", 0) or 0
        buy_price = current_price * (1 - drop / 100)
        if buy_price <= 0:
            continue
        shares = amount / buy_price
        cum_invested += amount
        cum_shares += shares
        rows.append(
            {
                "하락률(%)": drop,
                "매수가": buy_price,
                "매수금액": amount,
                "매수수량": shares,
                "누적평단가": cum_invested / cum_shares,
                "누적투자금": cum_invested,
                "누적수량": cum_shares,
            }
        )

    result = {
        "steps": rows,
        "total_invested": cum_invested,
        "total_shares": cum_shares,
        "avg_price": (cum_invested / cum_shares) if cum_shares else None,
    }

    if worst_case_price is not None and cum_shares:
        position_value = cum_shares * worst_case_price
        loss = cum_invested - position_value
        result["worst_case_price"] = worst_case_price
        result["worst_case_loss"] = loss
        result["worst_case_loss_pct"] = (loss / cum_invested * 100) if cum_invested else None
        if total_capital:
            result["worst_case_loss_pct_of_capital"] = loss / total_capital * 100

    return result


def compute_score_series(df: pd.DataFrame) -> pd.DataFrame:
    """analyze()와 같은 규칙을 전체 기간에 대해 벡터 연산으로 계산한다 (백테스트용).

    반환값: df와 같은 인덱스를 가진 DataFrame, 컬럼은 score(int), decision(str).
    MA200이 아직 없는 초반 구간은 NaN으로 비워둔다.
    """
    close, ma5, ma10, ma60, ma200 = (
        df["Close"], df["MA5"], df["MA10"], df["MA60"], df["MA200"],
    )

    def crossed_up_within(fast: pd.Series, slow: pd.Series, lookback: int) -> pd.Series:
        event = (fast.shift(1) < slow.shift(1)) & (fast > slow)
        return event.rolling(window=lookback + 1, min_periods=1).max().fillna(0).astype(bool)

    def crossed_down_within(fast: pd.Series, slow: pd.Series, lookback: int) -> pd.Series:
        event = (fast.shift(1) > slow.shift(1)) & (fast < slow)
        return event.rolling(window=lookback + 1, min_periods=1).max().fillna(0).astype(bool)

    bullish_align = (ma5 > ma10) & (ma10 > ma60) & (ma60 > ma200)
    bearish_align = (ma5 < ma10) & (ma10 < ma60) & (ma60 < ma200)
    price_above_200 = close > ma200
    ma200_slope_up = ma200 > ma200.shift(20)
    gc_5_10 = crossed_up_within(ma5, ma10, 5)
    dc_5_10 = crossed_down_within(ma5, ma10, 5)
    gc_10_60 = crossed_up_within(ma10, ma60, 20)
    dc_10_60 = crossed_down_within(ma10, ma60, 20)
    momentum_up = (close > ma5) & (close > ma10)
    momentum_down = (close < ma5) & (close < ma10)

    volume_ratio = df["Volume"] / df["VMA20"]
    price_up_day = close > close.shift(1)
    price_down_day = close < close.shift(1)
    volume_spike = volume_ratio >= 1.5
    volume_spike_up = volume_spike & price_up_day
    volume_spike_down = volume_spike & price_down_day

    score = (
        bullish_align.astype(int) * 2
        + bearish_align.astype(int) * -2
        + price_above_200.astype(int) * 1
        + (~price_above_200).astype(int) * -1
        + ma200_slope_up.fillna(False).astype(int) * 1
        + gc_5_10.astype(int) * 1
        + dc_5_10.astype(int) * -1
        + gc_10_60.astype(int) * 1
        + dc_10_60.astype(int) * -1
        + momentum_up.astype(int) * 1
        + momentum_down.astype(int) * -1
        + volume_spike_up.fillna(False).astype(int) * 1
        + volume_spike_down.fillna(False).astype(int) * -1
    )

    valid = ma200.notna()
    score = score.where(valid)

    decision = pd.cut(
        score,
        bins=[-100, 1, 4, 100],
        labels=["매수 보류·회피", "관망", "매수 고려"],
    )

    return pd.DataFrame({"score": score, "decision": decision}, index=df.index)


def backtest_forward_returns(df: pd.DataFrame, horizons=(5, 20, 60, 120)) -> dict:
    """과거에 같은 판단(매수 고려/관망/회피)이 나왔을 때, 이후 N거래일 뒤 수익률이

    실제로 어땠는지 집계한다. horizons 단위는 거래일 (5≈1주, 20≈1개월,
    60≈3개월, 120≈6개월).

    반환값: {horizon: DataFrame(판단, 표본수, 평균수익률, 승률, 최악, 최선)}
    표본 기간이 서로 겹치므로(독립 표본 아님) 엄밀한 통계적 유의성보다는
    "과거 이 신호가 나온 뒤 대체로 어떤 흐름이었는지" 참고용 지표다.
    """
    scored = compute_score_series(df)
    close = df["Close"].to_numpy()
    decisions = scored["decision"].to_numpy()
    n = len(df)

    results = {}
    for h in horizons:
        rows = []
        for label in ["매수 고려", "관망", "매수 보류·회피"]:
            idx = np.where(decisions == label)[0]
            idx = idx[idx + h < n]
            if len(idx) == 0:
                rows.append(
                    {"판단": label, "표본수": 0, "평균수익률": None, "승률": None, "최악": None, "최선": None}
                )
                continue
            fwd = close[idx + h] / close[idx] - 1
            rows.append(
                {
                    "판단": label,
                    "표본수": int(len(idx)),
                    "평균수익률": float(fwd.mean()),
                    "승률": float((fwd > 0).mean()),
                    "최악": float(fwd.min()),
                    "최선": float(fwd.max()),
                }
            )
        results[h] = pd.DataFrame(rows)
    return results


def print_report(ticker: str, result: dict) -> None:
    print("=" * 60)
    print(f" 이동평균선 분석 리포트 : {ticker}")
    print("=" * 60)
    print(f"기준일       : {result['date']}")
    print(f"현재가(종가) : {result['close']:,.2f}")
    for name, val in result["ma"].items():
        print(f"{name:<6}       : {val:,.2f}")
    if result.get("volume") is not None and result.get("vma20") is not None:
        print(f"거래량       : {result['volume']:,.0f}  (20일 평균 {result['vma20']:,.0f}, {result['volume_ratio']:.2f}배)")
    print("-" * 60)
    print("체크리스트")
    for desc, ok, pts in result["checks"]:
        mark = "✅" if ok else "➖"
        sign = f"+{pts}" if pts > 0 else f"{pts}"
        print(f" {mark} [{sign:>3}] {desc}")
    print("-" * 60)
    print(f"종합 점수 : {result['score']} / 최대 {result['max_score']}")
    print(f"판단      : {result['decision']}")
    print("-" * 60)
    if result.get("stop_loss"):
        print(f"변동성(ATR14) : {result['atr']:,.2f}  (현재가의 {result['atr_pct']:.1f}%)")
        print("ATR 기반 손절선 후보 (정답은 없음, 리스크 허용도에 맞게 선택)")
        for label, info in result["stop_loss"].items():
            print(f"  {label:<16} : {info['price']:,.2f}  (현재가 대비 -{info['pct_below']:.1f}%)")
    else:
        print("변동성(ATR) 계산에 필요한 데이터가 부족합니다.")
    print("=" * 60)
    print(
        "※ 본 결과는 5/10/60/200일 이동평균선 규칙만으로 계산한 참고용 기술적 신호이며,\n"
        "  투자 자문이 아닙니다. 거래량, 재무상태, 시장 상황 등을 함께 고려하시고\n"
        "  투자 판단과 책임은 본인에게 있습니다."
    )


def add_interactivity(fig, ax) -> None:
    """차트 창에서 마우스 휠로 확대/축소하고, 클릭한 지점의 날짜·가격을 표시한다.

    - 왼쪽 클릭: 클릭 지점의 날짜/가격을 말풍선으로 표시
    - 오른쪽 클릭: 말풍선 지우기
    - 마우스 휠: 커서 위치를 기준으로 확대/축소 (드래그 박스 확대는 툴바의 돋보기 아이콘으로도 가능)
    """
    import matplotlib.dates as mdates

    annotation = ax.annotate(
        "",
        xy=(0, 0),
        xytext=(15, 15),
        textcoords="offset points",
        bbox=dict(boxstyle="round,pad=0.4", fc="#ffffcc", ec="gray", alpha=0.95),
        arrowprops=dict(arrowstyle="->"),
        fontsize=10,
        visible=False,
        zorder=10,
    )

    def on_click(event):
        if event.inaxes != ax or event.xdata is None or event.ydata is None:
            return
        if event.button == 3:  # 오른쪽 클릭 -> 말풍선 숨기기
            annotation.set_visible(False)
            fig.canvas.draw_idle()
            return
        date_str = mdates.num2date(event.xdata).strftime("%Y-%m-%d")
        annotation.xy = (event.xdata, event.ydata)
        annotation.set_text(f"{date_str}\n{event.ydata:,.2f}")
        annotation.set_visible(True)
        fig.canvas.draw_idle()

    def on_scroll(event):
        if event.inaxes != ax or event.xdata is None or event.ydata is None:
            return
        base_scale = 1.2
        scale = 1 / base_scale if event.button == "up" else base_scale
        xlim, ylim = ax.get_xlim(), ax.get_ylim()
        xdata, ydata = event.xdata, event.ydata
        new_w = (xlim[1] - xlim[0]) * scale
        new_h = (ylim[1] - ylim[0]) * scale
        relx = (xlim[1] - xdata) / (xlim[1] - xlim[0])
        rely = (ylim[1] - ydata) / (ylim[1] - ylim[0])
        ax.set_xlim(xdata - new_w * (1 - relx), xdata + new_w * relx)
        ax.set_ylim(ydata - new_h * (1 - rely), ydata + new_h * rely)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("button_press_event", on_click)
    fig.canvas.mpl_connect("scroll_event", on_scroll)


def plot_chart(ticker: str, df: pd.DataFrame, result: dict, out_path: str, show: bool) -> None:
    import matplotlib
    import matplotlib.font_manager as fm

    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Windows 기본 폰트(DejaVu Sans)에는 한글 글리프가 없어 깨져 보이므로
    # 시스템에 있는 한글 폰트로 교체 (없으면 기본값 유지)
    for font_name in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
        if font_name in {f.name for f in fm.fontManager.ttflist}:
            plt.rcParams["font.family"] = font_name
            break
    plt.rcParams["axes.unicode_minus"] = False

    plot_df = df.tail(300)  # 최근 300거래일만 표시 (MA200 보이도록)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(plot_df.index, plot_df["Close"], label="종가", color="black", linewidth=1.2)
    colors = {"MA5": "#e74c3c", "MA10": "#f39c12", "MA60": "#2ecc71", "MA200": "#3498db"}
    for w in MA_WINDOWS:
        col = f"MA{w}"
        ax.plot(plot_df.index, plot_df[col], label=col, color=colors[col], linewidth=1.3)

    ax.set_title(f"{ticker}  —  {result['decision']}  (점수 {result['score']}/{result['max_score']})")
    ax.set_xlabel("날짜")
    ax.set_ylabel("가격")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"\n차트 이미지 저장됨 -> {out_path}")

    if show:
        add_interactivity(fig, ax)
        print(
            "차트 창 사용법: 마우스 휠로 확대/축소, 왼쪽 클릭으로 지점의 날짜·가격 표시, "
            "오른쪽 클릭으로 표시 지우기 (툴바 돋보기 아이콘으로 드래그 확대도 가능)"
        )
        plt.show()
    plt.close(fig)


def print_backtest(ticker: str, df: pd.DataFrame) -> None:
    horizon_labels = {5: "1주", 20: "1개월", 60: "3개월", 120: "6개월"}
    results = backtest_forward_returns(df)

    print("=" * 60)
    print(f" 백테스트 : {ticker} — 과거 이 판단, 실제로 맞았을까?")
    print(f" (조회 기간: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 60)
    for h, table in results.items():
        print(f"\n[{horizon_labels.get(h, f'{h}거래일')} 뒤 결과]")
        for _, row in table.iterrows():
            if row["표본수"] == 0:
                print(f"  {row['판단']:<12} 표본 없음")
                continue
            print(
                f"  {row['판단']:<12} 표본 {row['표본수']:>4}회 | "
                f"평균 {row['평균수익률']*100:+6.1f}% | 승률 {row['승률']*100:5.1f}% | "
                f"최악 {row['최악']*100:+6.1f}% | 최선 {row['최선']*100:+6.1f}%"
            )
    print("-" * 60)
    print(
        "※ 표본 기간이 서로 겹쳐 통계적으로 독립적이지 않으므로 참고용입니다.\n"
        "  과거 성과가 미래 수익을 보장하지 않습니다."
    )


def main():
    parser = argparse.ArgumentParser(description="이동평균선 기반 매수 신호 분석기")
    parser.add_argument("ticker", help="종목코드 또는 티커 (예: 005930, AAPL, TSLA)")
    parser.add_argument("--start", default=None, help="조회 시작일 YYYY-MM-DD (기본: 2년 전)")
    parser.add_argument("--out", default=None, help="차트 이미지 저장 경로 (기본: <ticker>_ma_chart.png)")
    parser.add_argument("--show", action="store_true", help="차트 창을 화면에 직접 띄우기")
    parser.add_argument("--backtest", action="store_true", help="과거 신호별 이후 수익률 백테스트 결과도 출력")
    args = parser.parse_args()

    try:
        df = fetch_data(args.ticker, args.start)
        result = analyze(df)
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

    print_report(args.ticker, result)

    if args.backtest:
        print()
        print_backtest(args.ticker, df)

    out_path = args.out or f"{args.ticker}_ma_chart.png"
    plot_chart(args.ticker, df, result, out_path, args.show)


if __name__ == "__main__":
    main()
