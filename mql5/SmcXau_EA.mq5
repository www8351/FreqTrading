//+------------------------------------------------------------------+
//|                                                   SmcXau_EA.mq5   |
//|      Multi-timeframe Smart Money Concepts A+ engine for XAUUSD    |
//|                                                                  |
//|  Self-contained port of the Python orb/smc/ package (magic       |
//|  20260621). Zero DLLs, zero custom includes — only the stock     |
//|  <Trade/Trade.mqh>. Copy into <terminal>/MQL5/Experts/, compile  |
//|  (F7), attach to an XAUUSD.ecn M15 chart on DEMO.                 |
//|                                                                  |
//|  Design (matches the Python engine 1:1):                         |
//|   * HTF H4 + D1 bias via fractal BOS/CHOCH (H4 primary, D1 veto).|
//|   * Unmitigated H4/D1 order blocks + developing/prior-day POC as  |
//|     the mandatory point-of-interest.                              |
//|   * M15 trigger: >=3 confluences (htf_poi mandatory) from        |
//|     {htf_poi, ltf_sweep, displacement, cisd, alignment,           |
//|      premium_discount}; direction is ALWAYS the HTF bias.         |
//|   * Structural SL beyond the invalidation wick / OB far edge;     |
//|     2% risk sizing; layered partials 5R/7R + 10R runner; BE lock  |
//|     and swing/ATR trail, both armed only at +2R; tighten-only.    |
//|   * No averaging / martingale / grid. One position at a time.     |
//|                                                                  |
//|  State is RECOMPUTED from the last N closed bars on every new     |
//|  M15 bar (not persisted), so a terminal restart on the copy-      |
//|  trading master reproduces identical structure/OB/POC state.      |
//+------------------------------------------------------------------+
#property copyright "FreqTrading — SMC XAU"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//====================================================================
//  Inputs (mirror orb/smc/config.py SmcConfig 1:1)
//====================================================================
input group "Identity / risk"
input long    InpMagic            = 20260621;   // magic (SMC_MAGIC)
input double  InpRiskPct          = 2.0;        // % balance risked per trade
input int     InpMaxTradesPerDay  = 2;          // per-UTC-day entry cap
input double  InpDailyLossPct     = 10.0;       // halt for the UTC day at this % loss

input group "Structure / order blocks"
input int     InpSwingLookback    = 2;          // fractal half-window
input double  InpDispBodyFrac     = 0.5;        // body/range >= this = displacement
input double  InpDispAtrMult      = 1.2;        // range >= mult*ATR = displacement
input int     InpObConfirmBars    = 10;         // OB promoted if BOS within N HTF bars
input int     InpObExpiryBars     = 180;        // OB expiry in HTF bars
input int     InpAtrPeriod        = 14;         // Wilder ATR period (M15 + HTF)

input group "POC / confluence"
input double  InpPocTol           = 2.0;        // $ distance to POC = htf_poi
input int     InpTicksPerRow      = 100;        // profile row = ticks*point
input int     InpMinConfluences   = 3;          // htf_poi mandatory + total >= this
input double  InpVolMult          = 1.5;        // displacement volume gate
input int     InpVolSmaPeriod     = 20;         // volume SMA period

input group "Entry / exits"
input double  InpStopBuffer       = 0.5;        // $ beyond invalidation
input double  InpStopMaxDist      = 15.0;       // $ cap; wider structural stop = skip
input double  InpPartialR1        = 5.0;        // first partial at this R
input double  InpPartialFrac1     = 0.40;       // fraction of ORIGINAL volume
input double  InpPartialR2        = 7.0;        // second partial at this R
input double  InpPartialFrac2     = 0.30;
input double  InpFinalTpR         = 10.0;       // close remainder here (0 = trail forever)
input double  InpBeAtR            = 2.0;        // lock breakeven at this R
input double  InpTrailStartR      = 2.0;        // start trailing at this R
input int     InpTrailMode        = 0;          // 0 = swing, 1 = ATR
input double  InpTrailAtrMult      = 2.5;
input double  InpTrailBuffer       = 0.5;

input group "Misc"
input int     InpLookbackBars     = 300;        // closed bars scanned per TF each M15
input bool    InpVerbose          = true;       // journal bias/confluence lines

//====================================================================
//  Globals
//====================================================================
CTrade   trade;

datetime g_last_m15_bar   = 0;    // new-M15-bar gate
datetime g_last_m1_bar    = 0;    // new-M1-bar gate (exit management)
int      g_cur_day        = -1;   // UTC day-of-year for the trade counter
int      g_trades_today   = 0;
double   g_day_start_bal  = 0.0;  // balance at UTC-day open (daily-loss halt)
bool     g_day_halted     = false;

