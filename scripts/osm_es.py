"""Locais candidatos do OpenStreetMap em toda a Espanha (8 131 municípios), para o radar da GO-CHARGE Prospect.

Fonte: extratos completos do OpenStreetMap publicados pela Geofabrik (Espanha + Canárias), em vez do Overpass:
o Overpass público não aguenta pedidos desta dimensão (a 1.ª recolha, a 19/10/2026, só fez 7 províncias em 5 h).
  1. descarrega spain-latest.osm.pbf e canary-islands-latest.osm.pbf (~1,5 GB, alguns minutos);
  2. osmium tags-filter fica só com as categorias que interessam;
  3. pyosmium lê o ficheiro filtrado (pontos, vias e multipolígonos → ponto central);
  4. cada local é atribuído ao município pelo limite oficial do IGN (es-atlas, ficheiro MUN_GEOJSON gerado no
     workflow); locais no mar/na costa a menos de ~1 km ficam no município mais próximo.
Uso:  MUN_GEOJSON=/tmp/mun.geojson python scripts/osm_es.py   (precisa de osmium-tool, osmium e shapely)
Saída: data/es/osm/<provincia>.json   ·   Dados © colaboradores do OpenStreetMap (ODbL), via Geofabrik
"""
import json, os, re, subprocess, sys, time, unicodedata, datetime as dt
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "data"))
MUN_GEOJSON = os.environ.get("MUN_GEOJSON", "/tmp/mun.geojson")
PROV = {"01": ("Álava", "País Vasco"), "02": ("Albacete", "Castilla-La Mancha"), "03": ("Alicante", "C. Valenciana"), "04": ("Almería", "Andalucía"),
        "05": ("Ávila", "Castilla y León"), "06": ("Badajoz", "Extremadura"), "07": ("Illes Balears", "Illes Balears"), "08": ("Barcelona", "Cataluña"),
        "09": ("Burgos", "Castilla y León"), "10": ("Cáceres", "Extremadura"), "11": ("Cádiz", "Andalucía"), "12": ("Castellón", "C. Valenciana"),
        "13": ("Ciudad Real", "Castilla-La Mancha"), "14": ("Córdoba", "Andalucía"), "15": ("A Coruña", "Galicia"), "16": ("Cuenca", "Castilla-La Mancha"),
        "17": ("Girona", "Cataluña"), "18": ("Granada", "Andalucía"), "19": ("Guadalajara", "Castilla-La Mancha"), "20": ("Gipuzkoa", "País Vasco"),
        "21": ("Huelva", "Andalucía"), "22": ("Huesca", "Aragón"), "23": ("Jaén", "Andalucía"), "24": ("León", "Castilla y León"),
        "25": ("Lleida", "Cataluña"), "26": ("La Rioja", "La Rioja"), "27": ("Lugo", "Galicia"), "28": ("Madrid", "Madrid"),
        "29": ("Málaga", "Andalucía"), "30": ("Murcia", "Murcia"), "31": ("Navarra", "Navarra"), "32": ("Ourense", "Galicia"),
        "33": ("Asturias", "Asturias"), "34": ("Palencia", "Castilla y León"), "35": ("Las Palmas", "Canarias"), "36": ("Pontevedra", "Galicia"),
        "37": ("Salamanca", "Castilla y León"), "38": ("Santa Cruz de Tenerife", "Canarias"), "39": ("Cantabria", "Cantabria"), "40": ("Segovia", "Castilla y León"),
        "41": ("Sevilla", "Andalucía"), "42": ("Soria", "Castilla y León"), "43": ("Tarragona", "Cataluña"), "44": ("Teruel", "Aragón"),
        "45": ("Toledo", "Castilla-La Mancha"), "46": ("Valencia", "C. Valenciana"), "47": ("Valladolid", "Castilla y León"), "48": ("Bizkaia", "País Vasco"),
        "49": ("Zamora", "Castilla y León"), "50": ("Zaragoza", "Aragón"), "51": ("Ceuta", "Ceuta"), "52": ("Melilla", "Melilla")}
