"""OzSkies background alerts: every run, check each watched place for major threats and
push new ones to that place's ntfy channel (https://ntfy.sh/ozskies-nn212-<place>)."""
import json, math, os, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

SITE = "https://nuggetnut212.github.io/Weather-/"
PREFIX = "ozskies-nn212-"
UA = {"User-Agent": "OzSkies alerts (github.com/NuggetNut212/Weather-)"}
now = datetime.now(timezone.utc)

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
        return json.loads(r.read())

def load(path, default):
    try: return json.load(open(path))
    except Exception: return default

def km(a, b, c, d):
    r = math.pi / 180; x = (c - a) * r; y = (d - b) * r
    h = math.sin(x / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin(y / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))

def when(ts, tz):
    t = datetime.fromtimestamp(ts, timezone.utc) + timedelta(seconds=tz)
    loc_now = now + timedelta(seconds=tz)
    part = "overnight" if t.hour < 6 else "morning" if t.hour < 12 else "afternoon" if t.hour < 18 else "evening"
    if t.date() == loc_now.date(): day = "today"
    elif t.date() == (loc_now + timedelta(days=1)).date(): day = "tomorrow"
    else: day = t.strftime("%A")
    return "tonight" if day == "today" and part == "evening" else day + " " + part

watch = load("data/watch.json", [])
sent = load("data/sent.json", {})
sent = {k: v for k, v in sent.items() if datetime.fromisoformat(v) > now - timedelta(days=3)}
fires = load("data/fires.json", {})
rfs = load("data/rfs.json", {})
alerts = []  # (place, key, title, message, priority, tags)

# ---- Weather threats from forecast models (next 36 hours) ----
if watch:
    q = urllib.parse.urlencode({
        "latitude": ",".join(str(w["lat"]) for w in watch), "longitude": ",".join(str(w["lon"]) for w in watch),
        "hourly": "weather_code,cape,lifted_index,wind_gusts_10m,precipitation", "timezone": "auto",
        "timeformat": "unixtime", "forecast_days": 3})
    try:
        res = get_json("https://api.open-meteo.com/v1/forecast?" + q)
        res = res if isinstance(res, list) else [res]
    except Exception as e:
        print("weather error", e); res = []
    for w, o in zip(watch, res):
        h = o.get("hourly") or {}; tz = o.get("utc_offset_seconds", 36000)
        t0 = now.timestamp(); storm = hail = gust = None; rain = 0; burst = None
        for i, t in enumerate(h.get("time", [])):
            if t < t0 - 3600 or t > t0 + 36 * 3600: continue
            cape = (h.get("cape") or [None])[i]; li = (h.get("lifted_index") or [None])[i]
            wc = (h.get("weather_code") or [None])[i]; g = (h.get("wind_gusts_10m") or [None])[i]; pr = (h.get("precipitation") or [0])[i] or 0
            if cape is not None and li is not None and cape >= 2500 and li <= -5 and not storm: storm = t
            if wc in (96, 99) and not hail: hail = t
            if g is not None and g >= 90 and (not gust or g > gust[0]): gust = (g, t)
            rain += pr
            if pr >= 30 and not burst: burst = (pr, t)
        day = lambda t: (datetime.fromtimestamp(t, timezone.utc) + timedelta(seconds=tz)).strftime("%Y-%m-%d")
        if hail: alerts.append((w, "hail|" + day(hail), "Hail possible at " + w["name"], "Thunderstorms with hail are forecast " + when(hail, tz) + ". Protect cars and outdoor gear, and keep an eye on Bureau warnings.", 5, "cloud_with_lightning_and_rain,warning"))
        elif storm: alerts.append((w, "storm|" + day(storm), "Severe thunderstorms possible at " + w["name"], "Very unstable air " + when(storm, tz) + " could produce severe storms with heavy rain, hail or damaging winds.", 4, "cloud_with_lightning,warning"))
        if gust: alerts.append((w, "wind|" + day(gust[1]), "Damaging winds at " + w["name"], "Gusts to about %d km/h %s. Secure loose items and avoid trees and powerlines." % (gust[0], when(gust[1], tz)), 4, "dash,warning"))
        if rain >= 80 or burst: alerts.append((w, "rain|" + day(t0), "Heavy rain at " + w["name"], "About %d mm in the next 36 hours%s. Flash flooding is possible. Never drive through floodwater." % (rain, (", with bursts of %d mm in an hour %s" % (burst[0], when(burst[1], tz))) if burst else ""), 4, "cloud_with_rain,warning"))