// Direction encoding: +1 LONG, -1 SHORT, 0 none.
#define DIR_LONG   1
#define DIR_SHORT -1
#define DIR_NONE   0

//+------------------------------------------------------------------+
//| Swing point                                                      |
//+------------------------------------------------------------------+
struct SwingPoint { datetime ts; double price; bool valid; };

//+------------------------------------------------------------------+
//| Result of a per-TF structure scan                                |
//+------------------------------------------------------------------+
struct StructResult
{
   int       trend;            // DIR_LONG / DIR_SHORT / DIR_NONE
   SwingPoint last_high;       // most recent confirmed swing high
   SwingPoint last_low;        // most recent confirmed swing low
   string     last_event;      // "BOS" | "CHOCH" | ""
   int        last_event_dir;  // direction of the last event
   int        last_event_bar;  // index (into the closed series, 0=oldest) of the break
};

//+------------------------------------------------------------------+
//| Order block                                                      |
//+------------------------------------------------------------------+
struct OrderBlock
{
   int      dir;        // DIR_LONG (bullish OB) / DIR_SHORT
   double   top;
   double   bottom;
   bool     mitigated;
   bool     active;
   int      born_bar;   // bar index where it became active
   bool     used;       // set once consumed as a POI this decision
};

//====================================================================
//  Initialisation
//====================================================================
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);   // resolves to IOC on JustMarkets .ecn
   trade.SetDeviationInPoints(20);

   g_cur_day       = DayOfYearUTC(TimeGMT());
   g_day_start_bal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_day_halted    = false;
   g_trades_today  = CountTradesTodayFromHistory();

   if(InpVerbose)
      PrintFormat("SmcXau_EA init: symbol=%s magic=%d risk=%.2f%% minConf=%d",
                  _Symbol, (int)InpMagic, InpRiskPct, InpMinConfluences);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {}

//====================================================================
//  Main tick handler
//====================================================================
void OnTick()
{
   RolloverDay();

   // --- exit management runs on every new M1 bar -------------------
   datetime m1t = iTime(_Symbol, PERIOD_M1, 0);
   if(m1t != g_last_m1_bar)
   {
      g_last_m1_bar = m1t;
      ManageOpenPositions();
   }

   // --- entry logic runs on every new M15 bar ----------------------
   datetime m15t = iTime(_Symbol, PERIOD_M15, 0);
   if(m15t == g_last_m15_bar)
      return;
   g_last_m15_bar = m15t;

   if(g_day_halted)
      return;
   if(HasOpenPosition())
      return;                                 // one position at a time
   if(g_trades_today >= InpMaxTradesPerDay)
      return;

   TryEnter();
}

//====================================================================
//  Day rollover + daily-loss halt
//====================================================================
void RolloverDay()
{
   int doy = DayOfYearUTC(TimeGMT());
   if(doy != g_cur_day)
   {
      g_cur_day       = doy;
      g_trades_today  = 0;
      g_day_start_bal = AccountInfoDouble(ACCOUNT_BALANCE);
      g_day_halted    = false;
   }
   // daily-loss circuit breaker (equity vs day-start balance)
   if(!g_day_halted && InpDailyLossPct > 0.0 && g_day_start_bal > 0.0)
   {
      double eq   = AccountInfoDouble(ACCOUNT_EQUITY);
      double loss = g_day_start_bal - eq;
      if(loss >= g_day_start_bal * InpDailyLossPct / 100.0)
      {
         g_day_halted = true;
         CloseAllOwn("daily_loss_halt");
         if(InpVerbose) Print("SMC daily-loss halt tripped");
      }
   }
}

int DayOfYearUTC(datetime t)
{
   MqlDateTime s; TimeToStruct(t, s);
   return s.day_of_year + s.year * 1000;   // unique per (year, day)
}

