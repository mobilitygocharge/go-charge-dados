"""Recolha dos dados oficiais da rede pública de carregamento (MOBI.E, via Ponto de Acesso Nacional).

Fonte: NAP Portugal (IMT) — feeds DATEX II da rede MOBI.E, de livre acesso.
  inventário : https://pgm.mobie.pt/integration/nap/evChargingInfra
  estado     : https://pgm.mobie.pt/integration/nap/evActualStatus

Uso:
  python scripts/mobie.py            # estado (ocupação) e, se ainda não houver de hoje, inventário
  python scripts/mobie.py --infra    # força o inventário
  python scripts/mobie.py --from-files infra.xml status.xml   # teste com ficheiros locais

Saídas (em data/mobie/):
  postos.json          um registo por local (site): coordenadas, operador, nº de tomadas, potências
  resumo.json          totais e data de publicação do feed
  ocupacao/AAAA-MM.json  por local: [amostras de tomadas, tomadas ocupadas, tomadas indisponíveis]
"""
import json, os, sys, time, datetime as dt
from pathlib import Path
from lxml import etree

INFRA_URL = os.environ.get("INFRA_URL", "https://pgm.mobie.pt/integration/nap/evChargingInfra")
STATUS_URL = os.environ.get("STATUS_URL", "https://pgm.mobie.pt/integration/nap/evActualStatus")
OUT = Path(os.environ.get("DATA_DIR", "data")) / "mobie"
UA = "go-charge-dados/1.0 (prospecao de mercado; recolha diaria)"

BUSY = {"charging", "occupied", "blocked", "reserved"}
DOWN = {"faulted", "inoperative", "outoforder", "unavailable", "closed", "outofstock"}


def ln(el):
    try:
        return etree.QName(el).localname
    except (ValueError, TypeError):
        return None

def child(el, name):
    if el is None: return None
    for c in el:
        if ln(c) == name: return c
    return None

def desc(el, name):
    if el is None: return None
    for d in el.iter():
        if d is not el and ln(d) == name: return d
    return None

def text(el):
    if el is None or el.text is None: return None
    t = el.text.strip(); return t or None

def value(el):
    if el is None: return None
    v = desc(el, "value")
    return text(v) if v is not None else text(el)

def num(s):
    try: return float(s)
    except (TypeError, ValueError): return None


def fetch(url, attempts=3):
    import requests
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            if r.status_code == 200 and r.content:
                return r.content
            last = f"HTTP {r.status_code}"
        except Exception as e:  # rede
            last = str(e)
        time.sleep(15 * (i + 1))
    raise RuntimeError(f"falha a descarregar {url}: {last}")


def parse_infra(src):
    """Um registo por local. Potências em kW."""
    sites, pub = [], None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        name = ln(el)
        if pub is None and name == "publicationTime":
            pub = text(el); continue
        if name != "energyInfrastructureSite":
            continue
        op = child(el, "operator")
        lat, lon = num(text(desc(el, "latitude"))), num(text(desc(el, "longitude")))
        points, kws, conn_types = 0, [], set()
        for rp in (d for d in el.iter() if ln(d) == "refillPoint"):
            points += 1
            powers = [p for c in rp if ln(c) == "connector" and (p := num(text(child(c, "maxPowerAtSocket")))) is not None]
            for c in rp:
                if ln(c) == "connector" and (t := text(child(c, "connectorType"))): conn_types.add(t)
            kw = max(powers) / 1000.0 if powers else 0.0
            if kw > 1000: kw = kw / 1000.0   # alguns operadores podem enviar mW por engano
            kws.append(kw)
        parking = None
        for af in (d for d in el.iter() if ln(d) == "associatedFacility"):
            cap = num(text(desc(af, "carParkingCapacity")))
            if cap is not None and (text(child(af, "type")) or "").lower() == "carpark":
                parking = (parking or 0) + cap
        if lat is None or lon is None:
            el.clear(keep_tail=True); continue
        sites.append({
            "id": el.get("id"),
            "name": (value(child(el, "name")) or "")[:120],
            "operator": (value(child(op, "name")) or "")[:80],
            "lat": round(lat, 6), "lon": round(lon, 6),
            "city": (value(desc(el, "city")) or "")[:60],
            "postcode": text(desc(el, "postcode")) or "",
            "points": points,
            "kw_total": round(sum(kws), 1),
            "kw_max": round(max(kws), 1) if kws else 0,
            "dc_points": sum(1 for k in kws if k >= 50),
            "ultra_points": sum(1 for k in kws if k >= 150),
            "ac_points": sum(1 for k in kws if 0 < k < 50),
            "connectors": sorted(conn_types),
            "parking": parking,
            "updated": text(child(el, "lastUpdated")) or "",
        })
        el.clear(keep_tail=True)
    return sites, pub


