from datetime import datetime
from zoneinfo import ZoneInfo
from ..config import get_settings
from ..database import connect, read_setting, write_setting

def assess(equity: float, realized: float|None=None, unrealized: float|None=None, fees: float|None=None, funding: float|None=None):
    cfg=read_setting("risk")
    tz=ZoneInfo(get_settings().reporting_timezone); day=datetime.now(tz).date().isoformat()
    with connect() as db:
        row=db.execute("SELECT equity,locked FROM baselines WHERE day=?",(day,)).fetchone()
        if not row:
            db.execute("INSERT INTO baselines(day,equity,locked) VALUES(?,?,NULL)",(day,equity)); baseline=equity; lock=None
        else: baseline=row[0]; lock=row[1]
        # Delta net_equity is the exchange's combined equity view. Until the
        # ledger is normalized, compare it with the persisted day baseline.
        net=(equity-baseline) if realized is None else realized+(unrealized or 0)-(fees or 0)+(funding or 0)
        loss=baseline*cfg["daily_loss_pct"]/100; profit=baseline*cfg["daily_profit_pct"]/100
        if cfg["enabled"] and lock is None:
            if net<=-loss: lock="loss"
            elif net>=profit: lock="profit"
            if lock: db.execute("UPDATE baselines SET locked=? WHERE day=?",(lock,day))
    result={"day":day,"starting_equity":baseline,"current_equity":equity,"realized_pnl":realized,"unrealized_pnl":unrealized,"fees":fees,"funding":funding,"net_pnl":net,"loss_limit":loss,"profit_target":profit,"remaining_loss_allowance":max(0,loss+net),"profit_progress_pct":max(0,100*net/profit) if profit else 0,"locked":lock,"status":"locked" if lock else "active","calculated_at":datetime.now(tz).isoformat(),"pnl_basis":"exchange net_equity change from persisted daily baseline" if realized is None else "realized + unrealized - fees + funding"}
    write_setting("risk_status",result)
    return result