//====================================================================
//  ENTRY — recompute everything from closed bars, evaluate confluence
//====================================================================
void TryEnter()
{
   // --- HTF structure + bias --------------------------------------
   StructResult h4  = ScanStructure(PERIOD_H4, InpLookbackBars);
   StructResult d1  = ScanStructure(PERIOD_D1, InpLookbackBars);
   StructResult m15 = ScanStructure(PERIOD_M15, InpLookbackBars);

   int bias = h4.trend;
   if(bias == DIR_NONE) return;
   if(d1.trend != DIR_NONE && d1.trend != bias) return;   // D1 veto

   // --- decision bar = last CLOSED M15 bar (shift 1) --------------
   double dO = iOpen (_Symbol, PERIOD_M15, 1);
   double dH = iHigh (_Symbol, PERIOD_M15, 1);
   double dL = iLow  (_Symbol, PERIOD_M15, 1);
   double dC = iClose(_Symbol, PERIOD_M15, 1);
   long   dV = iVolume(_Symbol, PERIOD_M15, 1);

   double atr_m15 = AtrOnTf(PERIOD_M15, InpAtrPeriod);
   if(atr_m15 <= 0.0) return;

   // --- five non-POI confluences (read-only) ----------------------
   double sweep_level = 0.0;
   bool has_sweep_level = false;
   bool ltf_sweep    = CheckSweep(bias, m15, dH, dL, dC, sweep_level, has_sweep_level);
   bool displacement = CheckDisplacement(dO, dH, dL, dC, dV, atr_m15);
   bool cisd         = CheckCisd(bias, dC);
   bool alignment    = CheckAlignment(bias, m15);
   bool prem_disc    = CheckPremiumDiscount(bias, dC);

   int non_poi = (ltf_sweep?1:0) + (displacement?1:0) + (cisd?1:0)
               + (alignment?1:0) + (prem_disc?1:0);

   // bail before touching an OB if poi can't lift us to the gate
   if(non_poi + 1 < InpMinConfluences) return;

   // --- htf_poi (MANDATORY, evaluated last) -----------------------
   double ob_top = 0.0, ob_bottom = 0.0;
   bool   ob_used = false;
   string poi_desc = "";
   bool poi = ResolvePoi(bias, dL, dH, dC, h4, d1, poi_desc, ob_top, ob_bottom, ob_used);
   if(!poi) return;

   int total = non_poi + 1;
   if(total < InpMinConfluences) return;

   // --- structural stop -------------------------------------------
   double stop;
   if(bias == DIR_LONG)
   {
      double floor = dL;
      if(ob_used)         floor = MathMin(floor, ob_bottom);
      if(has_sweep_level) floor = MathMin(floor, sweep_level);
      stop = floor - InpStopBuffer;
   }
   else
   {
      double ceil = dH;
      if(ob_used)         ceil = MathMax(ceil, ob_top);
      if(has_sweep_level) ceil = MathMax(ceil, sweep_level);
      stop = ceil + InpStopBuffer;
   }

   double entry     = dC;                      // reference; fill re-anchors risk
   double stop_dist = MathAbs(entry - stop);
   if(stop_dist <= 0.0 || stop_dist > InpStopMaxDist) return;   // fail-safe

   // --- 2% risk sizing --------------------------------------------
   double lot = ComputeLot(stop_dist);
   if(lot <= 0.0) return;

   string reason = StringFormat("smc_%s conf=%d/6 poi=%s",
                                (bias==DIR_LONG?"long":"short"), total, poi_desc);
   if(InpVerbose)
      PrintFormat("SMC ENTRY %s lot=%.2f entry=%.2f stop=%.2f (%s)",
                  (bias==DIR_LONG?"BUY":"SELL"), lot, entry, stop, reason);

   bool ok;
   if(bias == DIR_LONG)
      ok = trade.Buy(lot, _Symbol, 0.0, stop, 0.0, reason);   // SL server-side, TP 0
   else
      ok = trade.Sell(lot, _Symbol, 0.0, stop, 0.0, reason);

   if(ok)
      g_trades_today++;
   else if(InpVerbose)
      PrintFormat("SMC order failed: retcode=%d", trade.ResultRetcode());
}

//====================================================================
//  Confluence checks (ports of orb/smc/strategy.py)
//====================================================================
bool CheckSweep(int bias, const StructResult &m15, double dH, double dL, double dC,
                double &level, bool &has_level)
{
   has_level = false;
   if(bias == DIR_LONG)
   {
      if(m15.last_low.valid && dL < m15.last_low.price && dC > m15.last_low.price)
      { level = m15.last_low.price; has_level = true; return true; }
      // prior completed H4 low as ERL reference (shift 1 = last closed H4)
      double plo = iLow(_Symbol, PERIOD_H4, 1);
      if(dL < plo && dC > plo) { level = plo; has_level = true; return true; }
      return false;
   }
   if(m15.last_high.valid && dH > m15.last_high.price && dC < m15.last_high.price)
   { level = m15.last_high.price; has_level = true; return true; }
   double phi = iHigh(_Symbol, PERIOD_H4, 1);
   if(dH > phi && dC < phi) { level = phi; has_level = true; return true; }
   return false;
}

bool CheckDisplacement(double o, double h, double l, double c, long vol, double atr)
{
   double rng = h - l;
   if(rng <= 0.0) return false;
   double body = MathAbs(c - o);
   if(body / rng < InpDispBodyFrac) return false;
   if(rng < InpDispAtrMult * atr) return false;
   // volume gate: only when real volume + a ready SMA exist
   double vsma = VolumeSmaOnTf(PERIOD_M15, InpVolSmaPeriod);
   if(vol > 0 && vsma > 0.0)
      if((double)vol < InpVolMult * vsma) return false;
   return true;
}

