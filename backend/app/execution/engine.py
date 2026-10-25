"""Single-symbol, testnet-only volume-breakout strategy worker."""
import asyncio
import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from uuid import uuid4

import httpx
import pandas as pd

from ..database import connect, event, read_setting, write_setting
from ..exchange.delta_client import DeltaClient, DeltaError
from ..risk.risk_manager import assess
from ..strategy.indicators import signal

class WorkerBlocked(RuntimeError):
    pass

def _result(response):
    return response.get("result", response)

def _rows(response):
    value=_result(response)
    return value if isinstance(value,list) else value.get("data",[]) if isinstance(value,dict) else []

def _interval_seconds(resolution):
    units={"m":60,"h":3600,"d":86400}
    try: return int(resolution[:-1])*units[resolution[-1]]
    except (ValueError,KeyError,IndexError): raise WorkerBlocked("Unsupported strategy timeframe")

def _round_to_tick(price:Decimal,tick:Decimal,round_up:bool):
    mode=ROUND_CEILING if round_up else ROUND_FLOOR
    return (price/tick).to_integral_value(rounding=mode)*tick

def _exit_prices(reference:Decimal, side:str, distance:Decimal, target_distance:Decimal, tick:Decimal):
    if side=="buy": return _round_to_tick(reference-distance,tick,False),_round_to_tick(reference+target_distance,tick,True)
    return _round_to_tick(reference+distance,tick,True),_round_to_tick(reference-target_distance,tick,False)

def _save_decision(product, result, candle_time):
    with connect() as db:
        db.execute("INSERT INTO decisions(at,product,signal,reason,indicators) VALUES(?,?,?,?,?)",(datetime.now(timezone.utc).isoformat(),product,result["signal"],result["reason"],json.dumps(result["indicators"])))
    write_setting(f"last_bar:{product}",candle_time)

