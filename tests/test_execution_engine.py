from decimal import Decimal
from backend.app.execution.engine import _interval_seconds, _round_to_tick

def test_rounding_exit_prices_to_exchange_tick():
    assert _round_to_tick(Decimal("100.24"),Decimal("0.5"),False)==Decimal("100.0")
    assert _round_to_tick(Decimal("100.24"),Decimal("0.5"),True)==Decimal("100.5")

def test_timeframe_parsing():
    assert _interval_seconds("5m")==300
    assert _interval_seconds("1h")==3600