bool CheckCisd(int bias, double dC)
{
   // needs the two prior CLOSED M15 bars (shift 2 = prev, shift 3 = prev2)
   double pO = iOpen (_Symbol, PERIOD_M15, 2);
   double pC = iClose(_Symbol, PERIOD_M15, 2);
   double p2C= iClose(_Symbol, PERIOD_M15, 3);
   if(bias == DIR_LONG)
      return (pC < pO && dC > pO && p2C <= pO);
   return (pC > pO && dC < pO && p2C >= pO);
}

bool CheckAlignment(int bias, const StructResult &m15)
{
   if(m15.trend == bias) return true;
   // a CHOCH toward bias on the decision bar (last closed = index count-1)
   if(m15.last_event == "CHOCH" && m15.last_event_dir == bias
      && m15.last_event_bar >= (InpLookbackBars - 2))
      return true;
   return false;
}

bool CheckPremiumDiscount(int bias, double dC)
{
   double eq = DayEquilibrium();
   if(eq == EMPTY_VALUE)
   {
      double poc = DayPoc(0);
      if(poc == EMPTY_VALUE) return true;      // fail-open soft check
      eq = poc;
   }
   if(bias == DIR_LONG) return dC <= eq;
   return dC >= eq;
}

//====================================================================
//  htf_poi resolution: POC (read-only) then order blocks
//====================================================================
bool ResolvePoi(int bias, double dL, double dH, double dC,
                const StructResult &h4, const StructResult &d1,
                string &desc, double &ob_top, double &ob_bottom, bool &ob_used)
{
   ob_used = false;
   // developing-day POC
   double poc = DayPoc(0);
   if(poc != EMPTY_VALUE && MathAbs(dC - poc) <= InpPocTol)
   { desc = StringFormat("poc@%.2f", poc); return true; }
   // prior-day POC
   double ppoc = DayPoc(1);
   if(ppoc != EMPTY_VALUE && MathAbs(dC - ppoc) <= InpPocTol)
   { desc = StringFormat("prior_poc@%.2f", ppoc); return true; }
   // order blocks: D1 first (stronger), then H4
   if(FindOrderBlock(PERIOD_D1, bias, dL, dH, ob_top, ob_bottom))
   { desc = "d1_ob"; ob_used = true; return true; }
   if(FindOrderBlock(PERIOD_H4, bias, dL, dH, ob_top, ob_bottom))
   { desc = "h4_ob"; ob_used = true; return true; }
   return false;
}

//====================================================================
//  Structure scan — fractal swings + close-based BOS/CHOCH
//  Series arrays indexed OLDEST=0 .. NEWEST=n-1 (closed bars, shift>=1).
//====================================================================
StructResult ScanStructure(ENUM_TIMEFRAMES tf, int lookback_bars)
{
   StructResult r;
   r.trend = DIR_NONE; r.last_high.valid = false; r.last_low.valid = false;
   r.last_event = ""; r.last_event_dir = DIR_NONE; r.last_event_bar = -1;

   int n = lookback_bars;
   double hi[], lo[], cl[];
   datetime tm[];
   // copy CLOSED bars only: start at shift 1
   if(CopyHigh (_Symbol, tf, 1, n, hi) <= 0) return r;
   if(CopyLow  (_Symbol, tf, 1, n, lo) <= 0) return r;
   if(CopyClose(_Symbol, tf, 1, n, cl) <= 0) return r;
   if(CopyTime (_Symbol, tf, 1, n, tm) <= 0) return r;
   ArraySetAsSeries(hi, false);   // oldest first
   ArraySetAsSeries(lo, false);
   ArraySetAsSeries(cl, false);
   ArraySetAsSeries(tm, false);
   int cnt = ArraySize(cl);
   int lb  = InpSwingLookback;
   if(cnt < 2*lb + 2) return r;

   SwingPoint ref_high; ref_high.valid = false;
   SwingPoint ref_low;  ref_low.valid  = false;

   // Walk bars in order; a swing at center index j is confirmed when we reach
   // bar j+lb. Evaluate confirmation then the close-based break at each bar i.
   for(int i = 2*lb; i < cnt; i++)
   {
      int j = i - lb;                          // candidate swing center
      // strict fractal high/low over [j-lb, j+lb]
      bool is_high = true, is_low = true;
      for(int k = j-lb; k <= j+lb; k++)
      {
         if(k == j) continue;
         if(hi[k] >= hi[j]) is_high = false;
         if(lo[k] <= lo[j]) is_low  = false;
      }
      if(is_high)
      {
         ref_high.ts = tm[j]; ref_high.price = hi[j]; ref_high.valid = true;
         r.last_high = ref_high;
      }
      if(is_low)
      {
         ref_low.ts = tm[j]; ref_low.price = lo[j]; ref_low.valid = true;
         r.last_low = ref_low;
      }

      // close-based break at bar i (tie -> follow the bar's own direction)
      bool up_break = (ref_high.valid && cl[i] > ref_high.price);
      bool dn_break = (ref_low.valid  && cl[i] < ref_low.price);
      if(up_break && dn_break)
      {
         if(cl[i] >= cl[i-1]) dn_break = false; else up_break = false;
      }
      if(up_break)
      {
         r.last_event     = (r.trend == DIR_LONG || r.trend == DIR_NONE) ? "BOS" : "CHOCH";
         r.last_event_dir = DIR_LONG;
         r.last_event_bar = i;
         r.trend = DIR_LONG;
         ref_high.valid = false;               // consume
      }
      else if(dn_break)
      {
         r.last_event     = (r.trend == DIR_SHORT || r.trend == DIR_NONE) ? "BOS" : "CHOCH";
         r.last_event_dir = DIR_SHORT;
         r.last_event_bar = i;
         r.trend = DIR_SHORT;
         ref_low.valid = false;                // consume
      }
   }
   return r;
}

