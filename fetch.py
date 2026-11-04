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
FUNDS = [l[4:] for l in lines if l.startswith("FON:")]
BIST = [l for l in BIST if not l.startswith("FON:")]

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
    with urllib.request.urlopen(req, timeout=10) as r:
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
# İş Yatırım'a erişim yoksa (ör. yurt dışı sunucudan engelleniyorsa) hepsini beklemeden Yahoo ile devam et
from concurrent.futures import ThreadPoolExecutor
ISY = {}
try:
    ISY["THYAO"] = isyatirim("THYAO")
    ok = ISY["THYAO"] is not None
except Exception as e:
    ok = False; print("İş Yatırım erişilemedi:", e)
if ok:
    def _get(s):
        for _ in range(3):
            try: return s, isyatirim(s)
            except Exception: time.sleep(1.5)
        return s, None
    with ThreadPoolExecutor(8) as ex:
        for s, r in ex.map(_get, BIST): ISY[s] = r
for s in BIST:
    iy = ISY.get(s)
    yh = ybist.get(s + ".IS")
    # Aynı gün ise İş Yatırım (resmi kapanış) öncelikli; gün içinde Yahoo bugünün anlık çubuğunu verir, o daha yeniyse onu al
    if iy and yh: pick, src = (yh, "yahoo") if yh[0] > iy[0] else (iy, "isyatirim")
    else: pick, src = (iy, "isyatirim") if iy else (yh, "yahoo") if yh else (None, None)
    if not pick: continue
    d, p, pd_, pc = pick
    q = {"price": round(p, 4), "prevClose": round(pc, 4), "d": d, "prevD": pd_, "src": src}
    if iy and yh and iy[0] == yh[0] and abs(iy[1] / yh[1] - 1) > 0.003:
        q["check"] = f"isyatirim {iy[1]} / yahoo {yh[1]}"; notes.append(s)
    quotes[s] = q

for t, (d, p, pd_, pc) in yahoo_closes(US).items():
    quotes[t] = {"price": round(p, 4), "prevClose": round(pc, 4), "d": d, "prevD": pd_, "src": "yahoo", "market": "US"}

FUND_ERR = {}
def _tefas_host(host, code, kind="EMK"):
    import urllib.parse, http.cookiejar
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    try: op.open(urllib.request.Request(f"https://{host}/TarihselVeriler.aspx", headers={"User-Agent": ua}), timeout=15).read()
    except Exception: pass
    body = urllib.parse.urlencode({"fontip": kind, "sfontur": "", "fonkod": code, "fongrup": "",
        "bastarih": (now - dt.timedelta(days=12)).strftime("%d.%m.%Y"), "bittarih": now.strftime("%d.%m.%Y"),
        "fonturkod": "", "fonunvantip": ""}).encode()
    req = urllib.request.Request(f"https://{host}/api/DB/BindHistoryInfo", data=body, headers={"User-Agent": ua,
        "X-Requested-With": "XMLHttpRequest", "Origin": f"https://{host}", "Referer": f"https://{host}/TarihselVeriler.aspx",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Accept": "application/json, text/javascript, */*; q=0.01"})
    raw = op.open(req, timeout=20).read().decode("utf-8", "replace")
    try: rows = json.loads(raw).get("data") or []
    except Exception: raise RuntimeError("json değil: " + raw[:120].replace("\n", " "))
    rows = [x for x in rows if x.get("FIYAT")]; rows.sort(key=lambda x: int(x["TARIH"]))
    if not rows: raise RuntimeError("boş veri")
    f = lambda x: dt.datetime.fromtimestamp(int(x["TARIH"]) / 1000, TZ).strftime("%Y-%m-%d")
    a = rows[-1]; b = rows[-2] if len(rows) > 1 else rows[-1]
    return {"price": float(a["FIYAT"]), "prevClose": float(b["FIYAT"]), "d": f(a), "prevD": f(b), "name": a.get("FONUNVAN", ""), "src": host}

def _fintables(code):
    import re
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    raw = urllib.request.urlopen(urllib.request.Request(f"https://fintables.com/fonlar/{code}", headers={"User-Agent": ua}), timeout=20).read().decode("utf-8", "replace")
    m = re.search(r'"price"\s*:\s*"?([0-9.]+)', raw) or re.search(r'Fiyat[^0-9]{0,80}([0-9]+,[0-9]{4,6})', raw)
    if not m: raise RuntimeError("fiyat bulunamadı (%d bayt)" % len(raw))
    return {"price": float(m.group(1).replace(",", ".")), "prevClose": None, "d": now.strftime("%Y-%m-%d"), "src": "fintables"}

def fund_price(code):
    errs = []
    for fn in (lambda: _tefas_host("www.tefas.gov.tr", code), lambda: _tefas_host("www.fundturkey.com.tr", code), lambda: _fintables(code)):
        try: return fn()
        except Exception as e: errs.append(f"{type(e).__name__}: {str(e)[:120]}")
    FUND_ERR[code] = errs
    return None

funds = {}
for c in FUNDS:
    r = fund_price(c)
    if r: funds[c] = r

macro = yahoo_closes(["TRY=X", "XAUUSD=X", "GC=F", "XU100.IS", "^GSPC"])
if "XAUUSD=X" in macro: macro["GC=F"] = macro["XAUUSD=X"]; res_gold_src = "spot"
else: res_gold_src = "vadeli (GC=F)"
res = {"generatedAt": now.isoformat(timespec="seconds"), "quotes": quotes, "checks": notes, "funds": funds, "fund_errors": FUND_ERR}
if "TRY=X" in macro:
    res["usdtry"], res["usdtry_prev"] = macro["TRY=X"][1], macro["TRY=X"][3]
if "GC=F" in macro and "TRY=X" in macro:
    oz = 31.1034768
    res["gold_gram_try"] = round(macro["GC=F"][1] * macro["TRY=X"][1] / oz, 2)
    res["gold_prevClose"] = round(macro["GC=F"][3] * macro["TRY=X"][3] / oz, 2)
    res["gold_src"] = res_gold_src
for k, name in (("XU100.IS", "xu100"), ("^GSPC", "spx")):
    if k in macro:
        res[name], res[name + "_prevClose"], res[name + "_d"] = macro[k][1], macro[k][3], macro[k][0]

json.dump(res, open("prices.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(funds)}/{len(FUNDS)} fon; {len(quotes)} sembol yazıldı; kontrol gereken: {notes}")
