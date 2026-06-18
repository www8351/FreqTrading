# PLAN — "המוח השני" (Fundamental / Macro Decision Layer)

> מסמך תכנון בלבד. **אין קוד בשלב זה.** מטרת המסמך: להגדיר את הארכיטקטורה,
> נקודות ההזרקה, מקורות הנתונים והשלבים, לפני כתיבת שורה אחת.
> תאריך: 2026-06-16.

---

## 0. תקציר מנהלים + תיקון הנחת יסוד

**הבהרה קריטית לפני הכול:** הפרויקט **אינו מבוסס Freqtrade.** הוא מנוע ORB/SMC
מותאם אישית (חבילת `orb/`), state machine סינכרוני טהור (`IDLE → RANGE_DEFINED →
BREAKOUT → EXIT`), שרץ על MT5, **תהליך אחד לכל סימבול** (XAUUSD, US100, US500,
XAGUSD). אין `confirm_trade_entry`, אין `custom_exit`, אין `protections` של
Freqtrade. לכן כל מושג מ-Freqtrade ימופה למקבילה האמיתית בקוד (סעיף 5).

**מה אנחנו בונים:** שכבת החלטה פנדומנטלית/מאקרו ("מוח שני") שרצה **כסיידקאר נפרד**
ומפיקה `MacroState` — מצב סיכון גלובלי + הטיה כיוונית per-asset. שכבה זו מתפקדת כ
**סינון / אישור / וטו / שינוי sizing** על האותות הטכניים, **בלי לגעת במנוע הטהור**.

**עיקרון מנחה (לפי העדפותיך):** local-first, open-source, secure-by-design,
fail-safe. אם המוח השני לא זמין — המערכת מתנהגת בדיוק כמו היום (degrade gracefully,
ברירת מחדל = לא לחסום מסחר, אלא אם הוגדר אחרת).

---

## 1. מיפוי הארכיטקטורה הקיימת + נקודות הזרקה

### 1.1 זרימת ההחלטה (Decision Flow) כפי שהיא היום

```
feed (mt5feed / twelvedata)  →  CandleStream.run()  [stream.py]
        │  נר 1m סגור
        ▼
OrbEngine.on_candle(candle)  [engine.py]  ── PURE, ללא I/O
        │  מפיק Signal(ENTRY / EXIT / REJECT)
        ▼
on_signal(sig)  [cli.py cmd_live]  ◄── ★ שרשרת הסינון החיה כבר כאן ★
        │  עובר דרך: breaker.halted → trueopen deadzone → quarter_filter
        ▼
broker.execute(sig)  [broker/mt5.py]  ── שולח אורדר אמיתי
        │
on_bar(candle)  [cli.py]  ◄── ★ נקודת פעולות risk-off / sync ★
        │  spike-cancel, daily-loss close_all, babysitter, sl chase, force_flat
```

### 1.2 נקודות ההזרקה — היכן בדיוק נחבר את המוח השני

| # | מיקום בקוד | מה קורה שם היום | מה נזריק |
|---|---|---|---|
| **A** | `cli.py::on_signal`, שורות ~300–329 | פילטרים על `ENTRY`: `breaker.halted`, `trueopen deadzone`, `quarter_filter` — כולם עושים `return` ומדלגים על האורדר | **וטו/אישור פנדומנטלי** + שינוי `qty` דינמי. אותו דפוס בדיוק כמו הפילטרים הקיימים |
| **B** | `cli.py::on_bar`, שורות ~362–372 | `DailyLossBreaker` סוגר הכול ומבטל pending כשנחצה הסף | **Risk-off גלובלי**: blackout חדשות / war-spike → `close_all` + `cancel_pending` + עצירת כניסות |
| **C** | `riskguard.py` (מודול חדש לצד `DailyLossBreaker`/`SpikeCancel`) | מחלקות guard עצמאיות, נקיות, ניתנות-לבדיקה | מחלקה `MacroGuard` חדשה — צרכן של `MacroState`, מחזירה החלטות veto/scale |
| **D** | `cli.py::build_config` + `_add_common` | בניית קונפיג מ-flags | flags חדשים: `--macro-veto`, `--macro-mode`, `--macro-state-path`, `--macro-blackout-min` |