//====================================================================
//  Order block scan on an HTF — recompute candidates + active blocks
//  from closed bars, returning the most recent UNMITIGATED block of
//  `bias` overlapping the M15 decision bar's [dL, dH].
//====================================================================
bool FindOrderBlock(ENUM_TIMEFRAMES tf, int bias, double dL, double dH,
                    double &top, double &bottom)
{
   int n = InpLookbackBars;
   double o[], hi[], lo[], cl[];
   long   vol[];
   if(CopyOpen (_Symbol, tf, 1, n, o)  <= 0) return false;
   if(CopyHigh (_Symbol, tf, 1, n, hi) <= 0) return false;
   if(CopyLow  (_Symbol, tf, 1, n, lo) <= 0) return false;
   if(CopyClose(_Symbol, tf, 1, n, cl) <= 0) return false;
   ArraySetAsSeries(o,  false);
   ArraySetAsSeries(hi, false);
   ArraySetAsSeries(lo, false);
   ArraySetAsSeries(cl, false);
   int cnt = ArraySize(cl);
   int lb  = InpSwingLookback;
   if(cnt < 2*lb + 2) return false;

   StructResult st = ScanStructure(tf, n);   // reuse events (per-bar) below

   // Rebuild the structure again but capture per-bar events to drive OB promotion.
   // (Cheap at N<=300; keeps parity with the Python OB tracker.)
   double atrbuf = 0.0;
   OrderBlock blocks[];
   ArrayResize(blocks, 0);

   // rolling Wilder ATR over the TF
   double atr = 0.0; double tr_sum = 0.0; int atr_ready_at = InpAtrPeriod;
   double prev_close = cl[0];

   SwingPoint ref_high; ref_high.valid = false;
   SwingPoint ref_low;  ref_low.valid  = false;
   int trend = DIR_NONE;

   for(int i = 1; i < cnt; i++)
   {
      // update ATR (Wilder)
      double tr = MathMax(hi[i]-lo[i], MathMax(MathAbs(hi[i]-prev_close),
                                               MathAbs(lo[i]-prev_close)));
      if(i <= InpAtrPeriod) { tr_sum += tr; if(i == InpAtrPeriod) atr = tr_sum/InpAtrPeriod; }
      else                  atr = (atr*(InpAtrPeriod-1) + tr) / InpAtrPeriod;
      prev_close = cl[i];

      // structure event at bar i (mirror ScanStructure inline)
      int j = i - lb;
      if(j - lb >= 0 && j + lb < cnt)
      {
         bool is_high = true, is_low = true;
         for(int k = j-lb; k <= j+lb; k++)
         {
            if(k == j) continue;
            if(hi[k] >= hi[j]) is_high = false;
            if(lo[k] <= lo[j]) is_low  = false;
         }
         if(is_high){ ref_high.price = hi[j]; ref_high.valid = true; }
         if(is_low) { ref_low.price  = lo[j]; ref_low.valid  = true; }
      }
      int event_dir = DIR_NONE;
      bool up_break = (ref_high.valid && cl[i] > ref_high.price);
      bool dn_break = (ref_low.valid  && cl[i] < ref_low.price);
      if(up_break && dn_break){ if(cl[i] >= cl[i-1]) dn_break=false; else up_break=false; }
      if(up_break){ event_dir = DIR_LONG; trend = DIR_LONG; ref_high.valid=false; }
      else if(dn_break){ event_dir = DIR_SHORT; trend = DIR_SHORT; ref_low.valid=false; }

      // displacement at bar i?
      if(atr > 0.0)
      {
         double rng = hi[i]-lo[i];
         if(rng > 0.0 && MathAbs(cl[i]-o[i])/rng >= InpDispBodyFrac
            && rng >= InpDispAtrMult*atr)
         {
            int ddir = (cl[i] >= o[i]) ? DIR_LONG : DIR_SHORT;
            // last opposite-color candle before i
            for(int b = i-1; b >= MathMax(0, i-10); b--)
            {
               bool opp = (ddir == DIR_LONG) ? (cl[b] < o[b]) : (cl[b] > o[b]);
               if(opp)
               {
                  OrderBlock ob;
                  ob.dir = ddir; ob.top = hi[b]; ob.bottom = lo[b];
                  ob.mitigated = false; ob.active = false;
                  ob.born_bar = i; ob.used = false;
                  int sz = ArraySize(blocks);
                  ArrayResize(blocks, sz+1); blocks[sz] = ob;
                  break;
               }
            }
         }
      }

      // promote candidates on a same-direction event within confirm window
      if(event_dir != DIR_NONE)
      {
         for(int b = 0; b < ArraySize(blocks); b++)
         {
            if(!blocks[b].active && blocks[b].dir == event_dir
               && (i - blocks[b].born_bar) <= InpObConfirmBars)
            {
               blocks[b].active = true;
               blocks[b].born_bar = i;          // reset expiry clock to activation
            }
         }
      }

      // mitigation / invalidation / expiry against bar i (only AFTER activation)
      for(int b = 0; b < ArraySize(blocks); b++)
      {
         if(!blocks[b].active || blocks[b].mitigated) continue;
         if(i <= blocks[b].born_bar) continue;   // skip own/displacement legs
         // invalidation: close beyond far edge
         if(blocks[b].dir == DIR_LONG && cl[i] < blocks[b].bottom){ blocks[b].active=false; continue; }
         if(blocks[b].dir == DIR_SHORT && cl[i] > blocks[b].top)  { blocks[b].active=false; continue; }
         // expiry
         if((i - blocks[b].born_bar) > InpObExpiryBars){ blocks[b].active=false; continue; }
         // mitigation on touch
         if(hi[i] >= blocks[b].bottom && lo[i] <= blocks[b].top)
            blocks[b].mitigated = true;
      }
   }

   // choose the most recent active, unmitigated block of `bias` overlapping [dL,dH]
   int best = -1;
   for(int b = ArraySize(blocks)-1; b >= 0; b--)
   {
      if(blocks[b].active && !blocks[b].mitigated && blocks[b].dir == bias
         && dH >= blocks[b].bottom && dL <= blocks[b].top)
      { best = b; break; }
   }
   if(best < 0) return false;
   top = blocks[best].top; bottom = blocks[best].bottom;
   return true;
}

