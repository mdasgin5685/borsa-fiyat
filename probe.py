"""Geçici: veri kaynaklarının alanlarını görmek için."""
import json, urllib.request, datetime as dt
out = {}
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
def get(url):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")
        try: return json.loads(raw)
        except Exception: return {"_raw": raw[:1500]}
    except Exception as e: return {"_err": str(e)[:200]}
B = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/"
out["hisse"] = get(B + "HisseTekil?hisse=THYAO&startdate=01-10-2026&enddate=04-11-2026")
if isinstance(out["hisse"], dict) and out["hisse"].get("value"): out["hisse"]["value"] = out["hisse"]["value"][-1:]
for code, grp in (("THYAO", "XI_29"), ("AKBNK", "UFRS"), ("AKBNK", "UFRS_K"), ("ANHYT", "XI_29")):
    r = get(B + f"MaliTablo?companyCode={code}&exchange=TRY&financialGroup={grp}&year1=2026&period1=6&year2=2026&period2=3&year3=2025&period3=12&year4=2025&period4=9")
    if isinstance(r, dict) and r.get("value"): r["n"] = len(r["value"]); r["value"] = r["value"]
    out[f"mali_{code}_{grp}"] = r
for p in ("https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx?hisse=THYAO",):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(p, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8", "replace")
        import re
        out["kart_len"] = len(raw)
        for k in ("Sermaye", "Fiyat/Kazanç", "F/K", "PD/DD", "Halka Açıklık", "Sektör"):
            i = raw.find(k); out["kart_" + k] = raw[max(0, i - 200): i + 400] if i >= 0 else None
    except Exception as e: out["kart_err"] = str(e)[:200]
try:
    import yfinance as yf
    t = yf.Ticker("AAPL"); inf = t.info
    out["yf_info_keys"] = {k: inf.get(k) for k in ("sector", "industry", "trailingEps", "forwardEps", "bookValue", "trailingPE", "priceToBook", "sharesOutstanding", "dividendRate", "targetMeanPrice", "returnOnEquity", "currentPrice")}
    q = t.quarterly_income_stmt; out["yf_q_income_rows"] = list(q.index)[:40]; out["yf_q_cols"] = [str(c)[:10] for c in q.columns]
    b = t.quarterly_balance_sheet; out["yf_q_bal_rows"] = list(b.index)[:60]
    a = t.income_stmt; out["yf_a_cols"] = [str(c)[:10] for c in a.columns]
    ti = yf.Ticker("THYAO.IS").info; out["yf_thyao"] = {k: ti.get(k) for k in ("sector", "industry", "trailingEps", "bookValue", "trailingPE", "priceToBook", "sharesOutstanding", "currentPrice")}
except Exception as e: out["yf_err"] = str(e)[:300]
json.dump(out, open("probe.json", "w"), ensure_ascii=False, indent=1, default=str)
