"""Locais candidatos (hotéis, supermercados, restauração, parques, empresas, concessionários) e postos
do OpenStreetMap, por zona, no formato que a ferramenta GO-CHARGE Prospect importa ("go-charge-sync").

Mesma lógica do ficheiro auxiliar go-charge-sync-helper.html: cada zona é dividida numa grelha 3×3 e as
parcelas são consultadas uma a uma ao Overpass, com pausa entre pedidos e servidor de reserva.

Uso:
  python scripts/osm_sync.py                 # todas as zonas
  python scripts/osm_sync.py "Algarve"       # só uma zona
Saída: data/osm/<zona>.json   ·   Dados © colaboradores do OpenStreetMap (ODbL)
"""
import json, os, sys, time, unicodedata, re, datetime as dt
from pathlib import Path

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ZONES = {
    "Trás-os-Montes e Alto Douro": [40.85, -7.85, 42.15, -6.15],
    "Minho":                       [41.25, -8.95, 42.15, -7.75],
    "Porto e Douro Litoral":       [40.85, -8.95, 41.55, -7.70],
    "Beiras Interior":             [39.45, -7.85, 41.15, -6.75],
    "Centro e Beira Litoral":      [39.35, -9.05, 41.15, -7.75],
    "Lisboa e Vale do Tejo":       [38.55, -9.55, 39.75, -7.95],
    "Alentejo":                    [36.95, -8.75, 39.65, -6.90],
    "Algarve":                     [36.90, -9.05, 37.55, -7.35],
}
TYPES = ["Hotel", "Supermercado", "Restaurante", "Parque", "Empresa", "Concessionário"]
PARKING_ESTIMATE = {"Hotel": 55, "Supermercado": 90, "Empresa": 75, "Restaurante": 25, "Parque": 120, "Concessionário": 45}
OUT = Path(os.environ.get("DATA_DIR", "data")) / "osm"
UA = "go-charge-dados/1.0 (prospecao de mercado; recolha semanal)"
PAUSE = 3


def slug(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

def split_bbox(b, n=3):
    s, w, nn, e = b; dh, dw = (nn - s) / n, (e - w) / n
    return [[s + r * dh, w + c * dw, s + (r + 1) * dh, w + (c + 1) * dw] for r in range(n) for c in range(n)]

def bb(b): return ",".join(f"{v:.5f}" for v in b)

def q_stations(b): return f'[out:json][timeout:90];nwr["amenity"="charging_station"]({bb(b)});out center;'

def q_places(b):
    x = bb(b)
    return f"""[out:json][timeout:120];
(
  nwr["tourism"~"^(hotel|hostel|guest_house|motel|apartment)$"]["name"]({x});
  nwr["shop"~"^(supermarket|mall|department_store)$"]["name"]({x});
  nwr["amenity"~"^(restaurant|fast_food)$"]["name"]({x});
  nwr["amenity"="parking"]["access"!~"^(private|no|customers)$"]({x});
  nwr["office"]["name"]({x});
  nwr["shop"="car"]["name"]({x});
);
out center;"""

def overpass(query):
    import requests
    last = None
    for url in ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, timeout=200, headers={"User-Agent": UA})
            if r.status_code == 200:
                j = r.json()
                rem = j.get("remark") or ""
                if re.search(r"timed out|runtime error|out of memory", rem, re.I) and not j.get("elements"):
                    last = f"{url}: {rem}"; continue
                return j, url
            last = f"{url}: HTTP {r.status_code}"
            if r.status_code == 400: break
        except Exception as e:
            last = f"{url}: {e}"
        time.sleep(PAUSE)
    raise RuntimeError(last)

def capacity(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m and int(m.group()) < 100000 else None

def category(t):
    if t.get("tourism") in ("hotel", "hostel", "guest_house", "motel", "apartment"): return "Hotel"
    if t.get("shop") in ("supermarket", "mall", "department_store"): return "Supermercado"
    if t.get("shop") == "car": return "Concessionário"
    if t.get("amenity") in ("restaurant", "fast_food"): return "Restaurante"
    if t.get("amenity") == "parking": return "Parque"
    return "Empresa"

def url_ok(u):
    u = (u or "").strip()
    if not u: return ""
    if not re.match(r"^https?://", u): u = "https://" + u
    return u[:300]

def sync_zone(zone):
    bbox = ZONES[zone]; tiles = split_bbox(bbox)
    elements, servers, failures, ts, done = {}, set(), [], None, 0
    for i, q in enumerate([q_stations(bbox)] + [q_places(t) for t in tiles]):
        try:
            j, url = overpass(q)
            for el in j.get("elements", []):
                elements[f"{el['type']}:{el['id']}"] = el
            servers.add(url.split("/")[2]); done += 1
            tb = (j.get("osm3s") or {}).get("timestamp_osm_base")
            if tb and (not ts or tb > ts): ts = tb
        except Exception as e:
            failures.append(f"{'Postos' if i == 0 else 'Parcela ' + str(i)}: {e}"[:200])
        time.sleep(PAUSE)
    cands, stations = [], []
    for key, el in elements.items():
        t = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None: continue
        osm_url = f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
        if t.get("amenity") == "charging_station":
            stations.append({"id": f"osm:{el['type']}:{el['id']}", "osmType": el["type"], "osmId": el["id"],
                             "lat": round(lat, 6), "lon": round(lon, 6),
                             "tags": {k: str(v)[:200] for k, v in list(t.items())[:40]}, "osmUrl": osm_url})
        elif t.get("name") or t.get("brand") or t.get("operator"):
            ty = category(t); cap = capacity(t.get("capacity") or t.get("capacity:parking"))
            cands.append({"id": f"osm:{el['type']}:{el['id']}", "osmType": el["type"], "osmId": el["id"],
                          "name": str(t.get("name") or t.get("brand") or t.get("operator"))[:160], "type": ty,
                          "city": str(t.get("addr:city") or t.get("addr:town") or "")[:90],
                          "area": str(t.get("addr:suburb") or t.get("addr:place") or t.get("addr:street") or "")[:90],
                          "parking": cap if cap is not None else PARKING_ESTIMATE[ty],
                          "parkingSource": "osm" if cap is not None else "estimate",
                          "contact": str(t.get("operator") or "Por identificar")[:120],
                          "phone": str(t.get("phone") or t.get("contact:phone") or "")[:60],
                          "website": url_ok(t.get("website") or t.get("contact:website")),
                          "osmUrl": osm_url, "lat": round(lat, 6), "lon": round(lon, 6)})
    out = {"version": 1, "kind": "go-charge-sync", "zone": zone, "radius": 1, "categories": TYPES,
           "syncedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "servers": sorted(servers), "timestampOsmBase": ts, "tilesDone": done, "tilesTotal": len(tiles) + 1,
           "failures": failures, "candidates": cands, "stations": stations}
    if done == 0:
        raise RuntimeError(f"{zone}: nenhuma parcela respondeu — ficheiro anterior mantido")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{slug(zone)}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{zone}: {len(cands)} candidatos, {len(stations)} postos, parcelas {done}/{len(tiles)+1}")

def main(argv):
    zones = [z for z in ZONES if not argv or z in argv]
    errors = []
    for z in zones:
        try: sync_zone(z)
        except Exception as e: errors.append(str(e)); print("ERRO", e)
    if len(errors) == len(zones): sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1:])