async def strategy_cycle():
    cfg=read_setting("strategy"); risk_cfg=read_setting("risk"); client=DeltaClient()
    try:
        if not client.settings.delta_testnet_api_key or not client.settings.delta_testnet_api_secret:
            raise WorkerBlocked("Testnet API credentials are not configured")
        # The wallet meta.net_equity is Delta's combined equity measure. No
        # strategy entry is allowed if it is missing or non-positive.
        wallet=await client.request("GET","/v2/wallet/balances",private=True)
        write_setting("exchange_check",{"connected":True,"checked_at":datetime.now(timezone.utc).isoformat(),"message":"Testnet worker account sync succeeded"})
        meta=wallet.get("meta",{})
        try: equity=float(meta["net_equity"])
        except (KeyError,TypeError,ValueError): raise WorkerBlocked("Delta did not provide net_equity; daily risk cannot be enforced")
        if not math.isfinite(equity) or equity<=0: raise WorkerBlocked("Delta account equity is not positive")
        risk=assess(equity)
        if risk["locked"]:
            write_setting("bot_state","paused")
            event("risk_lock",f"Daily {risk['locked']} limit reached; strategy paused")
            return {"action":"paused","reason":f"Daily {risk['locked']} limit reached","risk":risk}

        balances=wallet.get("result",[])
        if not isinstance(balances,list) or not any(float(b.get("available_balance",0) or 0)>0 for b in balances):
            raise WorkerBlocked("No positive available testnet balance was returned")
        candles=_rows(await client.candles(cfg["product"],cfg["resolution"],160))
        frame=pd.DataFrame(candles).rename(columns={"o":"open","h":"high","l":"low","c":"close"})
        if not {"time","open","high","low","close"}.issubset(frame.columns):
            raise WorkerBlocked("Candle response is missing timestamp or OHLC fields")
        frame=frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
        frame["time"]=pd.to_numeric(frame["time"],errors="coerce")
        if frame.time.dropna().empty: raise WorkerBlocked("Candle timestamps are invalid")
        if frame.time.max()>10**12: frame["time"]=frame.time/1000
        interval=_interval_seconds(cfg["resolution"]); now=time.time()
        frame=frame.dropna(subset=["time"])
        confirmed=frame[frame.time+interval<=now]
        if len(confirmed)<max(cfg["slow_ema"],cfg["trend_sma"],cfg["rsi_period"],cfg["atr_period"])+2:
            raise WorkerBlocked("Not enough confirmed candles to evaluate the configured indicators")
        last=confirmed.iloc[-1]; candle_time=str(int(last.time))
        if now-(float(last.time)+interval)>interval*2:
            raise WorkerBlocked("Market candles are stale; no new entry is allowed")
        if read_setting(f"last_bar:{cfg['product']}")==candle_time:
            return {"action":"waiting","reason":"This confirmed candle has already been evaluated"}
        confirmed=confirmed.drop(columns=["time"])
        result=signal(confirmed,cfg)
        _save_decision(cfg["product"],result,candle_time)
        event("strategy",f"{cfg['product']} {result['signal']}: {result['reason']}")
        if result["signal"] not in {"LONG","SHORT"}:
            return {"action":"no_entry","reason":result["reason"],"signal":result}

        products=_rows(await client.products())
        product=next((p for p in products if p.get("symbol")==cfg["product"]),None)
        if not product or product.get("contract_type")!="perpetual_futures" or product.get("state")!="live" or product.get("trading_status") not in (None,"operational"):
            raise WorkerBlocked("Selected product is not an operational perpetual contract")
        ticker=_result(await client.ticker(cfg["product"]))
        mark=Decimal(str(ticker.get("mark_price") or ticker.get("close") or 0))
        atr=Decimal(str(result["indicators"].get("atr") or 0))
        tick=Decimal(str(product.get("tick_size") or 0))
        if mark<=0 or atr<=0 or tick<=0: raise WorkerBlocked("Valid mark price, ATR, or product tick size is unavailable")
        if float(risk_cfg["max_position_size"])<1: raise WorkerBlocked("Configured maximum position size is below one contract")
        distance=atr*Decimal(str(cfg.get("atr_stop_multiplier",2.0)))
        target_distance=distance*Decimal(str(cfg.get("risk_reward",2.0)))
        side="buy" if result["signal"]=="LONG" else "sell"
        stop,target=_exit_prices(mark,side,distance,target_distance,tick)
        if stop<=0 or target<=0: raise WorkerBlocked("Calculated protective exit price is invalid")

        positions=_rows(await client.positions())
        if any(int(p.get("size",0) or 0)!=0 for p in positions):
            return {"action":"blocked","reason":"An account position is already open; no additional position was submitted"}
        open_orders=_rows(await client.orders())
        if any(o.get("state") in {"open","pending"} for o in open_orders):
            return {"action":"blocked","reason":"An order is awaiting exchange reconciliation"}

        client_id=uuid4().hex
        order={"product_id":product["id"],"product_symbol":cfg["product"],"order_type":"market_order","size":1,"side":side,"client_order_id":client_id,"bracket_stop_loss_price":str(stop),"bracket_take_profit_price":str(target),"bracket_stop_trigger_method":"mark_price","reduce_only":False}
        created=datetime.now(timezone.utc).isoformat()
        with connect() as db:
            db.execute("INSERT INTO orders(id,client_id,product,side,size,state,created_at,payload) VALUES(?,?,?,?,?,?,?,?)",("pending-"+client_id,client_id,cfg["product"],side,1,"submitting",created,json.dumps(order)))
        try: response=await client.create_order(order)
        except (httpx.TimeoutException,httpx.NetworkError):
            try: response=await client.order_by_client_id(client_id)
            except Exception:
                with connect() as db: db.execute("UPDATE orders SET state='submission_unknown' WHERE client_id=?",(client_id,))
                raise WorkerBlocked("Order timed out and reconciliation failed. The bot paused; do not retry until the order is checked by client ID.")
        exchange_order=_result(response)
        if not isinstance(exchange_order,dict) or not exchange_order.get("id"):
            with connect() as db: db.execute("UPDATE orders SET state='submission_unknown' WHERE client_id=?",(client_id,))
            raise WorkerBlocked("Exchange did not confirm an order ID; bot paused for reconciliation")
        fill_price=Decimal(str(exchange_order.get("average_fill_price") or 0))
        if fill_price<=0:
            with connect() as db: db.execute("UPDATE orders SET id=?,state=?,payload=? WHERE client_id=?",(str(exchange_order["id"]),str(exchange_order.get("state","unknown")),json.dumps(exchange_order),client_id))
            write_setting("bot_state","paused")
            event("critical",f"Order {exchange_order['id']} accepted without a confirmed average fill price; strategy paused for reconciliation")
            return {"action":"paused","reason":"Exchange accepted the order but has not reported a fill price. Strategy paused; inspect open orders and position state before resuming.","order":exchange_order}
        # Delta accepts bracket edits against the created order. Re-anchor ATR
        # exits to the actual average fill before claiming protection.
        stop,target=_exit_prices(fill_price,side,distance,target_distance,tick)
        try:
            await client.update_bracket(exchange_order["id"],product["id"],cfg["product"],stop,target)
            exchange_order=_result(await client.order_by_client_id(client_id))
        except Exception as exc:
            write_setting("bot_state","paused")
            event("critical",f"Position order accepted but fill-anchored bracket update failed: {str(exc)[:250]}")
            return {"action":"paused","reason":"Order accepted; bracket update from actual fill failed. Inspect the Delta Demo position immediately.","order":exchange_order}
        state=str(exchange_order.get("state","unknown"))
        with connect() as db:
            db.execute("UPDATE orders SET id=?,state=?,payload=? WHERE client_id=?",(str(exchange_order["id"]),state,json.dumps(exchange_order),client_id))
        exits_confirmed=(Decimal(str(exchange_order.get("bracket_stop_loss_price",0) or 0))==stop and Decimal(str(exchange_order.get("bracket_take_profit_price",0) or 0))==target)
        event("order",f"Automatic testnet {side} order {exchange_order['id']} for {cfg['product']} accepted; protective exits {'confirmed' if exits_confirmed else 'not confirmed'}")
        if not exits_confirmed:
            write_setting("bot_state","paused")
            event("critical","Exchange did not confirm both attached exits; strategy paused immediately")
            return {"action":"paused","reason":"Order accepted but attached protective exits were not confirmed. Inspect the Delta Demo account immediately.","order":exchange_order}
        return {"action":"order_submitted","order":exchange_order,"stop_loss":str(stop),"take_profit":str(target)}
    finally:
        await client.close()

async def run_worker(app):
    owner=uuid4().hex
    if not _claim_lease(owner):
        write_setting("bot_state","paused")
        event("worker","Another worker holds the execution lease; bot paused")
        return
    try:
        while read_setting("bot_state")=="running":
            try:
                result=await strategy_cycle()
                if result.get("action") in {"paused"}: continue
            except Exception as exc:
                write_setting("bot_state","paused")
                event("worker_error",str(exc)[:400])
                continue
            _renew_lease(owner)
            await asyncio.sleep(10)
    finally:
        _release_lease(owner)

def _claim_lease(owner):
    now=time.time()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT owner,expires_at FROM worker_lease WHERE id=1").fetchone()
        if row and row[1]>now and row[0]!=owner:
            db.rollback(); return False
        db.execute("INSERT INTO worker_lease(id,owner,expires_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at",(owner,now+45))
        return True

def _renew_lease(owner):
    with connect() as db: db.execute("UPDATE worker_lease SET expires_at=? WHERE id=1 AND owner=?",(time.time()+45,owner))

def _release_lease(owner):
    with connect() as db: db.execute("DELETE FROM worker_lease WHERE id=1 AND owner=?",(owner,))
