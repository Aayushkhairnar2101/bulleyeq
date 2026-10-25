"""Delta India testnet v2 REST client. No production endpoint fallback exists."""
import hashlib, hmac, json, time
from urllib.parse import urlencode
import httpx
from ..config import get_settings, TESTNET_BASE

class DeltaError(RuntimeError): pass

class DeltaClient:
    def __init__(self, transport=None):
        self.settings = get_settings()
        self.base = TESTNET_BASE
        self.client = httpx.AsyncClient(base_url=self.base, timeout=10, transport=transport, headers={"User-Agent":"BullEyeQ/1.0","Accept":"application/json"})

    def _headers(self, method, path, query="", body=""):
        key, secret = self.settings.delta_testnet_api_key, self.settings.delta_testnet_api_secret
        if not key or not secret: raise DeltaError("Testnet API credentials are not configured")
        timestamp = str(int(time.time()))
        signature_data = method.upper() + timestamp + path + ("?" + query if query else "") + body
        signature = hmac.new(secret.encode(), signature_data.encode(), hashlib.sha256).hexdigest()
        return {"api-key":key,"timestamp":timestamp,"signature":signature,"Content-Type":"application/json"}

    async def request(self, method, path, params=None, payload=None, private=False):
        # Sign the exact query string sent on the wire. Delta signs the '?' and
        # query parameters as part of requestPath.
        query = urlencode(sorted((params or {}).items()))
        body = json.dumps(payload, separators=(",",":")) if payload is not None else ""
        headers = self._headers(method,path,query,body) if private else {}
        request_path = f"{path}?{query}" if query else path
        response = await self.client.request(method,request_path,content=body if body else None,headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            if response.status_code == 401:
                if code == "invalid_api_key":
                    reason = "API key is invalid or belongs to another Delta environment. Use a key created in the India Demo account."
                elif code in {"Signature Mismatch", "signature_mismatch"}:
                    reason = "Delta rejected the request signature. Check the API secret and system clock."
                elif code in {"SignatureExpired", "signature_expired"}:
                    reason = "Delta reports the request timestamp expired. Synchronize the system clock and retry."
                else:
                    reason = "Delta rejected API authentication. Verify the active India Demo key and secret, and synchronize the system clock."
                raise DeltaError(f"{reason} (HTTP 401)")
            raise DeltaError(f"Delta testnet returned HTTP {response.status_code}")
        if data.get("success") is False: raise DeltaError("Delta testnet rejected the request")
        return data

    async def products(self): return await self.request("GET","/v2/products",params={"contract_types":"perpetual_futures"})
    async def ticker(self, symbol): return await self.request("GET",f"/v2/tickers/{symbol}")
    async def candles(self, symbol, resolution="5m", limit=120):
        from datetime import datetime, timezone, timedelta
        end=datetime.now(timezone.utc); start=end-timedelta(minutes=5*limit)
        return await self.request("GET","/v2/history/candles",params={"symbol":symbol,"resolution":resolution,"start":int(start.timestamp()),"end":int(end.timestamp())})
    async def balance(self): return await self.request("GET","/v2/wallet/balances",private=True)
    async def positions(self): return await self.request("GET","/v2/positions/margined",private=True)
    async def orders(self): return await self.request("GET","/v2/orders",params={"state":"open"},private=True)
    async def create_order(self, order): return await self.request("POST","/v2/orders",payload=order,private=True)
    async def order_by_client_id(self, client_id): return await self.request("GET",f"/v2/orders/client_order_id/{client_id}",private=True)
    async def update_bracket(self, order_id, product_id, symbol, stop_loss, take_profit):
        return await self.request("PUT","/v2/orders/bracket",payload={"id":order_id,"product_id":product_id,"product_symbol":symbol,"bracket_stop_loss_price":str(stop_loss),"bracket_take_profit_price":str(take_profit),"bracket_stop_trigger_method":"mark_price"},private=True)
    async def close(self): await self.client.aclose()