//====================================================================
//  Volume-profile POC (day offset 0 = today, 1 = prior UTC day)
//====================================================================
double DayPoc(int day_offset)
{
   datetime now = TimeGMT();
   MqlDateTime s; TimeToStruct(now, s);
   s.hour=0; s.min=0; s.sec=0;
   datetime day_start = StructToTime(s) - day_offset*86400;
   datetime day_end   = day_start + 86400;

   double o[], hi[], lo[], cl[];
   long   vol[];
   int copied = CopyRatesRange_M15(day_start, day_end, o, hi, lo, cl, vol);
   if(copied < 5) return EMPTY_VALUE;

   double row = InpTicksPerRow * _Point;
   if(row <= 0.0) return EMPTY_VALUE;

   // histogram: map each bar's [low,high] span across rows, weight by tick vol
   double gmin = lo[ArrayMinimum(lo)];
   int nrows = 0;
   double gmax = hi[ArrayMaximum(hi)];
   nrows = (int)MathCeil((gmax - gmin)/row) + 1;
   if(nrows <= 0 || nrows > 100000) return EMPTY_VALUE;

   double hist[];
   ArrayResize(hist, nrows);
   ArrayInitialize(hist, 0.0);
   for(int i = 0; i < copied; i++)
   {
      int r0 = (int)MathFloor((lo[i]-gmin)/row);
      int r1 = (int)MathFloor((hi[i]-gmin)/row);
      if(r1 < r0) { int t=r0; r0=r1; r1=t; }
      double w = (vol[i] > 0) ? (double)vol[i] : 1.0;   // tpo fallback
      int span = (r1 - r0 + 1);
      double per = w / span;
      for(int r = r0; r <= r1; r++)
         if(r >= 0 && r < nrows) hist[r] += per;
   }
   int pk = ArrayMaximum(hist);
   if(pk < 0) return EMPTY_VALUE;
   return gmin + (pk + 0.5)*row;
}

