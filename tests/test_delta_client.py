import asyncio
from types import SimpleNamespace
import httpx
from backend.app.exchange.delta_client import DeltaClient, DeltaError

def test_private_request_signs_the_query_sent_over_wire(monkeypatch):
    captured={}
    def handler(request):
        captured["path"]=request.url.raw_path.decode()
        captured["headers"]=request.headers
        return httpx.Response(200,json={"success":True,"result":[]})
    client=DeltaClient(transport=httpx.MockTransport(handler))
    client.settings=SimpleNamespace(delta_testnet_api_key="demo-key",delta_testnet_api_secret="demo-secret")
    result=asyncio.run(client.request("GET","/v2/orders",params={"state":"open","product_id":3},private=True))
    assert result["success"] is True
    assert captured["path"]=="/v2/orders?product_id=3&state=open"
    assert captured["headers"]["api-key"]=="demo-key"
    assert captured["headers"]["signature"]
    assert client.base=="https://cdn-ind.testnet.deltaex.org"
    asyncio.run(client.close())

def test_private_request_fails_closed_without_credentials():
    client=DeltaClient()
    client.settings=SimpleNamespace(delta_testnet_api_key="",delta_testnet_api_secret="")
    try:
        client._headers("GET","/v2/orders")
    except DeltaError as error:
        assert "not configured" in str(error)
    else:
        raise AssertionError("Expected missing testnet credentials to block signing")
    asyncio.run(client.close())