def parse_status(src):
    """Por local: [tomadas reportadas, ocupadas, indisponíveis]."""
    agg, pub, site = {}, None, None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        name = ln(el)
        if pub is None and name == "publicationTime":
            pub = text(el); continue
        if name == "reference":
            par = el.getparent()
            if par is not None and ln(par) == "energyInfrastructureSiteStatus":
                site = el.get("id")
            continue
        if name != "refillPointStatus":
            continue
        st_el = child(el, "status")
        if st_el is None: st_el = desc(el, "status")
        st = (text(st_el) or "").lower()
        a = agg.setdefault(site or "?", [0, 0, 0])
        a[0] += 1
        if st in BUSY: a[1] += 1
        elif st in DOWN: a[2] += 1
        el.clear(keep_tail=True)
    return agg, pub


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def run_infra(src):
    sites, pub = parse_infra(src)
    if len(sites) < 1000 and not os.environ.get("ALLOW_SMALL"):
        raise RuntimeError(f"inventário suspeito: só {len(sites)} locais — não foi gravado")
    write_json(OUT / "postos.json", sites)
    resumo = {
        "fonte": "MOBI.E via NAP Portugal (DATEX II)", "publicado": pub,
        "recolhido": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "locais": len(sites), "tomadas": sum(s["points"] for s in sites),
        "tomadas_dc_50kw": sum(s["dc_points"] for s in sites),
        "tomadas_150kw": sum(s["ultra_points"] for s in sites),
        "potencia_total_mw": round(sum(s["kw_total"] for s in sites) / 1000, 1),
    }
    write_json(OUT / "resumo.json", resumo)
    print("inventário:", json.dumps(resumo, ensure_ascii=False))


def run_status(src):
    agg, pub = parse_status(src)
    if not agg:
        raise RuntimeError("estado vazio — não foi gravado")
    month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    path = OUT / "ocupacao" / f"{month}.json"
    acc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"amostras": 0, "ultima": None, "locais": {}}
    if acc.get("ultima") == pub:
        print("estado repetido (mesma publicação) — ignorado"); return
    for sid, (n, busy, down) in agg.items():
        a = acc["locais"].setdefault(sid, [0, 0, 0])
        a[0] += n; a[1] += busy; a[2] += down
    acc["amostras"] += 1; acc["ultima"] = pub
    write_json(path, acc)
    tot = [sum(v[i] for v in agg.values()) for i in range(3)]
    print(f"estado {pub}: {tot[0]} tomadas, {tot[1]} ocupadas ({tot[1]/max(1,tot[0]):.1%}), {tot[2]} indisponíveis")


def main(argv):
    if "--from-files" in argv:
        i = argv.index("--from-files"); infra, status = argv[i + 1], argv[i + 2]
        os.environ["ALLOW_SMALL"] = "1"
        run_infra(infra); run_status(status); return
    errors = []
    try:
        run_status(__import__("io").BytesIO(fetch(STATUS_URL)))
    except Exception as e:
        errors.append(f"estado: {e}")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    resumo = OUT / "resumo.json"
    need_infra = "--infra" in argv or not resumo.exists() or json.loads(resumo.read_text()).get("recolhido", "")[:10] != today
    if need_infra:
        try:
            run_infra(__import__("io").BytesIO(fetch(INFRA_URL)))
        except Exception as e:
            errors.append(f"inventário: {e}")
    if errors:
        print("AVISO:", " | ".join(errors))
        if len(errors) == 2 or (need_infra and not resumo.exists()):
            sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
