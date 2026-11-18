"""Convertit cotes-fr/<jour>.json au format du site (collection cotes_fr, doc <jour>). Usage: cotes_fr_site.py <dossier cotes-fr> <jour> <sortie.json>"""
import json, sys
d = json.load(open(f"{sys.argv[1]}/{sys.argv[2]}.json"))
code = {"même marché en France": "ok", "ligne différente en France": "ligne", "marché non proposé en France par notre source": "nm",
        "compétition non couverte par notre source FR": "nc"}
pos = {}
for k, r in d["positions"].items():
    _, _, _, mid, pari = k.split("|", 4)
    v = {"e": code.get(r.get("etat"), "ni")}
    if r.get("cote_fr_moyenne"): v["m"] = r["cote_fr_moyenne"]
    if (r.get("cotes_fr") or {}).get("winamax_fr"): v["w"] = r["cotes_fr"]["winamax_fr"]
    if f"{mid}|{pari}" not in pos or v["e"] == "ok": pos[f"{mid}|{pari}"] = v
json.dump({"jour": d["jour"], "maj": d.get("maj_winamax") if (d.get("maj_winamax") or "") > (d.get("maj") or "") else d.get("maj"), "pos": pos}, open(sys.argv[3], "w"), ensure_ascii=False)
print(len(pos), "positions")