**למה דווקא A ו-B ולא בתוך `engine.py`:** המנוע מוגדר *pure, stdlib-only, no I/O*
(README §Tech stack). הזרקת קריאות רשת/קבצים לתוכו תשבור את הטהירות, את הבדיקות
(90 passing) ואת ה-`replay` הדטרמיניסטי. כל ה-I/O והפילטור כבר חיים ב-`cli.py`
ברמת ה-orchestration — שם נשארים.

### 1.3 אילוצים מבניים שמשפיעים על העיצוב

- **תהליך אחד לסימבול, ללא state משותף** (Brain_X §1). מאקרו הוא גלובלי
  (NFP/CPI/FOMC/מלחמה משפיעים על כל הנכסים) → המוח השני חייב להיות **שירות משותף
  אחד** שכל תהליך-סימבול *קורא* ממנו, לא לוגיקה כפולה בכל תהליך.
- **כבר קיים PLANNED רלוונטי ב-Brain_X:** `news_modifier` (`news_sl_multiplier`,
  `news_lot_divider`) ו-`pre_market_blackout` (15:25–15:45 שעון ישראל). המוח השני
  הוא המימוש של ה-PLANNED הזה — לא פיצ'ר נטול-הקשר.
- **demo-only guard** קיים; כל הוספה תכבד את אותו עיקרון (`--live` בלבד למעבר).

---

## 2. ארכיטקטורת "המוח השני" — Sidecar Service

### 2.1 החלטת-על: סיידקאר ולא in-process

המוח השני יהיה **דמון לוקאלי נפרד** (`macro/` — חבילה חדשה לצד `orb/`) שמייצר
קובץ-מצב יחיד שכל תהליכי ה-orb קוראים. יתרונות:

- **decoupling מלא** מהמנוע — נכשל המוח, המסחר ממשיך (fail-safe).
- **משאב משותף אחד** לכל 4 הסימבולים — fetch אחד, לא ×4.
- **rate-limit מנוהל במקום אחד** (חלק מה-APIs מוגבלים ל-200–500 קריאות/יום).
- **קל לבדיקה ול-replay** — אפשר להזריק `MacroState` מזויף בבדיקות.

```
                       ┌─────────────────────────────────────────┐
                       │   macro/  (סיידקאר, רץ פעם אחת במכונה)    │
                       │                                          │
  APIs/feeds ───────►  │  collectors → normalizer → scorer → state│
  (calendar, GDELT,    │                                          │
   FRED, sentiment)    │   כותב:  macro_state.json  (אטומי)       │
                       └──────────────────┬───────────────────────┘
                                          │  קריאה בלבד (poll/mtime)
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼                             ▼                             ▼
   orb live --symbol XAUUSD     orb live --symbol US100      orb live --symbol US500 ...
   (MacroGuard reads state)     (MacroGuard reads state)     (MacroGuard reads state)
```

### 2.2 ערוץ התקשורת בין שני המוחות

ברירת מחדל: **קובץ JSON אחד** (`macro_state.json`) עם כתיבה אטומית
(write-temp-then-`os.replace`). פשוט, local-first, ללא תלות חיצונית, נקרא
מ-stdlib בלבד — תואם את אילוץ ה-stdlib של המנוע. שדרוג אופציונלי בעתיד: SQLite
מקומי (היסטוריה + שאילתות) או Unix socket / localhost HTTP (real-time push).
**לא** משתמשים ב-broker חיצוני / cloud — secure & local-first.

