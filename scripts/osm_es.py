"""Locais candidatos do OpenStreetMap em Espanha, nos municípios de prioridade A e B da GO-CHARGE Prospect.

Lista de municípios: data/es/osm_alvos.json (gerado a partir do ranking espanhol; agrupados por província).
Cada grupo de municípios é pedido ao Overpass pelos limites oficiais do OSM (etiqueta ine:municipio), com
"foreach" para saber a que município pertence cada local. Município sem limite no OSM → pedido pela caixa
envolvente (marcado bbox=true; a GO-CHARGE filtra depois pelo limite do IGN).
Robustez igual à recolha portuguesa: vários servidores e rondas; grupo que falha é repetido por categoria;
o que mesmo assim falhar mantém os dados da recolha anterior desses municípios.

Uso:  python scripts/osm_es.py            # todas as províncias
      python scripts/osm_es.py "Madrid"   # só uma província
Saída: data/es/osm/<provincia>.json   ·   Dados © colaboradores do OpenStreetMap (ODbL)
"""
import json, os, re, sys, time, unicodedata, datetime as dt
from pathlib import Path

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
DATA = Path(os.environ.get("DATA_DIR", "data"))
ALVOS = DATA / "es" / "osm_alvos.json"
OUT = DATA / "es" / "osm"
TYPES = ["Hotel", "Supermercado", "Restaurante", "Parque", "Empresa", "Concessionário"]
PARKING_ESTIMATE = {"Hotel": 55, "Supermercado": 90, "Empresa": 75, "Restaurante": 25, "Parque": 120, "Concessionário": 45}
UA = "go-charge-dados/1.0 (prospecao de mercado; recolha mensal)"
PAUSE = 4
ROUNDS = int(os.environ.get("OSM_ROUNDS", "3"))
ROUND_WAIT = int(os.environ.get("OSM_ROUND_WAIT", "30"))
DEADLINE = time.time() + 60 * float(os.environ.get("OSM_DEADLINE_MIN", "300"))

SEL = {   # categoria → filtros Overpass ({A} = conjunto de área ou caixa)
    "Hotel": ['nwr["tourism"~"^(hotel|hostel|guest_house|motel|apartment)$"]["name"]{A};'],
    "Supermercado": ['nwr["shop"~"^(supermarket|mall|department_store)$"]["name"]{A};'],
    "Restaurante": ['nwr["amenity"~"^(restaurant|fast_food)$"]["name"]{A};'],
    "Parque": ['nwr["amenity"="parking"]["access"!~"^(private|no|customers)$"]{A};'],
    "Empresa": ['nwr["office"]["name"]{A};'],
    "Concessionário": ['nwr["shop"="car"]["name"]{A};'],
}

