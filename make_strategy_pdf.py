from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


OUT_PATH = "A_strategy_summary.pdf"
FONT_PATH = r"C:\Windows\Fonts\malgun.ttf"
FONT_NAME = "Malgun"


def draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, line_h: float) -> float:
    words = text.split(" ")
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if c.stringWidth(test, FONT_NAME, 10) <= max_width:
            line = test
        else:
            c.drawString(x, y, line)
            y -= line_h
            line = w
    if line:
        c.drawString(x, y, line)
        y -= line_h
    return y


def main() -> None:
    pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
    c = canvas.Canvas(OUT_PATH, pagesize=A4)
    width, height = A4

    x = 18 * mm
    y = height - 20 * mm
    max_w = width - 36 * mm

    c.setFont(FONT_NAME, 16)
    c.drawString(x, y, "EMA200-ADX-ATR-1MIN 최종 전략 정리")
    y -= 10 * mm

    c.setFont(FONT_NAME, 10)
    sections = [
        ("1) 핵심 요약", [
            "타임프레임: 1분봉 마감 캔들 기준",
            "지표: EMA200, ATR14(Wilder), ADX14(Wilder), ATR MA30",
            "진입: 기준캔들(Basis) + 확인캔들(Confirm) 2단계",
            "사이징: 투입 마진 1% 방식 (equity의 1%)",
            "보유중 코인: 추가 감지/진입 알림 차단",
        ]),
        ("2) 진입 조건", [
            "Long Basis: close >= ema + atr_used * EMA_ATR_OFFSET_MULT",
            "Short Basis: close <= ema - atr_used * EMA_ATR_OFFSET_MULT",
            "필터: adx > ADX_MIN, atr_used 보정(min(atr, atr_ma30 * ATR_SPIKE_CAP_MULT))",
            "확인: basis 이후 CONFIRM_WITHIN_BARS 이내에 long은 close > basis_close, short은 close < basis_close",
        ]),
        ("3) 알림 정책", [
            "진입감지: 👀📌 진입 감지",
            "확인만료: ⛔ 진입 스킵",
            "실제주문성공: 🟢📈 / 🔴📉 진입",
            "보유 포지션 코인은 감지/스킵/재진입 알림 비활성",
        ]),
        ("4) 포지션 사이징", [
            "margin_usdt = get_account_equity_usdt() * POSITION_RISK_PCT",
            "레버리지 세팅 후 qty = calculate_quantity(symbol, margin_usdt, mark_price, leverage)",
            "의도: 잔고의 1%만 투입 마진으로 사용",
        ]),
        ("5) 손절/청산", [
            "초기 SL: min(기준봉저가-BASIS×ATR, 진입가-ENTRY_MIN×ATR) 롱 / 숏은 max 대칭",
            "트레일: long은 highest 기반 상향, short은 lowest 기반 하향",
            "청산: stop_price 터치 시 시장가 청산",
            "현재 서버 STOP 주문은 기본 OFF (계정 호환 이슈 -4120 회피)",
        ]),
        ("6) 현재 반영된 주요 변경", [
            "1) 투입 마진 1% 방식으로 사이징 변경",
            "2) ATR_MIN_BY_SYMBOL 제거 (모든 코인 동일 처리)",
            "3) 보유 코인에 대한 감지/재진입 알림 차단",
            "4) 서버 STOP 주문 기본 비활성화로 운영 안정화",
        ]),
    ]

    for title, bullets in sections:
        c.setFont(FONT_NAME, 12)
        c.drawString(x, y, title)
        y -= 6 * mm
        c.setFont(FONT_NAME, 10)
        for b in bullets:
            y = draw_wrapped(c, f"- {b}", x, y, max_w, 5.2 * mm)
            if y < 25 * mm:
                c.showPage()
                c.setFont(FONT_NAME, 10)
                y = height - 20 * mm
        y -= 2 * mm

    c.save()


if __name__ == "__main__":
    main()
