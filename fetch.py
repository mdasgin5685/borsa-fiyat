"""Portföy Defteri için günlük fiyat toplayıcı.
GitHub Actions her hafta içi çalıştırır; sonucu prices.json olarak depoya yazar.
BIST kapanışları önce İş Yatırım'ın günlük verisinden, olmazsa Yahoo Finance'ten alınır;
iki kaynak da varsa karşılaştırılır. ABD hisseleri, endeksler, kur ve altın Yahoo'dan gelir."""
import json, datetime as dt, urllib.request, time
from zoneinfo import ZoneInfo
import yfinance as yf

TZ = ZoneInfo("Europe/Istanbul")
now = dt.datetime.now(TZ)
lines = [l.strip() for l in open("tickers.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]
BIST = [l for l in lines if not l.startswith("US:")]
US = [l[3:] for l in lines if l.startswith("US:")]

def last_two(df):
    """Bir kapanış serisinden (tarih, kapanış) olarak son iki seansı döndürür."""
    s = df.dropna()
    if len(s) < 2: return None
    return (s.index[-1].strftime("%Y-%m-%d"), float(s.iloc[-1]), s.index[-2].strftime("%Y-%m-%d"), float(s.iloc[-2]))

def isyatirim(sym):
    start = (now - dt.timedelta(days=12)).strftime("%d-%m-%Y"); end = now.strftime("%d-%m-%Y")
    url = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
           f"?hisse={sym}&startdate={start}&enddate={end}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.load(r).get("value") or []
    rows = [x for x in rows if x.get("HGDG_KAPANIS")]
    if len(rows) < 2: return None
    rows.sort(key=lambda x: dt.datetime.strptime(x["HGDG_TARIH"], "%d-%m-%Y"))
    a, b = rows[-1], rows[-2]
    f = lambda d: dt.datetime.strptime(d, "%d-%m-%Y").strftime("%Y-%m-%d")
    return (f(a["HGDG_TARIH"]), float(a["HGDG_KAPANIS"]), f(b["HGDG_TARIH"]), float(b["HGDG_KAPANIS"]))

def yahoo_closes(tickers):
    out = {}
    if not tickers: return out
    data = yf.download(tickers, period="10d", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    for t in tickers:
        try:
            col = data[t]["Close"] if len(tickers) > 1 else data["Close"]
            r = last_two(col)
            if r: out[t] = r
        except Exception:
            pass
    return out

quotes, notes = {}, []
ybist = yahoo_closes([s + ".IS" for s in BIST])
for s in BIST:
    iy = None
    try: iy = isyatirim(s); time.sleep(0.3)
    except Exception as e: pass
    yh = ybist.get(s + ".IS")
    pick, src = (iy, "isyatirim") if iy else (yh, "yahoo") if yh else (None, None)
    if not pick: continue
    d, p, pd_, pc = pick
    q = {"price": round(p, 4), "prevClose": round(pc, 4), "d": d, "prevD": pd_, "src": src}
    if iy and yh and iy[0] == yh[0] and abs(iy[1] / yh[1] - 1) > 0.003:
        q["check"] = f"isyatirim {iy[1]} / yahoo {yh[1]}"; notes.append(s)
    quotes[s] = q

for t, (d, p, pd_, pc) in yahoo_closes(US).items():
    quotes[t] = {"price": round(p, 4), "prevClose": round(pc, 4), "d": d, "prevD": pd_, "src": "yahoo", "market": "US"}

macro = yahoo_closes(["TRY=X", "GC=F", "XU100.IS", "^GSPC"])
res = {"generatedAt": now.isoformat(timespec="seconds"), "quotes": quotes, "checks": notes}
if "TRY=X" in macro:
    res["usdtry"], res["usdtry_prev"] = macro["TRY=X"][1], macro["TRY=X"][3]
if "GC=F" in macro and "TRY=X" in macro:
    oz = 31.1034768
    res["gold_gram_try"] = round(macro["GC=F"][1] * macro["TRY=X"][1] / oz, 2)
    res["gold_prevClose"] = round(macro["GC=F"][3] * macro["TRY=X"][3] / oz, 2)
for k, name in (("XU100.IS", "xu100"), ("^GSPC", "spx")):
    if k in macro:
        res[name], res[name + "_prevClose"], res[name + "_d"] = macro[k][1], macro[k][3], macro[k][0]

json.dump(res, open("prices.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(quotes)} sembol yazıldı; kontrol gereken: {notes}")
