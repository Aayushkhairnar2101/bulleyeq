"""One confirmation-based volume breakout strategy for closed candles."""
import pandas as pd


def calculate(frame: pd.DataFrame, fast=9, slow=21, trend=50, rsi_period=14, atr_period=14,
              breakout_period=20, volume_period=20, vwap_period=20):
    required={"open","high","low","close"}
    if not required.issubset(frame.columns): raise ValueError("OHLC candles are required")
    out=frame.copy()
    close=out.close.astype(float)
    out["ema_fast"]=close.ewm(span=fast,adjust=False).mean()
    out["ema_slow"]=close.ewm(span=slow,adjust=False).mean()
    out["sma_trend"]=close.rolling(trend,min_periods=trend).mean()
    delta=close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/rsi_period,adjust=False,min_periods=rsi_period).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/rsi_period,adjust=False,min_periods=rsi_period).mean()
    rs=gain/loss.replace(0,float("nan")); out["rsi"]=100-(100/(1+rs))
    tr=pd.concat([out.high-out.low,(out.high-close.shift()).abs(),(out.low-close.shift()).abs()],axis=1).max(axis=1)
    out["atr"]=tr.ewm(alpha=1/atr_period,adjust=False,min_periods=atr_period).mean()
    macd=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean()
    out["macd"]=macd
    out["macd_signal"]=macd.ewm(span=9,adjust=False).mean()
    # Use only prior candles for the breakout boundary and volume baseline.
    out["breakout_high"]=out.high.shift(1).rolling(breakout_period,min_periods=breakout_period).max()
    out["breakout_low"]=out.low.shift(1).rolling(breakout_period,min_periods=breakout_period).min()
    if "volume" not in out and "v" in out: out["volume"]=out["v"]
    if "volume" in out:
        volume=pd.to_numeric(out.volume,errors="coerce").fillna(0).astype(float)
        average=volume.shift(1).rolling(volume_period,min_periods=volume_period).mean()
        out["relative_volume"]=volume/average.where(average>0)
        typical=(out.high.astype(float)+out.low.astype(float)+close)/3
        vol_sum=volume.rolling(vwap_period,min_periods=vwap_period).sum()
        out["vwap"]=(typical*volume).rolling(vwap_period,min_periods=vwap_period).sum()/vol_sum.where(vol_sum>0)
    return out


def signal(frame, config):
    fast=config.get("fast_ema",9); slow=config.get("slow_ema",21); trend=config.get("trend_sma",50)
    rsi_period=config.get("rsi_period",14); atr_period=config.get("atr_period",14)
    breakout_period=config.get("breakout_period",20); volume_period=config.get("volume_period",20); vwap_period=config.get("vwap_period",20)
    needed=max(slow,trend,rsi_period,atr_period,breakout_period+1,volume_period+1,vwap_period)+2
    if len(frame)<needed: return {"signal":"NONE","reason":f"Need at least {needed} valid candles.","indicators":{}}
    values=calculate(frame,fast,slow,trend,rsi_period,atr_period,breakout_period,volume_period,vwap_period)
    cur=values.iloc[-1]
    fields=["ema_fast","ema_slow","sma_trend","rsi","atr","macd","macd_signal","breakout_high","breakout_low"]
    if any(pd.isna(cur[x]) for x in fields): return {"signal":"NONE","reason":"Indicator warmup is incomplete.","indicators":{}}
    if "relative_volume" not in values or pd.isna(cur.get("relative_volume")) or pd.isna(cur.get("vwap")):
        return {"signal":"NONE","reason":"Volume or VWAP data is unavailable; entries are disabled.","indicators":{}}
    values_out={x:float(cur[x]) for x in fields+(["relative_volume","vwap"])}|{"close":float(cur.close),"volume":float(cur.volume)}
    result={"signal":"NONE","reason":"No confirmed volume breakout.","indicators":values_out}
    volume_ok=cur.relative_volume>=config.get("min_relative_volume",1.5)
    long_break=cur.close>cur.breakout_high
    short_break=cur.close<cur.breakout_low
    rsi_long=config.get("rsi_long",52); rsi_short=config.get("rsi_short",48)
    if long_break:
        checks=[volume_ok,cur.close>cur.vwap,cur.ema_fast>cur.ema_slow,cur.close>cur.sma_trend,
                cur.macd>cur.macd_signal,rsi_long<cur.rsi<75,cur.atr>0]
        if all(checks): result.update(signal="LONG",reason="Upside breakout confirmed by relative volume, VWAP, EMA trend, MACD, RSI and ATR.")
        else: result["reason"]="LONG breakout rejected: volume, VWAP, trend, MACD, RSI or ATR confirmation failed."
    elif short_break:
        checks=[volume_ok,cur.close<cur.vwap,cur.ema_fast<cur.ema_slow,cur.close<cur.sma_trend,
                cur.macd<cur.macd_signal,25<cur.rsi<rsi_short,cur.atr>0]
        if all(checks): result.update(signal="SHORT",reason="Downside breakout confirmed by relative volume, VWAP, EMA trend, MACD, RSI and ATR.")
        else: result["reason"]="SHORT breakout rejected: volume, VWAP, trend, MACD, RSI or ATR confirmation failed."
    return result
