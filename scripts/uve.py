"""Utilização da rede pública por município — Observatório da Mobilidade Elétrica (UVE), dados EGME/MOBI.E.

O observatório (observatorio.uve.pt) publica relatórios Power BI "publicar na Web" (públicos). Este script
faz as mesmas consultas que o relatório "Rede" faz ao abrir, e grava os números por município e por mês:
horas em carregamento, horas totais (taxa de utilização = carga / total), tomadas e tomadas rápidas.

Uso:  python scripts/uve.py            → data/uve/municipios.json
Fonte: Observatório da Mobilidade Elétrica (UVE), com dados EGME. Uso: indicadores agregados, citar a fonte.
"""
import json, os, sys, time, uuid, datetime as dt
from pathlib import Path

REPORT_KEY = os.environ.get("UVE_REDE_KEY", "407e0371-866f-4112-92f8-df476dac127e")   # relatório "Rede"
CLUSTERS = ["https://wabi-west-europe-b-primary-api.analysis.windows.net",
            "https://wabi-west-europe-d-primary-api.analysis.windows.net",
            "https://wabi-north-europe-redirect.analysis.windows.net"]
OUT = Path(os.environ.get("DATA_DIR", "data")) / "uve"
UA = "go-charge-dados/1.0 (prospecao de mercado; recolha mensal)"
FIELDS = ["MUNICIPIO_ID", "MUNICIPIO_DSC", "DISTRITO_DSC", "N_SEQ_MES", "MÊS"]
SUMS = ["hours_charging", "TOTAL", "N_TOMADAS", "N_TOMADAS_FAST"]


def headers():
    return {"X-PowerBI-ResourceKey": REPORT_KEY, "Content-Type": "application/json;charset=UTF-8",
            "ActivityId": str(uuid.uuid4()), "RequestId": str(uuid.uuid4()), "User-Agent": UA}


def query_body(model_id):
    col = lambda n: {"Column": {"Expression": {"SourceRef": {"Source": "m"}}, "Property": n}, "Name": "m." + n}
    agg = lambda n: {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "m"}}, "Property": n}}, "Function": 0},
                     "Name": "Sum(m." + n + ")"}
    sel = [col(f) for f in FIELDS] + [agg(f) for f in SUMS]
    return {"version": "1.0.0", "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
        "Query": {"Version": 2, "From": [{"Name": "m", "Entity": "T08_MUNICIPIO", "Type": 0}], "Select": sel},
        "Binding": {"Primary": {"Groupings": [{"Projections": list(range(len(sel)))}]},
                    "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": 30000}}}, "Version": 1}}}]},
        "QueryId": ""}], "cancelQueries": [], "modelId": model_id}


def decode_dsr(dsr):
    """Descomprime o formato DSR do Power BI: R = bits de colunas repetidas da linha anterior,
    Ø = bits de colunas nulas; colunas com DN são índices num dicionário de valores."""
    ds = dsr["DS"][0]
    if ds.get("RT"): print("AVISO: resposta truncada (há mais linhas do que a janela pedida)", file=sys.stderr)
    dicts = ds.get("ValueDicts", {})
    rows, schema, prev = [], None, None
    for row in ds["PH"][0].get("DM0", []):
        if "S" in row: schema = row["S"]
        n = len(schema); rep, nul = row.get("R", 0), row.get("Ø", 0)
        vals, it = [], iter(row.get("C", []))
        for i in range(n):
            if rep >> i & 1: v = prev[i]
            elif nul >> i & 1: v = None
            else:
                v = next(it)
                dn = schema[i].get("DN")
                if dn is not None and isinstance(v, int): v = dicts[dn][v]
            vals.append(v)
        prev = vals; rows.append(vals)
    return rows


def fnum(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def build(rows):
    munis = {}
    for r in rows:
        dico, nome, dist, seq, mes, carga, total, tom, fast = r
        carga, total = fnum(carga), fnum(total)
        if not dico or not mes or not total: continue          # mês ainda sem dados
        m = munis.setdefault(str(dico).zfill(4), {"nome": nome, "distrito": dist, "serie": []})
        ym = str(int(fnum(mes)))
        m["serie"].append({"mes": f"{ym[:4]}-{ym[4:6]}", "carga_h": round(carga or 0, 1), "total_h": round(total, 1),
                           "util": round((carga or 0) / total, 4), "tomadas": int(fnum(tom) or 0), "tomadas_rapidas": int(fnum(fast) or 0)})
    for m in munis.values(): m["serie"].sort(key=lambda x: x["mes"])
    meses = sorted({x["mes"] for m in munis.values() for x in m["serie"]})
    return {"fonte": "Observatório da Mobilidade Elétrica (UVE) — dados EGME/MOBI.E", "relatorio": "Rede · Municípios",
            "recolhido": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "meses": meses, "n_municipios": len(munis), "municipios": munis}


def fetch():
    import requests
    last = None
    for base in CLUSTERS:
        try:
            r = requests.get(f"{base}/public/reports/{REPORT_KEY}/modelsAndExploration?preferReadOnlySession=true", headers=headers(), timeout=60)
            r.raise_for_status(); model_id = r.json()["models"][0]["id"]
            q = requests.post(f"{base}/public/reports/querydata?synchronous=true", headers=headers(), data=json.dumps(query_body(model_id)), timeout=180)
            q.raise_for_status(); res = q.json()["results"][0]["result"]
            if "error" in res or "dsr" not in res.get("data", {}): raise RuntimeError(json.dumps(res)[:400])
            return res["data"]["dsr"]
        except Exception as e:
            last = f"{base}: {e}"; print("falhou", last, file=sys.stderr); time.sleep(5)
    raise RuntimeError(last)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture":       # teste sem rede
        out = build(decode_dsr(json.load(open(sys.argv[2], encoding="utf-8"))))
        print(json.dumps(out, ensure_ascii=False)[:1500]); return out
    out = build(decode_dsr(fetch()))
    if out["n_municipios"] < 250 or len(out["meses"]) < 6:
        sys.exit(f"Dados incompletos ({out['n_municipios']} municípios, {len(out['meses'])} meses) — ficheiro anterior mantido")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "municipios.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    last = out["meses"][-1]
    u = [m["serie"][-1] for m in out["municipios"].values() if m["serie"] and m["serie"][-1]["mes"] == last]
    nat = sum(x["carga_h"] for x in u) / max(1, sum(x["total_h"] for x in u))
    print(f"{out['n_municipios']} municípios, {len(out['meses'])} meses ({out['meses'][0]} a {last}); utilização nacional {last}: {nat:.1%}")


if __name__ == "__main__":
    main()
