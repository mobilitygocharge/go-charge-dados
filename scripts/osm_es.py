"""Locais candidatos do OpenStreetMap em Espanha, nos municípios de prioridade A e B da GO-CHARGE Prospect.

Fonte: extratos completos do OpenStreetMap publicados pela Geofabrik (Espanha + Canárias), em vez do Overpass:
o Overpass público não aguenta pedidos desta dimensão (a 1.ª recolha, a 19/10/2026, só fez 7 províncias em 5 h).
  1. descarrega spain-latest.osm.pbf e canary-islands-latest.osm.pbf (~1,5 GB, alguns minutos);
  2. osmium tags-filter fica só com as categorias que interessam;
  3. pyosmium lê o ficheiro filtrado (pontos, vias e multipolígonos → ponto central);
  4. cada local entra na província cujo(s) município(s)-alvo o contém pela caixa envolvente
     (data/es/osm_alvos.json); a GO-CHARGE atribui depois o município exato pelo limite do IGN (bbox=true).
Uso:  python scripts/osm_es.py            (precisa de osmium-tool e do pacote python osmium)
Saída: data/es/osm/<provincia>.json   ·   Dados © colaboradores do OpenStreetMap (ODbL), via Geofabrik
"""
import json, os, re, subprocess, sys, time, unicodedata, datetime as dt
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "data"))
ALVOS = DATA / "es" / "osm_alvos.json"
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

def main():
    alvos = json.loads(ALVOS.read_text(encoding="utf-8"))["provincias"]
    boxes = []   # (prov_slug, code, s, w, n, e)
    for p in alvos:
        for g in p["groups"]:
            for m in g: boxes.append((p["slug"], m[0], *m[1:5]))
    cell = {}
    for i, b in enumerate(boxes):
        for la in range(int((b[2] + 90) * 10), int((b[4] + 90) * 10) + 1):
            for lo in range(int((b[3] + 180) * 10), int((b[5] + 180) * 10) + 1):
                cell.setdefault((la, lo), []).append(i)
    per_prov = {p["slug"]: {} for p in alvos}
    ts_all, sources, errors = [], [], []
    for name, url in EXTRACTS.items():
        try:
            f = download(name, url); ts = osm_timestamp(f); ts_all.append(ts); sources.append(f"{name}: {ts}")
            items = collect(f)
            print(f"{name}: {len(items)} locais nas categorias ({ts})", flush=True)
        except Exception as e:
            errors.append(f"{name}: {e}"); print("ERRO", name, e, flush=True); continue
        for typ, oid, lat, lon, t, ty in items:
            hits = cell.get((int((lat + 90) * 10), int((lon + 180) * 10)), [])
            for i in hits:
                b = boxes[i]
                if b[2] <= lat <= b[4] and b[3] <= lon <= b[5]:
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
                         "lat": round(lat, 6), "lon": round(lon, 6), "muni": b[1], "bbox": True}
                    per_prov[b[0]].setdefault(c["id"], c)   # pode entrar em duas províncias vizinhas; o limite do IGN decide depois
    if len(errors) == len(EXTRACTS):
        print("Nenhum extrato processado — ficheiros anteriores mantidos"); sys.exit(1)
    ts = max([t for t in ts_all if t] or [None]) if any(ts_all) else None
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    OUT.mkdir(parents=True, exist_ok=True)
    for p in alvos:
        if errors and p["ccaa"] == "Canarias" and any(e.startswith("canarias") for e in errors): continue   # mantém o anterior
        if errors and p["ccaa"] != "Canarias" and any(e.startswith("spain") for e in errors): continue
        cands = list(per_prov[p["slug"]].values())
        out = {"version": 1, "kind": "go-charge-sync", "country": "ES", "zone": f"Espanha · {p['prov']}", "prov": p["prov"],
               "ccaa": p.get("ccaa", ""), "radius": 1, "categories": TYPES, "syncedAt": now,
               "servers": ["download.geofabrik.de"], "fonte": sources, "timestampOsmBase": ts, "tilesDone": 1, "tilesTotal": 1,
               "failures": errors, "keptFromPrevious": 0,
               "municipios": sorted(m[0] for g in p["groups"] for m in g), "semLimiteOSM": [],
               "candidates": cands, "stations": []}
        (OUT / f"{p['slug']}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{p['prov']}: {len(cands)} locais", flush=True)

if __name__ == "__main__":
    main()
