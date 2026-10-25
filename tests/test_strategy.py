import pandas as pd
from backend.app.strategy.indicators import calculate, signal

CONFIG={"fast_ema":9,"slow_ema":21,"trend_sma":50,"rsi_period":14,"rsi_long":50,"rsi_short":50,"atr_period":14}

def test_indicator_columns_and_warmup():
    close=list(range(1,61))
    frame=pd.DataFrame({"open":close,"high":[x+1 for x in close],"low":[x-1 for x in close],"close":close})
    out=calculate(frame)
    assert {"ema_fast","ema_slow","sma_trend","rsi","atr"}.issubset(out.columns)
    assert pd.isna(out.sma_trend.iloc[48])
    assert out.sma_trend.iloc[49]==25.5

def test_signal_rejects_insufficient_candles():
    frame=pd.DataFrame({k:list(range(1,20)) for k in ("open","high","low","close")})
    result=signal(frame,CONFIG)
    assert result["signal"]=="NONE"
    assert "at least" in result["reason"]