### 2.3 חוזה הנתונים — `MacroState` (סכמה)

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-16T10:40:00Z",
  "ttl_sec": 300,
  "global": {
    "risk_regime": "risk_on | neutral | risk_off",
    "risk_score": -0.42,                // [-1..+1], חיובי=risk-on
    "confidence": 0.78,                 // [0..1]
    "blackout": {
      "active": false,
      "until": null,                    // ISO; אם active → חוסם כניסות
      "reason": null                    // "FOMC" / "NFP" / "CPI" / "war_spike"
    }
  },
  "events": [                           // לוח שנה קדימה
    {"id":"FOMC-2026-06-17","ts":"2026-06-17T18:00:00Z","impact":"high",
     "kind":"rate_decision","blackout_pre_min":30,"blackout_post_min":30}
  ],
  "assets": {
    "XAUUSD": {"bias":"bullish","score":0.55,"horizon":"intraday","drivers":["risk_off","real_yields_down"]},
    "US100":  {"bias":"bearish","score":-0.30,"horizon":"intraday","drivers":["semis_weak","cpi_hot"]},
    "US500":  {"bias":"neutral","score":0.05,"horizon":"intraday","drivers":[]},
    "XAGUSD": {"bias":"bullish","score":0.40,"horizon":"intraday","drivers":["risk_off"]}
  }
}
```

`ttl_sec` קריטי ל-fail-safe: אם `MacroGuard` רואה state ישן מ-`ttl` → מתייחס
ל-state כ"לא ידוע" (stale) ונופל ל-policy ברירת מחדל (סעיף 6.2).

---

## 3. מקורות נתונים (Open-Source / Local-First priority)

מאורגן לפי ארבעת הצירים שביקשת. סדר עדיפות: חינמי+open-source → free tier → בתשלום.

### 3.1 לוח שנה כלכלי — NFP / CPI / FOMC (forward-looking schedule)

מה צריך: **מתי** מתפרסם אירוע (לחסימת blackout) + impact + forecast/previous.

- **ForexFactory scraper (open-source)** — פרויקט GitHub `economic-calendar-api`
  מחזיר את לוח ForexFactory כ-JSON (date/time/currency/impact/actual/forecast/
  previous). מקור ה-impact הטוב ביותר, ללא מפתח. סיכון: שינויי HTML → צריך
  health-check וגיבוי. **בחירת ברירת מחדל ל-schedule.**
- **FRED API (חינם, מפתח חינמי)** — נתוני **actual** רשמיים (PAYEMS=NFP,
  CPIAUCSL=CPI, DFF=Fed Funds). לא לוח קדימה, אלא ה-actual הסמכותי לאחר פרסום
  + הטיית הפתעה (surprise = actual − forecast). **בחירת ברירת מחדל ל-actuals.**
- **Financial Modeling Prep / Fin2Dev / FinanceFlowAPI** — free tier ללוח כלכלי
  ב-JSON. גיבוי/הצלבה ל-ForexFactory.

> שילוב מומלץ: **ForexFactory (schedule+impact) + FRED (actual+surprise)**.

### 3.2 חדשות + Sentiment

- **GDELT 2.0 DOC API (חינם לחלוטין, ללא מפתח, עדכון כל 15 דק')** — כיסוי גלובלי,
  tone score מובנה, 65+ שפות, themes/CAMEO. **עמוד התווך** גם לחדשות וגם
  לגיאופוליטיקה. בלי תקרת קריאות.
- **FinBERT (ProsusAI, HuggingFace) — self-hosted, ללא עלות API** — מודל סנטימנט
  פיננסי. רץ לוקאלית; מקבל כותרות (מ-GDELT / RSS / Guardian) ומחזיר
  positive/negative/neutral + ציון. תואם local-first. חלופה כבדה: FinGPT.
- **מקורות כותרות נוספים (free tier):** Guardian Open Platform (5,000/יום),
  NewsData.io (~200–500/יום), RSS ישיר (Reuters/Investing/CNBC) — ללא תקרה.

### 3.3 גיאופוליטיקה / מלחמות (risk-off detection)

- **GDELT** (כנ"ל) — CAMEO event codes + tone לזיהוי הסלמה. ספייק שלילי חד בטון
  סביב actors/geo רלוונטיים → טריגר `risk_off` / `war_spike` blackout.
- **פרוקסי שוק כ-confirmation:** VIX, DXY, US10Y real yields, נפט — מאשרים
  risk-off "אמיתי" (לא רק רעש חדשותי). זמינים דרך FRED / yfinance לוקאלי.

### 3.4 AI / שבבים (Semiconductors)

- **פרוקסי אקוויטי:** מדד SOX (סמיקונדקטורים), NVDA/AVGO/TSM/SMCI — מומנטום יחסי
  כ-proxy ל"חוזק תמת AI". משפיע ישירות על **US100** (Nasdaq, כבד-טק). מקור:
  yfinance לוקאלי / הפיד הקיים.
- **תמות חדשות:** סינון GDELT לפי themes (AI/chips/export-controls) + FinBERT
  לסנטימנט. אירועי export-controls/סנקציות שבבים = דרייבר ל-`US100` bias.

> **הערה:** רוב ה-APIs הציבוריים חוסמים גישה מ-`curl`/`requests` תחת ה-policy
> שלנו. ה-fetching ייעשה בסיידקאר עם health-checks; כל מקור מאחורי adapter
> מוחלף, כך שאפשר לכבות/להחליף מקור בלי לגעת ב-scorer.

---

## 4. מודול ניתוח / Scoring

ממיר אירועים גולמיים ל-`MacroState`. צינור 4-שלבי, כל שלב מודול נפרד וניתן-לבדיקה:

```
collectors/  →  normalizer  →  scorer  →  state_writer
(adapter per   (אירוע אחיד:   (חוקים +    (JSON אטומי
 source)        ts,impact,     משקלות →     + ttl)
                actual,fcast)   score)
