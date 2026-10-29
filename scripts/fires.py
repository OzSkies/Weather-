"""Collect current fire incidents from every Australian state fire service, plus
satellite hotspots, and save them to data/fires.json for OzSkies."""
import json, re, os, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

UA = {"User-Agent": "Mozilla/5.0 (OzSkies weather site; github.com/NuggetNut212/Weather-)", "Accept": "*/*"}

def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()

def strip(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(t or ""))).strip()

def pick(p, keys):
    low = {k.lower(): v for k, v in p.items()}
    for k in keys:
        v = low.get(k.lower())
        if isinstance(v, dict):
            v = v.get("name") or v.get("value") or v.get("title") or ""
        if isinstance(v, list):
            v = ", ".join(str(x.get("name", x) if isinstance(x, dict) else x) for x in v)
        if v not in (None, "", [], {}):
            return strip(v) if not isinstance(v, (int, float)) else v
    return ""

def centre(g):
    if not g: return None
    t = g.get("type"); c = g.get("coordinates")
    if t == "Point": return c[:2]
    if t == "MultiPoint": return c[0][:2]
    if t == "Polygon": ring = c[0]
    elif t == "MultiPolygon": ring = c[0][0]
    elif t == "LineString": ring = c
    elif t == "GeometryCollection":
        for x in g.get("geometries", []):
            if x.get("type") == "Point": return x["coordinates"][:2]
        for x in g.get("geometries", []):
            r = centre(x)
            if r: return r
        return None
    else: return None
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    return [sum(xs) / len(xs), sum(ys) / len(ys)]

LEVEL = re.compile(r"emergency[ _-]warning|watch[ _-]and[ _-]act|advice", re.I)

def norm(state, p, xy, link=""):
    title = pick(p, ["title", "sourceTitle", "WarningTitle", "name", "incidentName", "IncidentName", "Location_name", "location", "Locality", "suburb", "Header"])
    cat = pick(p, ["category", "WarningLevel", "warningLevel", "alertLevel", "AlertLevel", "Level", "action", "category1", "cap:severity"])
    blob = " ".join(str(v) for v in p.values() if isinstance(v, (str, int, float)))
    m = LEVEL.search(str(cat)) or LEVEL.search(blob)
    level = re.sub(r"[_-]", " ", m.group(0)).title().replace("And", "and") if m else (str(cat) or "Incident")
    typ = pick(p, ["type", "Type", "incidentType", "category2", "eventType", "IncidentType", "feedType"])
    blob_l = (str(typ) + " " + str(title) + " " + blob).lower()
    if not any(w in blob_l for w in ("fire", "burn", "smoke", "bush", "grass", "scrub")):
        return None
    if len(str(title)) < 7:
        extra = pick(p, ["area", "suburbs", "suburb", "Locality", "location"])
        if extra and not re.match(r"^-?\d", str(extra)): title = (title or "Fire") + " - " + str(extra)
    x, y = float(xy[0]), float(xy[1])
    if abs(x) > 180 or abs(y) > 90:
        import math
        x = x / 6378137.0 * 180 / math.pi
        y = (2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2) * 180 / math.pi
    xy = [x, y]
    if not (-45 < y < -9 and 110 < x < 160):
        return None
    desc = strip(p.get("description", "")) if isinstance(p.get("description"), str) else ""
    def dfield(name):
        m = re.search(name + r"\s*:\s*(.*?)(?=\s+[A-Z][A-Z /]{2,}\s*:|$)", desc)
        return m.group(1).strip() if m else ""
    res = {
        "state": state, "title": title or "Fire", "category": level,
        "status": pick(p, ["status", "Status", "CurrentStatus", "incidentStatus", "statusDescription"]),
        "type": typ, "size": pick(p, ["size", "sizeFmt", "Size", "area", "AreaBurnt", "fireSize"]),
        "location": pick(p, ["location", "Location_name", "Locality", "locality", "suburb", "suburbs", "area", "address"]),
        "council": pick(p, ["council", "lga", "LGA", "councilArea"]),
        "agency": pick(p, ["agency", "sourceOrg", "Agency", "responsibleAgency", "controlAgency"]),
        "updated": pick(p, ["updated", "PublishDateLocal_ISO", "lastUpdated", "updatedTime", "pubDate", "Date", "created"]),
        "lat": round(float(xy[1]), 5), "lon": round(float(xy[0]), 5), "link": link,
    }
    if desc:
        for key, label in (("status", "STATUS"), ("type", "TYPE"), ("size", "SIZE"), ("location", "LOCATION"),
                           ("council", "COUNCIL AREA"), ("agency", "RESPONSIBLE AGENCY"), ("updated", "UPDATED")):
            if not res.get(key):
                res[key] = dfield(label)
    return res

def from_geojson(state, data):
    out = []
    feats = data.get("features", []) if isinstance(data, dict) else []
    for f in feats:
        xy = centre(f.get("geometry"))
        if not xy: continue
        n = norm(state, f.get("properties") or {}, xy, (f.get("properties") or {}).get("link", "") or (f.get("properties") or {}).get("url", ""))
        if n: out.append(n)
    return out

def from_wa(state, data):
    out = []
    items = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list): items.extend(v)
    for it in items:
        if not isinstance(it, dict): continue
        gs = it.get("geo-source") or {}
        feats = gs.get("features") or []
        xy = centre(feats[0].get("geometry")) if feats else None
        if not xy: continue
        p = {k: v for k, v in it.items() if k != "geo-source"}
        icon = (feats[0].get("properties") or {}).get("icon-name", "")
        p["_icon"] = icon
        n = norm(state, p, xy, "https://www.emergency.wa.gov.au/")
        if n: out.append(n)
    return out

