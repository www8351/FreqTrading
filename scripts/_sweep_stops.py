import glob, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
from sim_realistic import load_csv, metrics, run

# per symbol: data, value/move, spread(px), comm, qty, daily, [stop bands min/max]
CFG = {
    "XAUUSD": dict(g="data/xauusd_1m_20260303_*.csv", v=100.0, sp=0.20, cm=7.0,
                   qty=0.06, daily=110.0,
                   bands=[(1.2,2.4),(1.6,3.2),(2.0,4.0),(2.6,5.2),(3.2,6.4),(4.0,8.0)]),
    "US100":  dict(g="data/us100_1m_20260303_*.csv", v=1.0, sp=1.0, cm=0.0,
                   qty=0.80, daily=60.0,
                   bands=[(9,18),(12,24),(15,30),(20,40),(24,48),(30,60)]),
    "US500":  dict(g="data/us500_1m_20260303_*.csv", v=1.0, sp=0.25, cm=0.0,
                   qty=4.80, daily=60.0,
                   bands=[(1.5,3.0),(2.0,4.0),(2.5,5.0),(3.25,6.5),(4.0,8.0),(5.0,10.0)]),
}

for sym, p in CFG.items():
    candles = load_csv(glob.glob(p["g"]))
    print(f"\n{sym}  value={p['v']} spread={p['sp']}  (current band marked *)")
    cur = {"XAUUSD": (2.0,4.0), "US100": (15,30), "US500": (2.5,5.0)}[sym]
    for smin, smax in p["bands"]:
        tr = run(candles, p["qty"], p["sp"], p["cm"], max_daily_loss=p["daily"],
                 stop_min=smin, stop_max=smax, value_per_move=p["v"])
        dz = [t for t in tr if t["zone"] != "dead_zone"]
        live = [t for t in dz if t["day_q"] in ("Q2","Q3")]
        mb, ml = metrics(tr), metrics(live)
        mark = " *" if (smin, smax) == cur else "  "
        print(f"  {smin:5.2f}/{smax:5.2f}{mark} base n={mb['n']:<5} win%={mb['win']:4.1f} "
              f"PF={mb['pf']:4.2f} pnl=${mb['pnl']:+9.0f} | live n={ml['n']:<4} "
              f"win%={ml['win']:4.1f} PF={ml['pf']:4.2f}")
