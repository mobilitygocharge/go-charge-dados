"""Indicadores municipais de Espanha a partir da API Tempus3 do INE espanhol (servicios.ine.es).

  - População: Cifras oficiales del Padrón (tabela 29005), último ano
  - Renda neta média por pessoa: Atlas de distribución de renta de los hogares (54 tabelas provinciais)
  - % de habitações turísticas sobre o total de habitações: Viviendas turísticas (tabela 39366), último mês
Uso:  python scripts/es_ine.py   → data/es/municipios_ine.json
"""
import json, os, sys, time, datetime as dt
from pathlib import Path
import requests

API = "https://servicios.ine.es/wstempus/js/ES/"
OUT = Path(os.environ.get("DATA_DIR", "data")) / "es"
UA = {"User-Agent": "go-charge-dados/1.0 (prospecao de mercado)"}

def get(path, tries=6):
    last = None
    for i in range(tries):
        try:
            r = requests.get(API + path, headers=UA, timeout=240)
            if r.ok:
                j = r.json()
                if isinstance(j, list): return j
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{path}: {last}")

def meta(s, var):
    for m in s.get("MetaData") or []:
        if m.get("T3_Variable") == var: return m
    return None

def first_val(s):
    d = (s.get("Data") or [None])[0]
    return (d or {}).get("Valor"), (d or {}).get("Anyo")

def main():
    out, info = {}, {}
    # 1. população
    for s in get("DATOS_TABLA/29005?nult=1&tip=AM"):
        m, sx, tm = meta(s, "Municipios"), meta(s, "Sexo"), meta(s, "Tamaño de los municipios")
        if not m or not sx or sx.get("Codigo") != "0" or (tm and tm.get("Codigo") != "0"): continue
        v, y = first_val(s)
        if v is None or not str(m.get("Codigo", "")).isdigit() or len(m["Codigo"]) != 5: continue
        out[m["Codigo"]] = {"nome": m.get("Nombre"), "pop": int(v)}; info["pop_ano"] = y
    print("municípios com população:", len(out))
    # 2. renda
    tabs = [t["Id"] for t in get("TABLAS_OPERACION/353") if "renta media y mediana" in t.get("Nombre", "").lower()]
    falhas = []
    for tid in tabs:
        try: data = get(f"DATOS_TABLA/{tid}?nult=1&tip=AM")
        except Exception as e: falhas.append(tid); print("falhou", tid, e); continue
        for s in data:
            m, sc = meta(s, "Municipios"), meta(s, "SALDOS CONTABLES")
            if not m or not sc or sc.get("Nombre") != "Renta neta media por persona": continue
            cod = str(m.get("Codigo", ""))
            if len(cod) != 5 or cod not in out: continue
            v, y = first_val(s)
            if v is not None: out[cod]["renda"] = v; info["renda_ano"] = y
        time.sleep(1)
    print("com renda:", sum(1 for x in out.values() if "renda" in x), "| tabelas falhadas:", falhas)
    # 3. habitação turística
    for s in get("DATOS_TABLA/39366?nult=1&tip=AM"):
        m = meta(s, "Municipios"); cod = str((m or {}).get("Codigo", ""))
        if cod in out:
            v, y = first_val(s)
            if v is not None: out[cod]["vt_pct"] = v; info["vt_ano"] = y
    print("com habitação turística:", sum(1 for x in out.values() if "vt_pct" in x))
    if len(out) < 8000 or sum(1 for x in out.values() if "renda" in x) < 7000:
        sys.exit("Dados incompletos — ficheiro anterior mantido")
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"fonte": "INE (Espanha) — API Tempus3: Padrón (29005), ADRH renta neta media por persona, Viviendas turísticas (39366)",
           "recolhido": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **info, "tabelas_renda_falhadas": falhas, "municipios": out}
    (OUT / "municipios_ine.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("gravado data/es/municipios_ine.json")

if __name__ == "__main__":
    main()