double DayEquilibrium()
{
   datetime now = TimeGMT();
   MqlDateTime s; TimeToStruct(now, s);
   s.hour=0; s.min=0; s.sec=0;
   datetime day_start = StructToTime(s);
   datetime day_end   = day_start + 86400;
   double o[], hi[], lo[], cl[];
   long vol[];
   int copied = CopyRatesRange_M15(day_start, day_end, o, hi, lo, cl, vol);
   if(copied < 1) return EMPTY_VALUE;
   double dmax = hi[ArrayMaximum(hi)];
   double dmin = lo[ArrayMinimum(lo)];
   return (dmax + dmin)/2.0;
}

// helper: copy M15 rates in [from,to) into separate arrays; returns count
int CopyRatesRange_M15(datetime from, datetime to,
                       double &o[], double &hi[], double &lo[], double &cl[], long &vol[])
{
   MqlRates rates[];
   int copied = CopyRates(_Symbol, PERIOD_M15, from, to, rates);
   if(copied <= 0) return 0;
   ArrayResize(o, copied); ArrayResize(hi, copied);
   ArrayResize(lo, copied); ArrayResize(cl, copied); ArrayResize(vol, copied);
   for(int i = 0; i < copied; i++)
   {
      o[i]=rates[i].open; hi[i]=rates[i].high; lo[i]=rates[i].low;
      cl[i]=rates[i].close; vol[i]=rates[i].tick_volume;
   }
   return copied;
}

//====================================================================
//  Indicators (Wilder ATR + volume SMA) over closed TF bars
//====================================================================
double AtrOnTf(ENUM_TIMEFRAMES tf, int period)
{
   int n = period + 2;
   double hi[], lo[], cl[];
   if(CopyHigh (_Symbol, tf, 1, n, hi) <= 0) return 0.0;
   if(CopyLow  (_Symbol, tf, 1, n, lo) <= 0) return 0.0;
   if(CopyClose(_Symbol, tf, 1, n, cl) <= 0) return 0.0;
   ArraySetAsSeries(hi, false); ArraySetAsSeries(lo, false); ArraySetAsSeries(cl, false);
   int cnt = ArraySize(cl);
   if(cnt < period+1) return 0.0;
   double tr_sum = 0.0;
   for(int i = 1; i <= period; i++)
      tr_sum += MathMax(hi[i]-lo[i], MathMax(MathAbs(hi[i]-cl[i-1]), MathAbs(lo[i]-cl[i-1])));
   double atr = tr_sum/period;
   for(int i = period+1; i < cnt; i++)
   {
      double tr = MathMax(hi[i]-lo[i], MathMax(MathAbs(hi[i]-cl[i-1]), MathAbs(lo[i]-cl[i-1])));
      atr = (atr*(period-1) + tr)/period;
   }
   return atr;
}

double VolumeSmaOnTf(ENUM_TIMEFRAMES tf, int period)
{
   long v[];
   if(CopyTickVolume(_Symbol, tf, 1, period, v) <= 0) return 0.0;
   if(ArraySize(v) < period) return 0.0;
   double sum = 0.0;
   for(int i = 0; i < period; i++) sum += (double)v[i];
   return sum/period;
}

//====================================================================
//  Position sizing (risk % of balance for the structural stop distance)
//====================================================================
double ComputeLot(double stop_dist)
{
   double bal        = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_money = bal * InpRiskPct / 100.0;
   double tick_val   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0 || tick_val <= 0.0 || stop_dist <= 0.0) return 0.0;
   double val_per_price = tick_val / tick_size;         // $ per 1.0 move per lot
   double lot = risk_money / (stop_dist * val_per_price);

   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vstep= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0) vstep = 0.01;
   lot = MathFloor(lot/vstep + 1e-9)*vstep;             // floor to step (never over-risk)
   if(lot < vmin) return 0.0;                           // too small -> skip (no over-risk)
   if(lot > vmax) lot = vmax;
   return lot;
}