def slug(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

def overpass(query, timeout=330):
    import requests
    last = None
    for rnd in range(ROUNDS):
        if time.time() > DEADLINE: raise RuntimeError(last or "tempo limite da recolha atingido")
        if rnd: time.sleep(ROUND_WAIT * rnd)
        for url in ENDPOINTS:
            try:
                r = requests.post(url, data={"data": query}, timeout=timeout, headers={"User-Agent": UA})
                if r.status_code == 200:
                    j = r.json(); rem = j.get("remark") or ""
                    if re.search(r"timed out|runtime error|out of memory", rem, re.I):
                        last = f"{url}: {rem[:120]}"; continue
                    return j, url
                last = f"{url}: HTTP {r.status_code}"
                if r.status_code == 400: raise RuntimeError(last + " (pedido inválido)")
            except RuntimeError: raise
            except Exception as e:
                last = f"{url}: {str(e)[:120]}"
            time.sleep(PAUSE)
    raise RuntimeError(last)

def osm_areas(codes):
    """Códigos INE que têm limite municipal no OSM (pedido leve: só etiquetas das áreas)."""
    found = set()
    for i in range(0, len(codes), 120):
        part = codes[i:i + 120]
        q = f'[out:json][timeout:180];area["ine:municipio"~"^({"|".join(part)})"]["admin_level"="8"];out tags;'
        j, _ = overpass(q); time.sleep(PAUSE)
        for el in j.get("elements", []):
            c = str((el.get("tags") or {}).get("ine:municipio", ""))[:5]
            if c in part: found.add(c)
    return found

def q_group(codes, cats):
    body = "".join(s.format(A="(area.a)") for c in cats for s in SEL[c])
    return (f'[out:json][timeout:300][maxsize:1073741824];area["ine:municipio"~"^({"|".join(codes)})"]["admin_level"="8"]->.all;'
            f'foreach.all->.a(.a out tags;({body});out center tags;);')

def q_bbox(b, cats):
    x = "({:.5f},{:.5f},{:.5f},{:.5f})".format(*b)
    body = "".join(s.format(A=x) for c in cats for s in SEL[c])
    return f"[out:json][timeout:300];({body});out center tags;"

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

def to_candidate(el, muni, bbox_mode):
    t = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None or not (t.get("name") or t.get("brand") or t.get("operator")): return None
    ty = category(t); cap = capacity(t.get("capacity") or t.get("capacity:parking"))
    c = {"id": f"osm:{el['type']}:{el['id']}", "osmType": el["type"], "osmId": el["id"],
         "name": str(t.get("name") or t.get("brand") or t.get("operator"))[:160], "type": ty,
         "city": str(t.get("addr:city") or t.get("addr:town") or "")[:90],
         "area": str(t.get("addr:suburb") or t.get("addr:place") or t.get("addr:street") or "")[:90],
         "parking": cap if cap is not None else PARKING_ESTIMATE[ty], "parkingSource": "osm" if cap is not None else "estimate",
         "contact": str(t.get("operator") or "Por identificar")[:120],
         "phone": str(t.get("phone") or t.get("contact:phone") or "")[:60],
         "website": url_ok(t.get("website") or t.get("contact:website")),
         "osmUrl": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
         "lat": round(lat, 6), "lon": round(lon, 6), "muni": muni}
    if bbox_mode: c["bbox"] = True
    return c

def fetch_group(codes, cats):
    """Devolve {código: [candidatos]} para um grupo com limites OSM."""
    j, url = overpass(q_group(codes, cats))
    res, cur = {c: [] for c in codes}, None
    for el in j.get("elements", []):
        if el["type"] == "area":
            cur = str((el.get("tags") or {}).get("ine:municipio", ""))[:5]; continue
        if cur in res:
            c = to_candidate(el, cur, False)
            if c: res[cur].append(c)
    return res, url, (j.get("osm3s") or {}).get("timestamp_osm_base")

def sync_prov(p, have_area):
    prev_file = OUT / f"{p['slug']}.json"
    prev = {}
    if prev_file.exists():
        try:
            for c in json.loads(prev_file.read_text(encoding="utf-8")).get("candidates", []): prev.setdefault(c.get("muni"), []).append(c)
        except Exception: pass
    by_muni, servers, failures, ts, done, total, kept = {}, set(), [], None, 0, 0, 0
    def note(url, t):
        nonlocal ts
        servers.add(url.split("/")[2])
        if t and (not ts or t > ts): ts = t
    for grp in p["groups"]:
        grp = [{"d": m[0], "bbox": m[1:5]} for m in grp]
        area_codes = [m["d"] for m in grp if m["d"] in have_area]
        box_munis = [m for m in grp if m["d"] not in have_area]
        if area_codes:
            total += 1
            try:
                res, url, t = fetch_group(area_codes, TYPES); note(url, t); by_muni.update(res); done += 1
            except Exception as e:
                print(f"  {p['prov']} · grupo {area_codes[0]}…: falhou ({str(e)[:80]}) — a repetir por categoria")
                part, ok = {c: [] for c in area_codes}, True
                for cat in TYPES:
                    try:
                        res, url, t = fetch_group(area_codes, [cat]); note(url, t)
                        for k, v in res.items(): part[k].extend(v)
                    except Exception as e2:
                        ok = False; failures.append(f"{area_codes[0]}… {cat}: {e2}"[:200])
                    time.sleep(PAUSE)
                if ok: by_muni.update(part); done += 1
                else:
                    for k in area_codes:
                        if prev.get(k): by_muni[k] = prev[k]; kept += len(prev[k])
                        elif part.get(k): by_muni[k] = part[k]
            time.sleep(PAUSE)
        for m in box_munis:
            total += 1
            try:
                j, url = overpass(q_bbox(m["bbox"], TYPES)); note(url, (j.get("osm3s") or {}).get("timestamp_osm_base"))
                by_muni[m["d"]] = [c for c in (to_candidate(el, m["d"], True) for el in j.get("elements", [])) if c]; done += 1
            except Exception as e:
                failures.append(f"{m['d']} (caixa): {e}"[:200])
                if prev.get(m["d"]): by_muni[m["d"]] = prev[m["d"]]; kept += len(prev[m["d"]])
            time.sleep(PAUSE)
    if not servers:
        raise RuntimeError(f"{p['prov']}: nenhum pedido respondeu — ficheiro anterior mantido")
    seen, cands = set(), []
    for k in sorted(by_muni):
        for c in by_muni[k]:
            if c["id"] not in seen: seen.add(c["id"]); cands.append(c)
    out = {"version": 1, "kind": "go-charge-sync", "country": "ES", "zone": f"Espanha · {p['prov']}", "prov": p["prov"],
           "ccaa": p.get("ccaa", ""), "radius": 1, "categories": TYPES,
           "syncedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "servers": sorted(servers),
           "timestampOsmBase": ts, "tilesDone": done, "tilesTotal": total, "failures": failures, "keptFromPrevious": kept,
           "municipios": sorted(m[0] for g in p["groups"] for m in g), "semLimiteOSM": sorted(m[0] for g in p["groups"] for m in g if m[0] not in have_area),
           "candidates": cands, "stations": []}
    OUT.mkdir(parents=True, exist_ok=True)
    prev_file.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{p['prov']}: {len(cands)} candidatos, pedidos completos {done}/{total}, {kept} mantidos da recolha anterior")

def main(argv):
    alvos = json.loads(ALVOS.read_text(encoding="utf-8"))["provincias"]
    provs = [p for p in alvos if not argv or p["prov"] in argv or p["slug"] in argv]
    codes = sorted(m[0] for p in provs for g in p["groups"] for m in g)
    try:
        have_area = osm_areas(codes)
    except Exception as e:
        print("Aviso: verificação de limites OSM falhou —", e); have_area = set(codes)
    print(f"{len(have_area)}/{len(codes)} municípios com limite no OSM")
    # províncias com recolha mais antiga primeiro (se o tempo acabar, a próxima execução continua pelas outras)
    def age(p):
        f = OUT / f"{p['slug']}.json"
        try: return json.loads(f.read_text(encoding="utf-8")).get("syncedAt", "")
        except Exception: return ""
    errors = []
    for p in sorted(provs, key=age):
        if time.time() > DEADLINE: errors.append(f"{p['prov']}: sem tempo nesta execução"); continue
        try: sync_prov(p, have_area)
        except Exception as e: errors.append(str(e)); print("ERRO", e)
    for e in errors: print("Pendente:", e)
    if len(errors) == len(provs): sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1:])
