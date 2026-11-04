# Kullanım: python3 job.py <db_klasörü> <prices.json> <mod: ogle|aksam>
import json, glob, sys, os, datetime as dt
from zoneinfo import ZoneInfo
D, PJ, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
TZ = ZoneInfo("Europe/Istanbul"); today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
def load(p):
    x = json.load(open(p)); return x.get("data", x) if isinstance(x, dict) and "data" in x else x
T = [load(f) for f in glob.glob(f"{D}/trades/*.json")]
for f in glob.glob(f"{D}/tradeBatches/*.json"): T += load(f).get("trades", [])
G = json.load(open(PJ)); Q = G["quotes"]
OLD = load(f"{D}/meta/prices.json") if os.path.exists(f"{D}/meta/prices.json") else {}
qty, mk = {}, {}
for t in T:
    s = t["symbol"]; qty[s] = qty.get(s, 0) + (t["qty"] if t["side"] == "buy" else -t["qty"]); mk[s] = t["market"]
openp = {s: q for s, q in qty.items() if abs(q) > 1e-9}
bist_days = sorted({v["d"] for k, v in Q.items() if v.get("market") != "US"})
latest = bist_days[-1] if bist_days else None
TD = latest or today  # fiyatların ait olduğu seans günü
todays = [t for t in T if t.get("date") == TD]
need = set(openp) | {t["symbol"] for t in todays}
quotes, missing, stale = {}, [], []
for s in need:
    if s == "GRAM-ALTIN": continue
    v = Q.get(s)
    old = (OLD.get("quotes") or {}).get(s, {})
    if old.get("source") in ("elle", "kullanıcı") and old.get("d") == today:
        continue  # kullanıcının bugün elle girdiği fiyat korunur
    if not v: missing.append(s); continue
    if mk.get(s) != "US" and latest and v["d"] < latest: stale.append(s)
    quotes[s] = {"price": v["price"], "prevClose": v["prevClose"], "changePct": round((v["price"] / v["prevClose"] - 1) * 100, 2),
                 "d": v["d"], "source": f'{v["d"][8:10]}.{v["d"][5:7]} · {v["src"]} (otomatik)'}
upd = {"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "quotes": quotes,
       "source": f'GitHub fiyat toplayıcı ({G["generatedAt"][:16]}): BIST İş Yatırım/Yahoo, ABD/kur/altın Yahoo'}
if G.get("funds"):  # BES fon fiyatları (TEFAS)
    upd["funds"] = {c: {"price": f["price"], "prevClose": f["prevClose"], "d": f["d"], "name": f.get("name", "")} for c, f in G["funds"].items()}
for k in ("usdtry", "xu100", "spx"):
    if k in G: upd[k] = round(G[k], 4)
if "xu100_prevClose" in G: upd["xu100_changePct"] = round((G["xu100"] / G["xu100_prevClose"] - 1) * 100, 2)
if "spx_prevClose" in G: upd["spx_changePct"] = round((G["spx"] / G["spx_prevClose"] - 1) * 100, 2)
gold_ok = G.get("gold_src") == "spot"
if gold_ok and "gold_gram_try" in G:
    upd.update(gold_gram_try=G["gold_gram_try"], gold_prevClose=G["gold_prevClose"], gold_d=today,
               gold_changePct=round((G["gold_gram_try"] / G["gold_prevClose"] - 1) * 100, 2))
# günlük K/Z
def px(s):
    if s == "GRAM-ALTIN": return (upd.get("gold_gram_try") or OLD.get("gold_gram_try"), upd.get("gold_prevClose") or OLD.get("gold_prevClose"), today)
    q = quotes.get(s) or (OLD.get("quotes") or {}).get(s) or {}
    return q.get("price"), q.get("prevClose"), q.get("d")
usd = upd.get("usdtry") or OLD.get("usdtry") or 0
day, by, val = 0.0, {}, 0.0
for s in need:
    p, pc, d = px(s)
    if not p: continue
    q_now = openp.get(s, 0); q0, cash = q_now, 0.0
    for t in todays:
        if t["symbol"] != s or t.get("bonus"): continue
        if t["side"] == "buy": q0 -= t["qty"]; cash -= t["qty"] * (t.get("price") or 0)
        else: q0 += t["qty"]; cash += t["qty"] * (t.get("price") or 0)
    fx = usd if mk.get(s) == "US" else 1
    if mk.get(s) == "US" and d != TD: pc = p
    v = (q_now * p - max(0, q0) * (pc or p) + cash) * fx
    by[s] = round(v, 2); day += v; val += q_now * p * fx
res = {"update": upd, "missing": missing, "stale_bist": stale, "gold_from_yahoo_ok": gold_ok,
       "day_TL": round(day, 2), "positions_value_TL": round(val, 2),
       "top": sorted(by.items(), key=lambda x: x[1], reverse=True)[:2], "bottom": sorted(by.items(), key=lambda x: x[1])[:2],
       "xu100_changePct": upd.get("xu100_changePct"), "generatedAt": G["generatedAt"], "latest_bist_day": latest, "bes_funds": len(G.get("funds") or {})}
if MODE == "aksam":
    pts = (load(f"{D}/meta/history.json") or {}).get("points", []) if os.path.exists(f"{D}/meta/history.json") else []
    qmap = {s: px(s)[0] for s in openp if px(s)[0]}
    point = {"d": latest or today, "v": round(val), "usdtry": usd, "gold": px("GRAM-ALTIN")[0], "xu100": upd.get("xu100"), "spx": upd.get("spx"), "q": qmap}
    pts = [p for p in pts if p["d"] != point["d"]] + [point]; pts.sort(key=lambda p: p["d"])
    json.dump({"points": pts}, open("history_update.json", "w"))
    res["history_file"] = os.path.abspath("history_update.json")
json.dump(upd, open("prices_update.json", "w"), ensure_ascii=False)
res["prices_file"] = os.path.abspath("prices_update.json")
del res["update"]
print(json.dumps(res, ensure_ascii=False, indent=1))