//====================================================================
//  Exit management — layered partials + BE + trail (tighten-only).
//  Ladder state is derived from DEAL HISTORY so a restart loses nothing.
//====================================================================
void ManageOpenPositions()
{
   int total = PositionsTotal();
   for(int idx = total-1; idx >= 0; idx--)
   {
      ulong ticket = PositionGetTicket(idx);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long   ptype   = PositionGetInteger(POSITION_TYPE);
      int    dir     = (ptype == POSITION_TYPE_BUY) ? DIR_LONG : DIR_SHORT;
      double entry   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl      = PositionGetDouble(POSITION_SL);
      double vol_now = PositionGetDouble(POSITION_VOLUME);
      double px      = (dir == DIR_LONG)
                       ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                       : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      // risk distance d from the current (server-side) SL; fall back if none
      double d = (sl > 0.0) ? MathAbs(entry - sl) : 0.0;
      if(d <= 0.0) continue;                    // cannot size R without a stop
      double profit = (dir == DIR_LONG) ? (px - entry) : (entry - px);
      double r = profit / d;

      double vol0 = OriginalVolumeFromHistory(ticket, vol_now);

      // ---- partials -------------------------------------------------
      // filled levels are inferred from how much has already been closed.
      double closed_frac = 1.0 - (vol_now / MathMax(vol0, 1e-9));
      bool p1_done = closed_frac >= (InpPartialFrac1 - 1e-6);
      bool p2_done = closed_frac >= (InpPartialFrac1 + InpPartialFrac2 - 1e-6);

      if(InpFinalTpR > 0.0 && r >= InpFinalTpR)
      {
         trade.PositionClosePartial(ticket, vol_now);   // close the runner
         continue;
      }
      if(!p1_done && r >= InpPartialR1)
      {
         double v = SnapVol(vol0 * InpPartialFrac1);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }
      else if(!p2_done && r >= InpPartialR2)
      {
         double v = SnapVol(vol0 * InpPartialFrac2);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }

      // ---- BE lock + trail (both armed only at >= their R) ----------
      double new_sl = sl;
      if(InpBeAtR > 0.0 && r >= InpBeAtR)
         new_sl = Tighter(dir, new_sl, entry);          // breakeven floor

      if(r >= InpTrailStartR)
      {
         double cand = TrailCandidate(dir, px);
         if(cand != EMPTY_VALUE)
            new_sl = Tighter(dir, new_sl, cand);
      }
      // emit modify only when strictly tighter (never widen)
      if(IsStrictlyTighter(dir, sl, new_sl))
         trade.PositionModify(ticket, NormalizeDouble(new_sl, _Digits), 0.0);
   }
}

double TrailCandidate(int dir, double px)
{
   if(InpTrailMode == 1)   // ATR
   {
      double atr = AtrOnTf(PERIOD_M15, InpAtrPeriod);
      if(atr <= 0.0) return EMPTY_VALUE;
      return (dir == DIR_LONG) ? px - InpTrailAtrMult*atr : px + InpTrailAtrMult*atr;
   }
   // swing: last confirmed M15 swing +/- buffer
   StructResult m15 = ScanStructure(PERIOD_M15, InpLookbackBars);
   if(dir == DIR_LONG)
   {
      if(!m15.last_low.valid) return EMPTY_VALUE;
      return m15.last_low.price - InpTrailBuffer;
   }
   if(!m15.last_high.valid) return EMPTY_VALUE;
   return m15.last_high.price + InpTrailBuffer;
}

// return the tighter (more protective) of two SLs for the side
double Tighter(int dir, double a, double b)
{
   if(a <= 0.0) return b;
   if(b <= 0.0) return a;
   return (dir == DIR_LONG) ? MathMax(a, b) : MathMin(a, b);
}

bool IsStrictlyTighter(int dir, double cur, double proposed)
{
   if(proposed <= 0.0) return false;
   if(cur <= 0.0) return true;
   return (dir == DIR_LONG) ? (proposed > cur + _Point/2) : (proposed < cur - _Point/2);
}

//====================================================================
//  Deal-history helpers (original volume + today's trade count)
//====================================================================
double OriginalVolumeFromHistory(ulong position_id, double fallback)
{
   if(!HistorySelectByPosition(position_id)) return fallback;
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
   {
      ulong dticket = HistoryDealGetTicket(i);
      if(dticket == 0) continue;
      if(HistoryDealGetInteger(dticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return HistoryDealGetDouble(dticket, DEAL_VOLUME);
   }
   return fallback;
}

int CountTradesTodayFromHistory()
{
   datetime now = TimeGMT();
   MqlDateTime s; TimeToStruct(now, s);
   s.hour=0; s.min=0; s.sec=0;
   datetime day_start = StructToTime(s);
   if(!HistorySelect(day_start, now)) return 0;
   int deals = HistoryDealsTotal();
   int cnt = 0;
   for(int i = 0; i < deals; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != InpMagic) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) == DEAL_ENTRY_IN) cnt++;
   }
   return cnt;
}

//====================================================================
//  Small utilities
//====================================================================
bool HasOpenPosition()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic
         && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

void CloseAllOwn(string why)
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic
         && PositionGetString(POSITION_SYMBOL) == _Symbol)
         trade.PositionClose(t);
   }
}

double SnapVol(double v)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   return MathFloor(v/step + 1e-9)*step;
}

double MinVol() { return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN); }
//+------------------------------------------------------------------+