def from_list(state, data):
    out = []
    items = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []
    for p in items:
        if not isinstance(p, dict): continue
        lat = pick(p, ["lat", "latitude", "Latitude", "y"]); lon = pick(p, ["lon", "lng", "long", "longitude", "Longitude", "x"])
        if lat == "" or lon == "":
            loc = str(p.get("Location") or p.get("location") or "")
            m = re.match(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", loc)
            if not m: continue
            lat, lon = m.group(1), m.group(2)
        try:
            n = norm(state, p, [float(lon), float(lat)])
        except Exception:
            continue
        if n: out.append(n)
    return out

def from_georss(state, raw):
    out = []
    root = ET.fromstring(raw)
    for it in root.iter():
        if not it.tag.lower().endswith(("item", "entry")): continue
        p = {}; xy = None
        for c in it:
            tag = c.tag.split("}")[-1].lower()
            if tag == "point" and c.text:
                a = c.text.split(); xy = [float(a[1]), float(a[0])]
            elif tag in ("lat", "latitude") and c.text: p["_lat"] = c.text
            elif tag in ("long", "lon", "longitude") and c.text: p["_lon"] = c.text
            else: p[tag] = c.text or ""
        if not xy and "_lat" in p: xy = [float(p["_lon"]), float(p["_lat"])]
        if not xy: continue
        n = norm(state, p, xy, p.get("link", ""))
        if n: out.append(n)
    return out

SOURCES = [
    ("NSW", "https://www.rfs.nsw.gov.au/feeds/majorIncidents.json", "geojson"),
    ("VIC", "https://emergency.vic.gov.au/public/events-geojson.json", "geojson"),
    ("VIC", "https://emergency.vic.gov.au/public/osom-geojson.json", "geojson"),
    ("QLD", "https://publiccontent-gis-psba-qld-gov-au.s3.amazonaws.com/content/Feeds/BushfireCurrentIncidents/bushfireAlert.json", "geojson"),
    ("WA", "https://api.emergency.wa.gov.au/v1/incidents", "wa"),
    ("WAW", "https://api.emergency.wa.gov.au/v1/warnings", "wa"),
    ("TAS", "https://alert.tas.gov.au/data/data.geojson", "geojson"),
    ("TAS", "https://alert.tas.gov.au/data/incidents-and-alerts.xml", "georss"),
    ("ACT", "https://www.esa.act.gov.au/feeds/currentincidents.xml", "georss"),
]

out = {"incidents": [], "hotspots": [], "sources": {}, "errors": []}
done = set()
DEBUG = {}
for state, url, kind in SOURCES:
    if state in done: continue
    try:
        raw = get(url)
        if kind == "georss": items = from_georss(state, raw)
        else:
            data = json.loads(raw)
            if kind == "wa": items = from_wa(state, data)
            else: items = from_geojson(state, data) if (kind == "geojson" and isinstance(data, dict) and "features" in data) else from_list(state, data)
        for i in items: i["state"] = "WA" if state == "WAW" else i["state"]
        out["incidents"].extend(items); out["sources"][state] = {"url": url, "count": len(items)}
        if items: done.add(state)
        try:
            smp = data if kind != "georss" else None
            if isinstance(smp, dict) and smp.get("features"): smp = smp["features"][0]
            elif isinstance(smp, dict): smp = {k: (str(v)[:300]) for k, v in list(smp.items())[:12]}
            elif isinstance(smp, list): smp = smp[0] if smp else []
            DEBUG[state + " " + url] = json.dumps(smp)[:1500] if smp is not None else raw[:1500].decode("utf-8", "replace")
        except Exception as e:
            DEBUG[state + " " + url] = "debug err %s" % e
    except Exception as e:
        out["errors"].append("%s %s: %s" % (state, url, str(e)[:120]))

# Satellite hotspots (Geoscience Australia / Digital Earth Australia)
try:
    hs = json.loads(get("https://hotspots.dea.ga.gov.au/data/recent-hotspots.json", 120))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pts = []
    for f in hs.get("features", []):
        p = f.get("properties") or {}; xy = centre(f.get("geometry"))
        if not xy: continue
        t = str(p.get("datetime") or p.get("start_dt") or "")
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff: continue
        except Exception:
            pass
        conf = p.get("confidence")
        try:
            if conf is not None and float(conf) < 50: continue
        except Exception:
            pass
        pts.append([round(xy[1], 3), round(xy[0], 3), t[:16]])
    seen = set(); slim = []
    for p in pts:
        k = (round(p[0], 2), round(p[1], 2))
        if k in seen: continue
        seen.add(k); slim.append(p)
    out["hotspots"] = slim[:4000]; out["sources"]["hotspots"] = {"count": len(slim)}
except Exception as e:
    out["errors"].append("hotspots: %s" % str(e)[:120])

json.dump(DEBUG, open("data/fires_debug.json", "w"), indent=1) if os.path.isdir("data") else None
OUT = "data/fires.json"
old = None
if os.path.exists(OUT):
    try: old = json.load(open(OUT))
    except Exception: old = None
if old is not None and {k: v for k, v in old.items() if k != "checked"} == out:
    print("No change"); sys.exit(0)
out["checked"] = datetime.now(timezone.utc).isoformat()
os.makedirs("data", exist_ok=True)
json.dump(out, open(OUT, "w"), separators=(",", ":"))
print("Saved", len(out["incidents"]), "incidents,", len(out["hotspots"]), "hotspots; sources", out["sources"], "errors", out["errors"])
