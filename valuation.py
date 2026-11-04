"""Portföy Defteri için aylık değerleme raporu.

Her hisse için "kağıt üstündeki değer" üç yöntemle hesaplanır ve ortalaması alınır:
  1) Sektör çarpanları: aynı sektördeki şirketlerin medyan F/K ve PD/DD'si
  2) Kendi geçmişi: şirketin enflasyon muhasebesi dönemindeki (2023 sonu →) medyan F/K ve PD/DD'si
  3) Graham formülü: √(22,5 × hisse başı kâr × hisse başı özsermaye)
BIST verisi İş Yatırım'dan (mali tablolar, piyasa değeri), ABD verisi Yahoo Finance'ten gelir.
Sonuç valuation.json'a yazılır. Bu bir yatırım tavsiyesi değildir; yöntemlerin varsayımlarına bağlı bir hesaptır."""
import json, math, time, statistics as st, datetime as dt, urllib.request
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
try:
    import yfinance as yf
except ImportError:
    yf = None  # VAL_OFFLINE ile yerel deneme

TZ = ZoneInfo("Europe/Istanbul")
now = dt.datetime.now(TZ)
lines = [l.strip() for l in open("tickers.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]
BIST = [l for l in lines if not l.startswith(("US:", "FON:"))]
US = [l[3:] for l in lines if l.startswith("US:")]
B = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
HIST_FROM = (2023, 12)  # enflasyon muhasebesi (TMS 29) ile tutarlı dönem
ERR = {}

def get(url, tries=3):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last

def med(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return st.median(xs) if xs else None

# ---------------------------------------------------------------- BIST
def periods():
    out = []
    for y in range(HIST_FROM[0] - 1, now.year + 1):
        for p in (3, 6, 9, 12):
            if (y, p) <= (now.year, now.month): out.append((y, p))
    return out

def mali(code):
    """{(yıl, dönem): {'net': ana ortaklık net kârı (yılbaşından), 'eq': ana ortaklık özkaynağı}} ve kullanılan grup."""
    ps = periods()
    for grp in ("XI_29", "UFRS_K", "UFRS", "XI_29K"):
        res = {}
        for i in range(0, len(ps), 4):
            chunk = ps[i:i + 4]
            while len(chunk) < 4: chunk = chunk + [ps[0]]  # uç nokta tam 4 dönem istiyor
            q = "&".join(f"year{j+1}={y}&period{j+1}={p}" for j, (y, p) in enumerate(chunk))
            try: rows = get(B + f"MaliTablo?companyCode={code}&exchange=TRY&financialGroup={grp}&{q}").get("value") or []
            except Exception: rows = []
            if not rows: continue
            for j, per in enumerate(chunk):
                net = eq = None
                for r in rows:
                    v = r.get(f"value{j+1}")
                    try: v = float(v) if v not in (None, "") else None
                    except Exception: v = None
                    if r.get("itemCode") == "3Z": net = v
                    elif r.get("itemCode") == "2O": eq = v
                if eq:  # özkaynak 0/boşsa dönem açıklanmamış
                    res[per] = {"net": net or 0.0, "eq": eq}
        if res: return res, grp
    return {}, None

def ttm(res, y, p):
    if (y, p) not in res: return None
    if p == 12: return res[(y, p)]["net"]
    a, b = res.get((y - 1, 12)), res.get((y - 1, p))
    if not a or not b: return None
    return res[(y, p)]["net"] + a["net"] - b["net"]

def market(code):
    """Günlük piyasa değeri serisi {tarih: PD} ve son satır."""
    start = dt.date(HIST_FROM[0], HIST_FROM[1], 1) - dt.timedelta(days=10)
    rows = []
    s = start
    while s <= now.date():  # uç nokta uzun aralıkları kısabildiği için yıllık parçalar
        e = min(now.date(), s + dt.timedelta(days=365))
        try: rows += get(B + f"HisseTekil?hisse={code}&startdate={s:%d-%m-%Y}&enddate={e:%d-%m-%Y}").get("value") or []
        except Exception: pass
        s = e + dt.timedelta(days=1)
    ser = {}
    for r in rows:
        if r.get("PD"):
            ser[dt.datetime.strptime(r["HGDG_TARIH"], "%d-%m-%Y").date()] = (float(r["PD"]), float(r.get("SERMAYE") or 0), float(r.get("HGDG_KAPANIS") or 0))
    return ser

def pd_at(ser, d):
    ks = [k for k in ser if k <= d]
    return ser[max(ks)][0] if ks else None

def bist_one(code):
    try:
        res, grp = mali(code)
        if not res: ERR[code] = "mali tablo bulunamadı"; return code, None
        ser = market(code)
        if not ser: ERR[code] = "piyasa değeri bulunamadı"; return code, None
        last_d = max(ser); pd_now, shares, price = ser[last_d]
        pers = sorted(res)
        # tutarlılık: son dönemin özkaynağı bir önceki dönemin 1/3'ünden az ya da 3 katından fazlaysa (birim/format değişikliği,
        # hatalı veri) o dönemi atla ve uyarı koy
        flag = None
        while len(pers) >= 2:
            a_, b_ = res[pers[-1]]["eq"], res[pers[-2]]["eq"]
            if b_ and a_ and 1/3 <= a_ / b_ <= 3: break
            flag = f"{pers[-1][0]}/{pers[-1][1]} bilançosu bir öncekiyle tutarsız göründüğü için kullanılmadı"
            res.pop(pers[-1]); pers = pers[:-1]
        latest = pers[-1]
        net_ttm, eq = ttm(res, *latest), res[latest]["eq"]
        hist_pe, hist_pb = [], []
        for (y, p) in pers:
            if (y, p) < HIST_FROM: continue
            end = dt.date(y, p, 28) + dt.timedelta(days=4); end = end - dt.timedelta(days=end.day)
            m = pd_at(ser, end)
            if not m: continue
            t = ttm(res, y, p)
            if t and t > 0: hist_pe.append(m / t)
            if res[(y, p)]["eq"] > 0: hist_pb.append(m / res[(y, p)]["eq"])
        return code, {"market": "BIST", "group": grp, "price": price, "priceDate": str(last_d), "shares": shares, "mcap": pd_now,
                      "netTTM": net_ttm, "equity": eq, "period": f"{latest[0]}/{latest[1]}",
                      "histPE": hist_pe, "histPB": hist_pb, "histFrom": f"{HIST_FROM[0]}/{HIST_FROM[1]}", "flag": flag,
                      "series": {f"{y}/{p}": v for (y, p), v in res.items()}}
    except Exception as e:
        ERR[code] = f"{type(e).__name__}: {str(e)[:120]}"; return code, None

# ---------------------------------------------------------------- ABD
def us_one(t):
    try:
        tk = yf.Ticker(t); inf = tk.info or {}
        price = inf.get("currentPrice") or inf.get("regularMarketPrice")
        shares = inf.get("sharesOutstanding")
        eps, bvps = inf.get("trailingEps"), inf.get("bookValue")
        if not (price and shares): ERR[t] = "fiyat/hisse sayısı yok"; return t, None
        hist_pe, hist_pb = [], []
        try:
            inc, bal = tk.income_stmt, tk.balance_sheet
            px = tk.history(period="6y", interval="1d", auto_adjust=False)["Close"]
            for c in inc.columns:
                e = inc.loc["Diluted EPS", c] if "Diluted EPS" in inc.index else None
                p = px[px.index.tz_localize(None) <= c].iloc[-1] if len(px[px.index.tz_localize(None) <= c]) else None
                if p is None: continue
                if e and e == e and e > 0: hist_pe.append(float(p / e))
                if c in bal.columns:
                    eqv = next((bal.loc[k, c] for k in ("Common Stock Equity", "Stockholders Equity") if k in bal.index), None)
                    sh = bal.loc["Ordinary Shares Number", c] if "Ordinary Shares Number" in bal.index else None
                    if eqv and sh and eqv == eqv and sh == sh and eqv > 0: hist_pb.append(float(p / (eqv / sh)))
        except Exception as e:
            ERR[t + "_hist"] = str(e)[:120]
        return t, {"market": "US", "price": float(price), "priceDate": now.strftime("%Y-%m-%d"), "shares": float(shares),
                   "mcap": float(price) * float(shares), "netTTM": (eps or 0) * shares if eps is not None else None,
                   "equity": (bvps or 0) * shares if bvps is not None else None, "period": "son 12 ay",
                   "histPE": hist_pe, "histPB": hist_pb, "histFrom": "yıllık, son 4 yıl",
                   "sector": inf.get("sector"), "industry": inf.get("industry"), "name": inf.get("shortName")}
    except Exception as e:
        ERR[t] = f"{type(e).__name__}: {str(e)[:120]}"; return t, None

def sector_of(code):
    try:
        inf = yf.Ticker(code + ".IS").info or {}
        return code, (inf.get("sector"), inf.get("industry"), inf.get("shortName"))
    except Exception:
        return code, (None, None, None)

# ---------------------------------------------------------------- çalıştır
import os, sys
if os.environ.get("VAL_OFFLINE"):
    _raw = json.load(open("valuation_raw.json", encoding="utf-8")); data, ERR = _raw["data"], _raw["errors"]
else:
    import sys
    T0 = time.time()
    def log(*a): print(f"[{time.time()-T0:6.0f}s]", *a, flush=True)
    data = {}
    with ThreadPoolExecutor(6) as ex:
        for k, (c, r) in enumerate(ex.map(bist_one, BIST)):
            if r: data[c] = r
            if k % 10 == 0: log("BIST", k, c, bool(r))
    log("BIST bitti", len(data))
    with ThreadPoolExecutor(4) as ex:
        for c, (s_, i, n) in ex.map(sector_of, list(data)):
            data[c].update(sector=s_, industry=i, name=n)
    log("sektörler bitti", sum(1 for x in data.values() if x.get("sector")))
    with ThreadPoolExecutor(4) as ex:
        for c, r in ex.map(us_one, US):
            if r: data[c] = r
    log("ABD bitti", len(data))
    json.dump({"data": data, "errors": ERR, "at": now.isoformat(timespec="seconds")}, open("valuation_raw.json", "w", encoding="utf-8"), ensure_ascii=False)

TR_SECTOR = {"Financial Services": "Finans", "Industrials": "Sanayi", "Consumer Cyclical": "Döngüsel tüketim", "Consumer Defensive": "Temel tüketim",
             "Energy": "Enerji", "Basic Materials": "Temel malzeme", "Technology": "Teknoloji", "Communication Services": "İletişim",
             "Healthcare": "Sağlık", "Utilities": "Kamu hizmetleri", "Real Estate": "Gayrimenkul"}
fmt = lambda x, d=1: "—" if x is None else f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")

for c, r in data.items():
    sh, net, eq = r["shares"], r.get("netTTM"), r.get("equity")
    r["pe"] = r["mcap"] / net if net and net > 0 else None
    r["pb"] = r["mcap"] / eq if eq and eq > 0 else None
    r["eps"] = net / sh if net is not None and sh else None
    r["bvps"] = eq / sh if eq is not None and sh else None

def peers(c):
    r = data[c]
    for key in ("industry", "sector"):
        if not r.get(key): continue
        ps = [k for k, x in data.items() if k != c and x["market"] == r["market"] and x.get(key) == r[key]]
        pe = [data[k]["pe"] for k in ps if data[k]["pe"] and data[k]["pe"] < 80]
        pb = [data[k]["pb"] for k in ps if data[k]["pb"] and data[k]["pb"] < 30]
        if len(pe) >= 3 or len(pb) >= 3:
            return key, ps, (med(pe) if len(pe) >= 3 else None), (med(pb) if len(pb) >= 3 else None)
    return None, [], None, None

# Graham'ın 22,5'i (F/K 15 × PD/DD 1,5) ABD içindir; BIST'te piyasanın kendi medyan F/K × PD/DD'si kullanılır
_bpe = med([x["pe"] for x in data.values() if x["market"] == "BIST" and x["pe"] and x["pe"] < 80])
_bpb = med([x["pb"] for x in data.values() if x["market"] == "BIST" and x["pb"] and x["pb"] < 30])
GK_BIST = (_bpe or 15) * (_bpb or 1.5)
out = {}
for c, r in data.items():
    sh, net, eq, price = r["shares"], r.get("netTTM"), r.get("equity"), r["price"]
    lvl, ps, spe, spb = peers(c)
    hpe, hpb = (med(r["histPE"]) if len(r["histPE"]) >= 3 else None), (med(r["histPB"]) if len(r["histPB"]) >= 3 else None)
    def fair(pe, pb):
        v = []
        if pe and net and net > 0: v.append(net * pe / sh)
        if pb and eq and eq > 0: v.append(eq * pb / sh)
        return sum(v) / len(v) if v else None
    m = {"sector": fair(spe, spb), "history": fair(hpe, hpb)}
    fin = any(k in (r.get("industry") or "") for k in ("Bank", "Insurance", "Credit", "Conglomerate", "REIT", "Capital Markets", "Asset Management"))
    gk = 22.5 if r["market"] == "US" else GK_BIST
    m["graham"] = math.sqrt(gk * r["eps"] * r["bvps"]) if not fin and r["eps"] and r["bvps"] and r["eps"] > 0 and r["bvps"] > 0 else None
    vals = [v for v in m.values() if v]
    mid = sum(vals) / len(vals) if vals else None
    up = (mid / price - 1) * 100 if mid and price else None
    # kısa açıklama
    cur = "$" if r["market"] == "US" else "₺"
    notes = []
    if r["pe"]: notes.append(f"F/K {fmt(r['pe'])}" + (f" (sektör {fmt(spe)}, kendi ortalaması {fmt(hpe)})" if spe or hpe else ""))
    else: notes.append("son 12 ayda zarar ettiği için F/K hesaplanamıyor")
    if r["pb"]: notes.append(f"PD/DD {fmt(r['pb'], 2)}" + (f" (sektör {fmt(spb, 2)}, kendi ortalaması {fmt(hpb, 2)})" if spb or hpb else ""))
    verdict = None
    if up is not None:
        verdict = "ucuz" if up >= 20 else "makul" if up > -15 else "pahalı"
    out[c] = {"market": r["market"], "name": r.get("name"), "sector": TR_SECTOR.get(r.get("sector"), r.get("sector")), "industry": r.get("industry"),
              "price": round(price, 4), "priceDate": r["priceDate"], "period": r["period"], "group": r.get("group"),
              "mcap": round(r["mcap"]), "netTTM": round(net) if net is not None else None, "equity": round(eq) if eq is not None else None,
              "eps": r["eps"] and round(r["eps"], 4), "bvps": r["bvps"] and round(r["bvps"], 4),
              "pe": r["pe"] and round(r["pe"], 2), "pb": r["pb"] and round(r["pb"], 2),
              "sectorPE": spe and round(spe, 2), "sectorPB": spb and round(spb, 2), "peerLevel": lvl, "peers": ps[:12],
              "histPE": hpe and round(hpe, 2), "histPB": hpb and round(hpb, 2), "histN": max(len(r["histPE"]), len(r["histPB"])), "histFrom": r["histFrom"],
              "fair": {k: (round(v, 2) if v else None) for k, v in m.items()},
              "fairMid": mid and round(mid, 2), "fairLow": vals and round(min(vals), 2) or None, "fairHigh": vals and round(max(vals), 2) or None,
              "upside": up and round(up, 1), "verdict": verdict, "cur": cur, "note": "; ".join(notes + ([r["flag"]] if r.get("flag") else []))}

json.dump({"generatedAt": now.isoformat(timespec="seconds"), "method": "sektör çarpanları + kendi geçmişi (2023 sonu →) + Graham; ortalama",
           "grahamBIST": {"pe": _bpe and round(_bpe, 2), "pb": _bpb and round(_bpb, 2), "k": round(GK_BIST, 2)}, "thresholds": {"ucuz": ">= +20%", "makul": "-15% .. +20%", "pahalı": "<= -15%"},
           "items": out, "errors": ERR}, open("valuation.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(out)} şirket değerlendi; hata: {len(ERR)}")