# ---- Bushfires: Watch and Act / Emergency Warning within 50 km ----
for x in fires.get("incidents", []):
    cat = str(x.get("category", ""))
    lvl = 3 if re.search("emergency", cat, re.I) else 2 if re.search("watch", cat, re.I) else 0
    if lvl < 2: continue
    for w in watch:
        d = km(w["lat"], w["lon"], x["lat"], x["lon"])
        if d > 50: continue
        alerts.append((w, "fire|%s|%s" % (x.get("title"), cat), ("EMERGENCY WARNING: " if lvl == 3 else "Watch and Act: ") + x.get("title", "Bushfire"),
                       "%s, %d km from %s.%s%s %s" % (x.get("type") or "Fire", d, w["name"], (" Status: " + x["status"] + ".") if x.get("status") else "", (" Size: " + x["size"] + ".") if x.get("size") else "",
                       "You may be in danger. Act immediately and follow emergency services advice." if lvl == 3 else "Conditions are changing. Start taking action to protect yourself."),
                       5 if lvl == 3 else 4, "fire,rotating_light" if lvl == 3 else "fire,warning"))

# ---- NSW fire danger: Extreme / Catastrophic or Total Fire Ban ----
def normc(t): return re.sub(r"[^a-z]", "", re.sub(r"\b(shire|city|council|municipal|regional|of|the)\b", "", str(t or "").lower()))
for w in watch:
    if w.get("state") != "NSW" or not rfs.get("districts"): continue
    if "lga" not in w:
        try:
            g = get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": w["name"], "count": 10, "country_code": "AU"}))
            best = min((r for r in g.get("results", []) if "New South Wales" in str(r.get("admin1", ""))), key=lambda r: km(w["lat"], w["lon"], r["latitude"], r["longitude"]), default=None)
            w["lga"] = best.get("admin2", "") if best and km(w["lat"], w["lon"], best["latitude"], best["longitude"]) < 40 else ""
        except Exception:
            w["lga"] = ""
    n = normc(w.get("lga"))
    if not n: continue
    dist = next((d for d in rfs["districts"] if any(c and (c == n or c.startswith(n) or n.startswith(c)) for c in map(normc, re.split(r"[;,]", str(d.get("Councils", "")))))), None)
    if not dist: continue
    for day, rk, bk in (("today", "DangerLevelToday", "FireBanToday"), ("tomorrow", "DangerLevelTomorrow", "FireBanTomorrow")):
        rating = str(dist.get(rk, "")).upper(); ban = str(dist.get(bk, "")).lower() in ("yes", "true")
        stamp = (now + timedelta(hours=10) + (timedelta(days=1) if day == "tomorrow" else timedelta())).strftime("%Y-%m-%d")
        if rating in ("EXTREME", "CATASTROPHIC"):
            alerts.append((w, "fdr|%s|%s" % (stamp, rating), "%s fire danger %s: %s" % (rating.title(), day, w["name"]),
                           ("For your survival, leave bush fire risk areas." if rating == "CATASTROPHIC" else "Take action now to protect your life and property.") + " (" + dist.get("Name", "") + " fire area, NSW RFS)",
                           5 if rating == "CATASTROPHIC" else 4, "fire,warning"))
        if ban:
            alerts.append((w, "toban|" + stamp, "Total Fire Ban %s: %s" % (day, w["name"]),
                           "A Total Fire Ban is in place for the %s fire area. No fires in the open, including wood and charcoal barbecues." % dist.get("Name", ""), 4, "no_entry,fire"))

# ---- Send new alerts ----
count = 0
for w, key, title, msg, prio, tags in alerts:
    k = w["id"] + "|" + key
    if k in sent: continue
    req = urllib.request.Request("https://ntfy.sh/" + PREFIX + w["id"], data=msg.encode("utf-8"), method="POST",
        headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Priority": str(prio), "Tags": tags, "Click": SITE + "#" + w["id"], "User-Agent": UA["User-Agent"]})
    try:
        urllib.request.urlopen(req, timeout=30).read(); sent[k] = now.isoformat(); count += 1
    except Exception as e:
        print("send failed", k, e)
json.dump(sent, open("data/sent.json", "w"), indent=0)
json.dump(watch, open("data/watch.json", "w"), indent=1)
print("alerts found", len(alerts), "sent", count)
