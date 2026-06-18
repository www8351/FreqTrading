import glob, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
from sim_realistic import load_csv, metrics, run

candles = load_csv(glob.glob("data/xagusd_1m_20260303_*.csv"))
print("XAGUSD spread=0.03 value=5000 | sweep iron stop band (win% over full sample)")
for smin, smax in [(0.055,0.110),(0.08,0.16),(0.10,0.20),(0.15,0.30),(0.20,0.40)]:
    tr = run(candles, 0.04, 0.03, 7.0, max_daily_loss=60.0,
             stop_min=smin, stop_max=smax, value_per_move=5000.0)
    dz = [t for t in tr if t["zone"] != "dead_zone"]
    live = [t for t in dz if t["day_q"] in ("Q2","Q3")]
    mb, ml = metrics(tr), metrics(live)
    print(f"  stop {smin:.3f}/{smax:.3f}: baseline n={mb['n']:<5} win%={mb['win']:4.1f} "
          f"PF={mb['pf']:4.2f} pnl=${mb['pnl']:+8.0f} | live n={ml['n']:<4} "
          f"win%={ml['win']:4.1f} PF={ml['pf']:4.2f}")
