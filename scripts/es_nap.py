"""Postos de carregamento públicos em Espanha — Punto de Acceso Nacional (DGT/MITERD), DATEX II v3.

Fonte oficial (licença CC BY, publicação diária):
  https://nap.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml
Um pedido por execução (o ficheiro tem ~85 MB). Saída:
  data/es/nap/postos.json   — um registo por local (emplazamiento)
  data/es/nap/resumo.json   — totais, data de publicação e um excerto do 1.º registo (diagnóstico)
"""
import json, os, re, sys, time, datetime as dt
from pathlib import Path

URL = "https://nap.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml"
OUT = Path(os.environ.get("DATA_DIR", "data")) / "es" / "nap"
UA = "go-charge-dados/1.0 (prospecao de mercado; recolha semanal)"
MAX_KW = 600          # há registos com 1000 kW (erros de carga)

def first(block, pat):
    m = re.search(pat, block)
    return m.group(1).strip() if m else ""

def dec(s):
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))

def addr(site, prefix):
    return dec(first(site, r'<com:value lang="es">' + prefix + r':\s*([^<]*)<'))

def parse_site(site):
    sid = first(site, r'^[^>]*id="([^"]+)"')
    try:
        lat = float(first(site, r"<loc:latitude>([^<]+)<")); lon = float(first(site, r"<loc:longitude>([^<]+)<"))
    except ValueError:
        return None
    if not sid or (lat == 0 and lon == 0) or not (27 < lat < 44.5 and -18.5 < lon < 4.6):
        return None
    def conn_list(block):
        out = []
        for c in re.finditer(r"<egi:connector>([\s\S]*?)</egi:connector>", block):
            b = c.group(1)
            try: w = float(first(b, r"<egi:maxPowerAtSocket>([^<]+)<"))
            except ValueError: w = 0
            kw = w / 1000 if w > 1000 else w          # alguns registos vêm em kW
            if kw <= 0 or kw > MAX_KW: continue
            t = first(b, r"<egi:connectorType>([^<]+)<"); mode = first(b, r"<egi:chargingMode>([^<]+)<")
            out.append((t, kw, mode == "mode4DC" or t in ("iec62196T2COMBO", "chademo")))
        return out
    allc = conn_list(site)
    if not allc:
        return None
    types = {}
    for t, _, _ in allc: types[t] = types.get(t, 0) + 1
    # ponto de carga (refillPoint) = um veículo de cada vez; potência do ponto = a do seu conector mais potente
    rps = [conn_list(m.group(1)) for m in re.finditer(r"<egi:refillPoint\b[^>]*>([\s\S]*?)</egi:refillPoint>", site)]
    rps = [r for r in rps if r] or [[c] for c in allc]
    pkw = [max(k for _, k, _ in r) for r in rps]
    pdc = [any(d for _, _, d in r) for r in rps]
    points, kw_sum, kw_max = len(rps), round(sum(pkw), 1), max(pkw)
    dc = sum(1 for k, d in zip(pkw, pdc) if d and k >= 50); hpc = sum(1 for k, d in zip(pkw, pdc) if d and k >= 150)
    conns = len(allc)
    stations = len(re.findall(r"<egi:energyInfrastructureStation\b", site))
    op_block = re.search(r"<fac:operator [\s\S]*?</fac:operator>", site)
    op = dec(first(op_block.group(0), r'<com:value lang="es">([^<]*)<')) if op_block else ""
    return {"id": "nap:" + sid,
            "name": dec(first(site, r'<fac:name>\s*<com:values>\s*<com:value lang="es">([^<]*)<'))[:160],
            "op": op[:120], "lat": round(lat, 6), "lon": round(lon, 6),
            "addr": addr(site, "Dirección")[:160], "town": addr(site, "Municipio")[:90], "prov": addr(site, "Provincia")[:60],
            "hours": dec(first(site, r"<fac:label>([^<]*)<"))[:200],
            "points": points, "conns": conns, "stations": stations, "kw": round(kw_max, 1), "kw_sum": kw_sum, "dc": dc, "hpc": hpc,
            "types": types, "upd": first(site, r"<fac:lastUpdated>([^<]+)<")}

def main():
    import requests
    last = None
    for attempt in range(4):
        try:
            r = requests.get(URL, timeout=600, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            r.encoding = "utf-8"
            if r.status_code == 200 and "energyInfrastructureTable" in r.text[:20000]:
                xml = r.text; break
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
        time.sleep(60 * (attempt + 1))
    else:
        print("ERRO: NAP indisponível —", last); sys.exit(1)
    pub = first(xml[:5000], r"<com:publicationTime>([^<]+)<")
    open_t, close_t = "<egi:energyInfrastructureSite ", "</egi:energyInfrastructureSite>"
    sites, raw_first, pos, skipped = [], "", xml.find(open_t), 0
    while pos != -1:
        end = xml.find(close_t, pos)
        if end == -1: break
        block = xml[pos + len(open_t):end]
        if not raw_first: raw_first = block[:6000]
        s = parse_site(block)
        if s: sites.append(s)
        else: skipped += 1
        pos = xml.find(open_t, end)
    if len(sites) < 3000:
        print(f"ERRO: só {len(sites)} locais — ficheiro anterior mantido"); sys.exit(1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "postos.json").write_text(json.dumps({"source": URL, "license": "CC BY 4.0", "publicationTime": pub,
        "sites": sites}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tot = lambda k: sum(s[k] for s in sites)
    resumo = {"recolhido": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "publicationTime": pub,
              "bytes": len(xml), "sites": len(sites), "ignorados": skipped, "points": tot("points"), "conns": tot("conns"),
              "stations": tot("stations"), "kw_sum": round(tot("kw_sum")), "dc50": tot("dc"), "hpc150": tot("hpc"),
              "operadores_top": sorted(((o, n) for o, n in __import__("collections").Counter(s["op"] for s in sites).items()), key=lambda x: -x[1])[:15],
              "excerto_1o_registo": raw_first}
    (OUT / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"NAP {pub}: {len(sites)} locais, {resumo['points']} pontos, {resumo['conns']} conectores, "
          f"{resumo['dc50']} pontos DC≥50 kW, {resumo['hpc150']} ≥150 kW ({skipped} ignorados)")

if __name__ == "__main__":
    main()