OUT = DATA / "es" / "osm"
WORK = Path(os.environ.get("WORK_DIR", "/tmp/osm_es"))
EXTRACTS = {"spain": "https://download.geofabrik.de/europe/spain-latest.osm.pbf",
            "canarias": "https://download.geofabrik.de/africa/canary-islands-latest.osm.pbf"}
TYPES = ["Hotel", "Supermercado", "Restaurante", "Parque", "Empresa", "Concessionário"]
PARKING_ESTIMATE = {"Hotel": 55, "Supermercado": 90, "Empresa": 75, "Restaurante": 25, "Parque": 120, "Concessionário": 45}
FILTER = ["nwr/tourism=hotel,hostel,guest_house,motel,apartment", "nwr/shop=supermarket,mall,department_store,car",
          "nwr/amenity=restaurant,fast_food,parking", "nwr/office"]

def slug(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

def capacity(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m and int(m.group()) < 100000 else None

def category(t):
    if t.get("tourism") in ("hotel", "hostel", "guest_house", "motel", "apartment"): return "Hotel"
    if t.get("shop") in ("supermarket", "mall", "department_store"): return "Supermercado"
    if t.get("shop") == "car": return "Concessionário"
    if t.get("amenity") in ("restaurant", "fast_food"): return "Restaurante"
    if t.get("amenity") == "parking": return "Parque"
    if t.get("office"): return "Empresa"
    return None

def url_ok(u):
    u = (u or "").strip()
    if not u: return ""
    if not re.match(r"^https?://", u): u = "https://" + u
    return u[:300]

def wanted(t):
    ty = category(t)
    if not ty: return None
    if ty == "Parque":
        if t.get("access") in ("private", "no", "customers"): return None
    elif not t.get("name"): return None
    if not (t.get("name") or t.get("brand") or t.get("operator")): return None
    return ty

def download(name, url):
    WORK.mkdir(parents=True, exist_ok=True)
    f = WORK / f"{name}.osm.pbf"
    for attempt in range(3):
        r = subprocess.run(["curl", "-fsSL", "--retry", "3", "-o", str(f), url])
        if r.returncode == 0 and f.stat().st_size > 1_000_000: return f
        time.sleep(30)
    raise RuntimeError(f"download falhou: {url}")

def osm_timestamp(f):
    r = subprocess.run(["osmium", "fileinfo", "-g", "header.option.osmosis_replication_timestamp", str(f)], capture_output=True, text=True)
    return r.stdout.strip() or None

def collect(pbf):
    import osmium
    flt = WORK / (pbf.stem + ".filtered.osm.pbf")
    subprocess.run(["osmium", "tags-filter", "--overwrite", "-o", str(flt), str(pbf), *FILTER], check=True)
    out = []
    class H(osmium.SimpleHandler):
        def node(self, n):
            if not n.tags: return
            t = dict(n.tags); ty = wanted(t)
            if ty and n.location.valid(): out.append(("node", n.id, n.location.lat, n.location.lon, t, ty))
        def way(self, w):
            if not w.tags: return
            t = dict(w.tags); ty = wanted(t)
            if not ty: return
            pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if pts: out.append(("way", w.id, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), t, ty))
        def area(self, a):
            if a.from_way(): return          # vias fechadas já tratadas em way()
            t = dict(a.tags); ty = wanted(t)
            if not ty: return
            pts = [(n.lat, n.lon) for ring in a.outer_rings() for n in ring if n.location.valid()]
            if pts: out.append(("relation", a.orig_id(), sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), t, ty))
    H().apply_file(str(flt), locations=True, idx="flex_mem")
    return out

