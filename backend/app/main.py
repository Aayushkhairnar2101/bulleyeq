from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .config import get_settings
from .database import init_db, read_setting, write_setting, event, connect
from .exchange.delta_client import DeltaClient, DeltaError
from .risk.risk_manager import assess
from .strategy.indicators import signal
from .execution.engine import run_worker

@asynccontextmanager
async def lifespan(app):
    init_db()
    if read_setting("bot_state")=="running":
        write_setting("bot_state","stopped")
        event("bot_recovery","Server restarted; automatic trading remains stopped until manually restarted")
    app.state.worker_task=None
    yield
    task=app.state.worker_task
    if task and not task.done():
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

app=FastAPI(title="BullEyeQ",version="1.0.0",description="Delta Exchange India testnet trading terminal",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173","http://127.0.0.1:5174","http://localhost:5174"],allow_methods=["GET","POST","PUT"],allow_headers=["Authorization","Content-Type"])
client_factory=DeltaClient

def control_auth(authorization):
    settings=get_settings(); token=settings.app_auth_token
    if token=="change-me-before-enabling-controls" or not authorization or authorization!=f"Bearer {token}": raise HTTPException(401,"Set a private APP_AUTH_TOKEN and provide it as a bearer token")

def payload(result): return result.get("result",result)

@app.get("/api/health")
def health(): return {"ok":True,"environment":"TESTNET","time":datetime.now(timezone.utc).isoformat()}

@app.get("/api/exchange/status")
def exchange_status():
    s=get_settings(); configured=bool(s.delta_testnet_api_key and s.delta_testnet_api_secret)
    check=read_setting("exchange_check") or {}
    checked_at=check.get("checked_at")
    fresh=False
    if checked_at:
        try: fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(checked_at)).total_seconds()<=120
        except ValueError: fresh=False
    connected=bool(configured and check.get("connected") and fresh)
    message=check.get("message") if fresh else ("Last connection check is stale" if checked_at else ("Connection has not been checked" if configured else "Testnet credentials are not configured"))
    return {"environment":"TESTNET","base_url":s.delta_testnet_base_url,"credentials_configured":configured,"api_key_masked":(s.delta_testnet_api_key[:4]+"••••") if configured else None,"connected":connected,"checked_at":checked_at,"message":message}

@app.post("/api/exchange/test-connection")
async def test_connection(authorization:str|None=Header(default=None)):
    control_auth(authorization); c=client_factory()
    try:
        result=await c.balance(); checked=datetime.now(timezone.utc).isoformat(); write_setting("exchange_check",{"connected":True,"checked_at":checked,"message":"Testnet account request succeeded"}); event("exchange","Testnet account request succeeded")
        return {"connected":True,"environment":"TESTNET","message":"Testnet account request succeeded"}
    except DeltaError as e:
        checked=datetime.now(timezone.utc).isoformat(); write_setting("exchange_check",{"connected":False,"checked_at":checked,"message":str(e)}); event("exchange_error",str(e)); raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/market/products")
async def products():
    c=client_factory()
    try: return payload(await c.products())
    except DeltaError as e: raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/market/candles")
async def candles(symbol:str="BTCUSD",resolution:str="5m",limit:int=120):
    if not 1<=limit<=500: raise HTTPException(422,"limit must be between 1 and 500")
    c=client_factory()
    try: return payload(await c.candles(symbol,resolution,limit))
    except DeltaError as e: raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/market/ticker")
async def ticker(symbol:str="BTCUSD"):
    c=client_factory()
    try: return payload(await c.ticker(symbol))
    except DeltaError as e: raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/strategy/signal")
async def strategy_signal():
    cfg=read_setting("strategy"); c=client_factory()
    try:
        raw=payload(await c.candles(cfg["product"],cfg["resolution"],120))
        import pandas as pd
        frame=pd.DataFrame(raw)
        rename={"o":"open","h":"high","l":"low","c":"close"}; frame=frame.rename(columns=rename)
        result=signal(frame,cfg)
        from datetime import datetime, timezone
        with connect() as db:
            db.execute("INSERT INTO decisions(at,product,signal,reason,indicators) VALUES(?,?,?,?,?)",(datetime.now(timezone.utc).isoformat(),cfg["product"],result["signal"],result["reason"],__import__("json").dumps(result["indicators"])))
        return result
    except DeltaError as e: return {"signal":"NONE","reason":f"Market data unavailable: {e}","indicators":{}}
    finally: await c.close()