```

### 4.1 נרמול (normalizer)
כל collector מחזיר `RawEvent` אחיד: `{source, ts, kind, asset_scope, impact,
actual, forecast, previous, tone, text}`. מנטרל הבדלי פורמט/אזורי-זמן (הכול ל-UTC,
כמו המנוע).

### 4.2 ניקוד (scorer) — שכבות
1. **Event surprise:** `surprise = (actual − forecast)/σ`. ממופה ל-bias per-asset
   דרך טבלת רגישות (למשל CPI חם → USD↑ → XAU↓, US100↓; NFP חזק → risk-on).
2. **Sentiment aggregate:** ממוצע משוקלל-זמן (חלון מתגלגל) של ציוני FinBERT על
   כותרות רלוונטיות per-asset, עם half-life decay.
3. **Geopolitical risk:** ספייק ב-GDELT tone שלילי + אישור פרוקסי שוק (VIX/DXY)
   → `risk_off` + `war_spike` blackout אם חוצה סף.
4. **Thematic (AI/semis):** מומנטום SOX/NVDA יחסי → דרייבר ל-US100/US500.

פלט per-asset: `score ∈ [-1..+1]`, `confidence ∈ [0..1]`, `bias`, `drivers[]`,
`horizon`. הציון הגלובלי = aggregation של הצירים + override של blackout פעיל.

### 4.3 Blackout windows (קשיח, לא הסתברותי)
לכל אירוע high-impact → חלון חסימה `[ts − pre_min, ts + post_min]`. ברירת מחדל
מ-Brain_X: 30 דק' לפני/אחרי FOMC/NFP/CPI (מתיישר עם `pre_market_blackout`
הקיים 15:25–15:45). בתוך החלון: `blackout.active=true` → המוח השני מורה וטו על
**כל** כניסה חדשה, ללא תלות בציון.

### 4.4 שקיפות והסבר
כל החלטת veto/scale נרשמת ל-log עם ה-`drivers` שגרמו לה (פורמט pipe-delimited
key=val כמו שאר ה-orb), כדי שתהיה ניתנת-ביקורת ב-backtest וב-live.

---

## 5. ממשק עם הלוגיקה הטכנית — מיפוי Freqtrade → מנוע אמיתי

| מושג Freqtrade שביקשת | המקבילה האמיתית בפרויקט שלנו | מימוש המוח השני |
|---|---|---|
| `confirm_trade_entry` | פילטר ב-`cli.py::on_signal` על `SignalKind.ENTRY` (נק' הזרקה **A**) | `MacroGuard.allow_entry(sig) → (ok, scaled_qty, reason)`. `ok=False` → `return` (אותו דפוס כמו `TRUEOPEN_SKIP`) |
| `custom_stake_amount` / sizing | `cfg.qty` מועבר ל-`broker.execute` | מכפיל דינמי: `news_lot_divider` (Brain_X §2) — חצי lot ב-impact גבוה כדי לשמור 5% סיכון |
| `custom_exit` | אין; יציאות במנוע (`_exit`) + babysitter | **לא** נוגעים ביציאות הטכניות. מאקרו רק *מונע כניסה* או *סוגר proaktively* (להלן) |
| `protections` | `DailyLossBreaker` + `SpikeCancel` ב-`riskguard.py` | `MacroGuard` כ-guard נוסף; `risk_off`/`war_spike` ב-`on_bar` → `close_all`+`cancel_pending` (נק' הזרקה **B**) |

### 5.1 שלושת מצבי הפעולה (`--macro-mode`)
- **`off`** — המוח השני כבוי לחלוטין (התנהגות היום). ברירת מחדל בהתחלה.
- **`filter`** (מומלץ ל-rollout) — וטו על כניסות בלבד + scaling. **לא** סוגר
  פוזיציות פתוחות. שמרני, low-risk.
- **`guard`** — כמו filter + risk-off פעיל סוגר/מצמצם פוזיציות פתוחות בזמן
  blackout/war-spike. אגרסיבי יותר; להפעיל רק אחרי backtest.

### 5.2 לוגיקת ההחלטה (filter mode)
```
on ENTRY signal:
  st = MacroGuard.read()                  # קריאה מ-macro_state.json
  if st is stale (age > ttl):  → default policy (סעיף 6.2)
  if st.global.blackout.active:           → VETO  (reason=blackout:FOMC)
  asset = st.assets[symbol]
  if mode==filter and sign(asset.score) opposes signal.direction
        and asset.confidence ≥ conf_min:  → VETO  (reason=macro_bias_conflict)
  if asset.score aligns / neutral:        → ALLOW, qty *= scale(impact, confidence)