def municipalities():
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    g = json.loads(Path(MUN_GEOJSON).read_text(encoding="utf-8"))
    geoms, codes = [], []
    for f in g["features"]:
        c = str(f.get("id") or "")
        if len(c) != 5 or c[:2] not in PROV: continue
        gm = shape(f["geometry"]).buffer(0)
        if gm.is_empty: gm = shape(f["geometry"])
        if gm.is_empty: continue
        geoms.append(gm); codes.append(c)
    return geoms, codes, STRtree(geoms)

def main():
    from shapely.geometry import Point
    geoms, codes, tree = municipalities()
    print(f"{len(codes)} municípios com limite", flush=True)
    def muni_of(lat, lon):
        p = Point(lon, lat)
        for i in tree.query(p):
            if geoms[i].contains(p): return codes[i]
        best, bd = None, 0.01                     # ~1 km: marinas, portos, pontões
        for i in tree.query(p.buffer(0.01)):
            d = geoms[i].distance(p)
            if d < bd: best, bd = codes[i], d
        return best
    per_prov = {k: {} for k in PROV}
    munis_by_prov = {k: sorted(c for c in codes if c[:2] == k) for k in PROV}
    ts_all, sources, errors, outside = [], [], [], 0
    for name, url in EXTRACTS.items():
        try:
            f = download(name, url); ts = osm_timestamp(f); ts_all.append(ts); sources.append(f"{name}: {ts}")
            items = collect(f)
            print(f"{name}: {len(items)} locais nas categorias ({ts})", flush=True)
        except Exception as e:
            errors.append(f"{name}: {e}"); print("ERRO", name, e, flush=True); continue
        for typ, oid, lat, lon, t, ty in items:
            m = muni_of(lat, lon)
            if not m: outside += 1; continue
            cap = capacity(t.get("capacity") or t.get("capacity:parking"))
            c = {"id": f"osm:{typ}:{oid}", "osmType": typ, "osmId": oid,
                 "name": str(t.get("name") or t.get("brand") or t.get("operator"))[:160], "type": ty,
                 "city": str(t.get("addr:city") or t.get("addr:town") or "")[:90],
                 "area": str(t.get("addr:suburb") or t.get("addr:place") or t.get("addr:street") or "")[:90],
                 "parking": cap if cap is not None else PARKING_ESTIMATE[ty], "parkingSource": "osm" if cap is not None else "estimate",
                 "contact": str(t.get("operator") or "Por identificar")[:120],
                 "phone": str(t.get("phone") or t.get("contact:phone") or "")[:60],
                 "website": url_ok(t.get("website") or t.get("contact:website")),
                 "osmUrl": f"https://www.openstreetmap.org/{typ}/{oid}",
                 "lat": round(lat, 6), "lon": round(lon, 6), "muni": m}
            per_prov[m[:2]].setdefault(c["id"], c)
    if len(errors) == len(EXTRACTS):
        print("Nenhum extrato processado — ficheiros anteriores mantidos"); sys.exit(1)
    print(f"{outside} locais fora de qualquer município (mar, Andorra, Gibraltar, Portugal, França)", flush=True)
    ts = max([t for t in ts_all if t] or [None]) if any(ts_all) else None
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    OUT.mkdir(parents=True, exist_ok=True)
    for k, (prov, ccaa) in PROV.items():
        canarias = k in ("35", "38")
        if any(e.startswith("canarias" if canarias else "spain") for e in errors): continue   # mantém o ficheiro anterior
        cands = list(per_prov[k].values())
        out = {"version": 2, "kind": "go-charge-sync", "country": "ES", "zone": f"Espanha · {prov}", "prov": prov, "ccaa": ccaa,
               "radius": 1, "categories": TYPES, "syncedAt": now, "servers": ["download.geofabrik.de"], "fonte": sources,
               "timestampOsmBase": ts, "tilesDone": 1, "tilesTotal": 1, "failures": errors, "keptFromPrevious": 0,
               "municipios": munis_by_prov[k], "semLimiteOSM": [], "candidates": cands, "stations": []}
        (OUT / f"{slug(prov)}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{prov}: {len(cands)} locais", flush=True)

if __name__ == "__main__":
    main()
