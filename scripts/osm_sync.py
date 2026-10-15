"""Locais candidatos (hotéis, supermercados, restauração, parques, empresas, concessionários) e postos
do OpenStreetMap, por zona, no formato que a ferramenta GO-CHARGE Prospect importa ("go-charge-sync").

Mesma lógica do ficheiro auxiliar go-charge-sync-helper.html: cada zona é dividida numa grelha 3×3 e as
parcelas são consultadas uma a uma ao Overpass, com pausa entre pedidos e servidor de reserva.
Robustez (o Overpass público falha muito com HTTP 500/504 e timeouts):
  - cada pedido tenta todos os servidores em várias rondas, com espera crescente;
  - uma parcela que continue a falhar é dividida em 4 e as partes são pedidas uma a uma;
  - o que mesmo assim falhar mantém os dados da recolha anterior para essa área (nunca se perde
    o que já tinha sido recolhido); o ficheiro indica quantos locais vieram da recolha anterior.

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
ROUNDS = int(os.environ.get("OSM_ROUNDS", "3"))      # rondas por pedido (cada ronda tenta todos os servidores)
ROUND_WAIT = int(os.environ.get("OSM_ROUND_WAIT", "30"))
SPLIT_DEPTH = int(os.environ.get("OSM_SPLIT_DEPTH", "1"))   # parcela que falha → 4 partes
DEADLINE = time.time() + 60 * float(os.environ.get("OSM_DEADLINE_MIN", "270"))   # depois disto não se fazem mais pedidos


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
    last = None
    for rnd in range(ROUNDS):
        if time.time() > DEADLINE: raise RuntimeError(last or "tempo limite da recolha atingido")
        if rnd: time.sleep(ROUND_WAIT * rnd)
        try: return overpass_once(query)
        except RuntimeError as e:
            last = str(e)
            if "HTTP 400" in last: break
    raise RuntimeError(last)

def overpass_once(query):
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

def inside(lat, lon, b): return b[0] <= lat < b[2] and b[1] <= lon < b[3]

def sync_zone(zone):
    bbox = ZONES[zone]; tiles = split_bbox(bbox)
    elements, servers, failures, ts, done = {}, set(), [], None, 0
    missing_places, missing_stations = [], []      # áreas que não responderam

    def run(q, label):
        nonlocal ts
        j, url = overpass(q)
        for el in j.get("elements", []):
            elements[f"{el['type']}:{el['id']}"] = el
        servers.add(url.split("/")[2])
        tb = (j.get("osm3s") or {}).get("timestamp_osm_base")
        if tb and (not ts or tb > ts): ts = tb
        time.sleep(PAUSE)

    def fetch(b, qf, label, depth, missing):
        """Pede a área b; se falhar, divide em 4 e tenta as partes. Devolve True se a área ficou toda coberta."""
        try:
            run(qf(b), label); return True
        except Exception as e:
            if depth >= SPLIT_DEPTH:
                failures.append(f"{label}: {e}"[:200]); missing.append(b); time.sleep(PAUSE); return False
            print(f"  {zone} · {label}: falhou ({str(e)[:80]}) — a dividir em 4")
            ok = [fetch(sb, qf, f"{label}.{k+1}", depth + 1, missing) for k, sb in enumerate(split_bbox(b, 2))]
            return all(ok)

    if fetch(bbox, q_stations, "Postos", 0, missing_stations): done += 1
    for i, t in enumerate(tiles):
        if fetch(t, q_places, f"Parcela {i+1}", 0, missing_places): done += 1
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
    # áreas sem resposta: manter o que a recolha anterior tinha lá (nunca perder dados já recolhidos)
    kept = 0
    prev_file = OUT / f"{slug(zone)}.json"
    if (missing_places or missing_stations) and prev_file.exists():
        try:
            prev = json.loads(prev_file.read_text(encoding="utf-8"))
            have = {c["id"] for c in cands} | {s["id"] for s in stations}
            for c in prev.get("candidates", []):
                if c.get("id") not in have and any(inside(c["lat"], c["lon"], b) for b in missing_places):
                    cands.append(c); have.add(c["id"]); kept += 1
            for s in prev.get("stations", []):
                if s.get("id") not in have and any(inside(s["lat"], s["lon"], b) for b in missing_stations):
                    stations.append(s); have.add(s["id"]); kept += 1
        except Exception as e:
            failures.append(f"Leitura da recolha anterior: {e}"[:200])
    out = {"version": 1, "kind": "go-charge-sync", "zone": zone, "radius": 1, "categories": TYPES,
           "syncedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "servers": sorted(servers), "timestampOsmBase": ts, "tilesDone": done, "tilesTotal": len(tiles) + 1,
           "failures": failures, "keptFromPrevious": kept, "candidates": cands, "stations": stations}
    if not servers:
        raise RuntimeError(f"{zone}: nenhuma parcela respondeu — ficheiro anterior mantido")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{slug(zone)}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{zone}: {len(cands)} candidatos, {len(stations)} postos, parcelas completas {done}/{len(tiles)+1}, "
          f"{kept} mantidos da recolha anterior, {len(failures)} áreas sem resposta")

def main(argv):
    zones = [z for z in ZONES if not argv or z in argv]
    errors = []
    for z in zones:
        try: sync_zone(z)
        except Exception as e: errors.append(str(e)); print("ERRO", e)
    if len(errors) == len(zones): sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1:])