```
**עיקרון:** המאקרו הוא **שכבת אישור/וטו** מעל הטכני — הוא לעולם לא *יוזם* עסקה.
טריגר הכניסה תמיד טכני (ORB breakout + ROC). זה שומר על ה-edge הקיים שנבדק.

---

## 6. State, Error Handling, תזמון

### 6.1 תזמון (real-time vs polling)
- **סיידקאר:** scheduler מדורג לפי מקור — לוח כלכלי כל ~15 דק' (משתנה לאט),
  GDELT/sentiment כל 5–15 דק', פרוקסי שוק (VIX/DXY) כל דקה. כותב `MacroState`
  אחרי כל מחזור. אירועי blackout מחושבים מראש מהלוח (לא תלויי polling בזמן אמת).
- **צרכן (orb):** קורא ב-`on_bar` (כבר רץ פעם בנר). זול: קריאת mtime + parse JSON
  רק אם השתנה. אפס latency נוסף משמעותי.
- **דיוק blackout:** מאחר שהמנוע מעבד נר 1m סגור, רזולוציית החסימה היא דקה —
  מספיק; לחסימה חדה לפני NFP מרחיבים `pre_min` בדקה ביטחון.

### 6.2 Fail-safe policy (החלק הכי חשוב)
| תקלה | התנהגות |
|---|---|
| `macro_state.json` חסר | `mode` יורד אפקטיבית ל-`off` → מסחר כרגיל. log WARNING |
| state ישן (> ttl) | `default_when_stale` (flag): `allow` (ברירת מחדל, שמרני-למסחר) או `block` |
| collector בודד נכשל | סיידקאר ממשיך עם שאר המקורות; משדה את אותו אסט כ-`confidence` נמוך |
| כל המקורות נפלו | סיידקאר כותב `risk_regime=neutral, confidence=0` → אין veto |
| JSON פגום | parse מוגן; הצרכן מתעלם ונשאר על ה-state האחרון התקין עד ttl |

**עיקרון על:** כשל במוח השני **לעולם לא** עוצר מסחר תקין ולעולם לא פותח פוזיציה.
הכי גרוע שיקרה — חוזרים להתנהגות של היום.

### 6.3 Idempotency & טסטים
- `MacroState` כתיבה אטומית (`os.replace`) → הצרכן לא רואה קובץ חצי-כתוב.
- `MacroGuard` מחלקה pure (כמו `DailyLossBreaker`) → unit tests בלי רשת:
  מזריקים `MacroState` ובודקים allow/veto/scale. מצטרף ל-90 הטסטים הקיימים.
- backtest: `replay` מקבל `MacroState` היסטורי (snapshot לפי ts) כדי לבדוק את
  ה-edge של הסינון על נתוני העבר לפני live.

---

## 7. שלבי יישום (Milestones) + סיכון/מורכבות

| שלב | תוצר | מורכבות | סיכון | תלות |
|---|---|---|---|---|
| **M0 — חוזה + שלד** | סכמת `MacroState`, חבילה `macro/`, `MacroGuard` (pure) + טסטים, flags ב-CLI, mode=`off` בלבד | נמוכה | נמוך | — |
| **M1 — Blackout בלבד** | collector ForexFactory schedule → חלונות blackout. הזרקה A: וטו בזמן FOMC/NFP/CPI. זה כבר ה-PLANNED `pre_market_blackout` | נמוכה-בינונית | נמוך | M0 |
| **M2 — FRED actuals + surprise** | scorer שכבה 1 (event surprise → bias per-asset). mode=`filter` | בינונית | בינוני (כיול טבלת רגישות) | M1 |
| **M3 — GDELT geopolitics** | risk-off / war-spike + אישור פרוקסי שוק. mode=`guard` (סגירה proaktive) | בינונית-גבוהה | בינוני-גבוה (false positives) | M2 |
| **M4 — Sentiment (FinBERT)** | self-hosted FinBERT, scorer שכבה 2, decay | גבוהה (תשתית מודל) | בינוני | M2 |
| **M5 — AI/Semis thematic** | SOX/NVDA momentum → US100/US500 bias | בינונית | נמוך-בינוני | M2 |
| **M6 — Backtest + כיול** | `replay` עם MacroState היסטורי; מדידת PF לפני/אחרי הסינון per-symbol | גבוהה | — (validation) | M2–M5 |

**המלצת rollout:** M0→M1→M2 ואז **shadow mode** (לוג בלבד, בלי veto אמיתי)
לשבוע, השוואת החלטות מול תוצאות, ורק אז הפעלת `filter` חי. `guard` (M3) אחרי
backtest מובהק. זה מתיישר עם הדיסציפלינה ב-Brain_X (כל שינוי מגובה backtest).

---

## 8. שאלות פתוחות / החלטות לפני יישום

1. **ערוץ State:** JSON אטומי (מומלץ, stdlib, local-first) או SQLite/HTTP-localhost?
   ברירת מחדל שלי: JSON ל-M0–M2, לשקול SQLite מ-M3 (היסטוריה ל-backtest).
2. **default_when_stale:** כש-state ישן — `allow` (לא לחסום מסחר) או `block`
   (שמרני-סיכון)? משפיע על אופי ה-fail-safe.
3. **filter vs guard לפוזיציות פתוחות:** האם מאקרו רשאי *לסגור* פוזיציה רווחית
   באמצע בגלל war-spike, או רק למנוע כניסות חדשות? (מ-3 → guard).
4. **טבלת רגישות per-asset:** מי מגדיר את מיפוי CPI/NFP→bias — ידני (מבוסס
   מאקרו קלאסי) או נלמד מ-backtest? מתחילים ידני, מכיילים ב-M6.
5. **תקציב מקורות:** האם נשארים 100% חינמי/open-source (GDELT+FRED+ForexFactory
   scrape+FinBERT), או free-tier מסחרי (FMP) מותר כגיבוי?
6. **scope ה-blackout:** גלובלי (כל הסימבולים) או per-currency (NFP/CPI משפיעים
   בעיקר על USD-pairs)? כרגע הכול USD-denominated אז גלובלי מספיק.
7. **FinBERT — איפה רץ:** אותה מכונה כמו הבוטים (CPU מספיק לכותרות) או מכונה
   נפרדת? משפיע על M4.
8. **policy רשת:** חלק מה-APIs נחסמים מ-fetch ישיר תחת ה-guardrails — לאמת מראש
   אילו מקורות נגישים מהסביבה התפעולית שלך.

---

## נספח — קבצים שנקראו למיפוי
`orb/engine.py`, `orb/models.py`, `orb/stream.py`, `orb/riskguard.py`,
`orb/cli.py`, `Brain_X.md`, `README.md`.

**מקורות מחקר (data sources):**
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) · [GDELT data](https://www.gdeltproject.org/data.html)
- [FRED — St. Louis Fed](https://fred.stlouisfed.org/) · [PAYEMS / NFP](https://fred.stlouisfed.org/tags/series?t=nonfarm%3Bpayrolls)
- [economic-calendar-api (GitHub, ForexFactory→JSON)](https://github.com/andrevlima/economic-calendar-api)
- [FMP Economic Calendar API](https://site.financialmodelingprep.com/developer/docs/stable/economics-calendar)
- [FinBERT (ProsusAI, GitHub)](https://github.com/ProsusAI/finBERT) · [finbert.org](https://finbert.org/)
- [Free News APIs 2026 comparison](https://dataresearchtools.com/best-news-apis-comparison/)