@app.get("/api/strategy/config")
def strategy_config(): return read_setting("strategy")
@app.put("/api/strategy/config")
def set_strategy(body:dict,authorization:str|None=Header(default=None)):
    control_auth(authorization)
    allowed={"product","resolution","fast_ema","slow_ema","trend_sma","rsi_period","rsi_long","rsi_short","atr_period","breakout_period","volume_period","vwap_period","min_relative_volume","atr_stop_multiplier","risk_reward"}
    if set(body)!=allowed: raise HTTPException(422,"Provide every strategy setting")
    integer_fields=allowed-{"product","resolution","atr_stop_multiplier","risk_reward","min_relative_volume"}
    numeric_fields=("atr_stop_multiplier","risk_reward","min_relative_volume")
    if not all(isinstance(body[k],int) and body[k]>0 for k in integer_fields) or not all(isinstance(body[k],(int,float)) and body[k]>0 for k in numeric_fields) or body["fast_ema"]>=body["slow_ema"] or not 0<=body["rsi_short"]<100 or not 0<body["rsi_long"]<=100 or body["rsi_short"]>=body["rsi_long"]: raise HTTPException(422,"Invalid indicator parameters")
    write_setting("strategy",body); event("config","Strategy configuration updated"); return body

@app.get("/api/risk/config")
def risk_config(): return read_setting("risk")
@app.put("/api/risk/config")
def set_risk(body:dict,authorization:str|None=Header(default=None)):
    control_auth(authorization)
    if set(body)!={"daily_loss_pct","daily_profit_pct","max_position_size","max_positions","enabled"}: raise HTTPException(422,"Provide all risk settings")
    if not (0<body["daily_loss_pct"]<=100 and 0<body["daily_profit_pct"]<=100 and body["max_position_size"]>0 and 1<=body["max_positions"]<=10): raise HTTPException(422,"Risk values are outside permitted bounds")
    write_setting("risk",body); event("risk_config","Risk configuration updated"); return body

@app.get("/api/risk/status")
def risk_status(): return read_setting("risk_status") or {"available":False,"status":"unavailable","message":"Account equity must be loaded from Delta testnet before the daily risk baseline can be calculated."}
@app.get("/api/bot/status")
def bot_status():
    state=read_setting("bot_state")
    task=app.state.worker_task
    return {"state":state,"mode":"testnet","worker":"running" if task and not task.done() else "not_running","entries_enabled":state=="running"}
@app.post("/api/bot/{action}")
async def bot_control(action:str,authorization:str|None=Header(default=None)):
    control_auth(authorization)
    if action not in {"start","pause","resume","stop","kill-switch"}: raise HTTPException(404,"Unknown bot action")
    if action in {"start","resume"}:
        settings=get_settings()
        if not settings.delta_testnet_api_key or not settings.delta_testnet_api_secret: raise HTTPException(503,"Configure a valid Delta India testnet key with Read Data and Trading permissions first")
        write_setting("bot_state","running")
        if not app.state.worker_task or app.state.worker_task.done(): app.state.worker_task=asyncio.create_task(run_worker(app))
        event("bot","Automatic testnet strategy started by operator")
        return {"state":"running","mode":"TESTNET","message":"Strategy worker started. It waits for a confirmed candle signal and passes all risk checks before any order."}
    state="stopped" if action in {"stop","kill-switch"} else "paused"
    write_setting("bot_state",state); event("bot",f"Bot {state} by operator"); return {"state":state,"mode":"TESTNET"}

@app.get("/api/system/events")
def events():
    with connect() as db: return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100").fetchall()]

@app.get("/api/strategy/decisions")
def decisions():
    with connect() as db: return [dict(r) for r in db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 100").fetchall()]

@app.get("/api/positions")
async def positions():
    c=client_factory()
    try: return payload(await c.positions())
    except DeltaError as e: raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/orders")
async def orders():
    c=client_factory()
    try: return payload(await c.orders())
    except DeltaError as e: raise HTTPException(502,str(e))
    finally: await c.close()

@app.get("/api/trades")
def trades(): return []
@app.get("/api/performance")
def performance(): return {"available":False,"message":"Performance metrics will appear after actual fills have been recorded."}

@app.get("/api/account/summary")
async def account_summary():
    s=get_settings()
    if not s.delta_testnet_api_key: return {"connected":False,"equity":None,"available_balance":None,"message":"Configure testnet credentials to load account data"}
    c=client_factory()
    try: return {"connected":True,"balances":payload(await c.balance()),"positions":payload(await c.positions()),"orders":payload(await c.orders())}
    except DeltaError as e: return {"connected":False,"message":str(e)}
    finally: await c.close()
