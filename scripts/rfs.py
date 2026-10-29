"""Fetch official NSW RFS fire danger ratings, total fire bans and major incidents,
and save a slim copy to data/rfs.json for the OzSkies site to read."""
import json, re, os, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = {"User-Agent": "OzSkies weather site (github.com/NuggetNut212/Weather-)"}
OUT = "data/rfs.json"

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def strip(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()

out = {"source": "NSW Rural Fire Service", "districts": [], "incidents": [], "errors": []}

try:
    root = ET.fromstring(get("https://www.rfs.nsw.gov.au/feeds/fdrToban.xml"))
    for el in root.iter():
        if el.tag.lower().endswith("district"):
            d = {c.tag: (c.text or "").strip() for c in el}
            if d:
                out["districts"].append(d)
except Exception as e:
    out["errors"].append("fdr: %s" % e)

try:
    j = json.loads(get("https://www.rfs.nsw.gov.au/feeds/majorIncidents.json"))
    for f in j.get("features", []):
        p = f.get("properties", {}) or {}
        g = f.get("geometry") or {}
        pt = None
        if g.get("type") == "Point":
            pt = g.get("coordinates")
        elif g.get("type") == "GeometryCollection":
            for x in g.get("geometries", []):
                if x.get("type") == "Point":
                    pt = x.get("coordinates")
        if not pt:
            continue
        desc = strip(p.get("description", ""))
        def field(name):
            m = re.search(name + r"\s*:\s*(.*?)(?=\s+[A-Z][A-Z /]{2,}\s*:|$)", desc)
            return m.group(1).strip() if m else ""
        out["incidents"].append({
            "title": p.get("title", ""), "category": p.get("category", ""),
            "link": p.get("link", ""), "pubDate": p.get("pubDate", ""),
            "lon": pt[0], "lat": pt[1],
            "status": field("STATUS"), "type": field("TYPE"), "size": field("SIZE"),
            "location": field("LOCATION"), "updated": field("UPDATED"),
        })
except Exception as e:
    out["errors"].append("incidents: %s" % e)

old = None
if os.path.exists(OUT):
    try:
        old = json.load(open(OUT))
    except Exception:
        old = None
same = old is not None and {k: v for k, v in old.items() if k != "checked"} == out
out["checked"] = datetime.now(timezone.utc).isoformat()
if same:
    print("No change")
    sys.exit(0)
os.makedirs("data", exist_ok=True)
json.dump(out, open(OUT, "w"), separators=(",", ":"))
print("Updated: %d districts, %d incidents, errors=%s" % (len(out["districts"]), len(out["incidents"]), out["errors"]))
