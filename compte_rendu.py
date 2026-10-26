"""Compte rendu d'équipe : ce que deviennent nos possessions, d'où elles partent, rendement en
Zone Gold, vitesse de ruck, discipline, réaction après un score.

Tout est lu dans les instances XML déjà codées (convention 2026), SANS codage supplémentaire.
Les codes d'équipe sont des fenêtres vidéo de durée fixe posées autour de l'action : on retrouve
l'instant de l'action par un décalage mesuré sur les 5 matchs (bornes des possessions) :
  PDB / DISCIPLINES : début + 15 s   (fenêtre de 25 / 20 s)
  Essai             : début + 20 s   (fenêtre de 23 s)
  Jeu au pied       : début + 13,5 s (fenêtre de 20 s)
  Touches / Mêlées  : la possession démarre DANS la fenêtre (début + 6 à 10 s)
  RCE (réception)   : la possession démarre vers début + 4 s
Zones (convention UBB) : GOLD / RUMBLE / COP / ROUGE = 11 / 36 / 64 / 89 m de la ligne adverse.
Zone nue = ballon à nous dans notre sens ; suffixe « A » ou préfixe adverse = ballon à eux.
"""
from collections import Counter, defaultdict

from parser import _normalize_tag, _detect_adverse_token, _scoring_events, detect_half_time, compute_zone_gold_log, _is_bip_code

DECALAGE = {"PDB": 15.0, "DISCIPLINES": 15.0, "ESSAI": 20.0, "JEU AU PIED": 13.5, "BREAK": 13.0, "CLIC": 13.0}
ZONES = ("GOLD", "RUMBLE", "COP", "ROUGE")
BANDES = ["Nos 22", "Notre moitié", "Leur moitié", "Leurs 22"]
# Bande du terrain vue de chez nous, selon la zone et l'équipe qui a le ballon.
_BANDE_NOUS = {"ROUGE": "Nos 22", "COP": "Notre moitié", "RUMBLE": "Leur moitié", "GOLD": "Leurs 22"}
_BANDE_EUX = {"GOLD": "Nos 22", "RUMBLE": "Notre moitié", "COP": "Leur moitié", "ROUGE": "Leurs 22"}

ORIGINES = ["Touche", "Mêlée", "Renvoi reçu", "Ballon récupéré", "Jeu au pied adverse", "Pénalité jouée vite", "Jeu continu / autre"]
ISSUES = ["Essai", "Pénalité obtenue", "Jeu au pied", "Ballon perdu", "Pénalité concédée", "Autre arrêt"]
ISSUE_TON = {"Essai": "pos", "Pénalité obtenue": "pos", "Jeu au pied": "neu", "Ballon perdu": "neg",
             "Pénalité concédée": "neg", "Autre arrêt": "neu"}
VITESSES = [("-2", "Rapide (< 2 s)"), ("-4", "Moyen (2 à 4 s)"), ("+4", "Lent (> 4 s)"), ("PERDU", "Ruck perdu")]
PHASES_PEN = {"DEF": "Défense", "OFF": "Attaque", "CONQUETE": "Conquête", "AUTRES": "Autres"}


def _labels(inst, groupe):
    return [(l.get("text") or "").strip() for l in inst.get("labels") or [] if _normalize_tag(l.get("group")) == groupe]


def _minute_fn(ins):
    """Instant vidéo -> minute de match (1re période depuis le 1er coup d'envoi, 2e depuis la reprise)."""
    bip = sorted(float(i.get("start") or 0) for i in ins if _is_bip_code(i.get("code_raw")))
    if not bip:
        return lambda t: None
    mt = detect_half_time(ins)
    k1 = bip[0]
    k2 = next((b for b in bip if mt is not None and b > mt), None)

    def minute(t):
        if mt is not None and k2 is not None and t >= mt:
            return int(min(max(40 + (t - k2) / 60.0, 40), 99)) + 1
        return int(min(max((t - k1) / 60.0, 0), 45)) + 1
    return minute


class _Match:
    """Ligne du temps d'un match : événements d'équipe (nous / eux), zones, possessions."""

    def __init__(self, m):
        ins = m["instances"] or []
        self.m = m
        self.adv = _normalize_tag(_detect_adverse_token(ins) or "ADV")
        self.ev = defaultdict(list)
        self.zones = []
        for i in ins:
            t = _normalize_tag(i.get("code_raw")).split()
            if not t:
                continue
            s = float(i.get("start") or 0)
            e = float(i.get("end") or s)
            z = None
            if len(t) == 1 and t[0] in ZONES:
                z = _BANDE_NOUS[t[0]]
            elif len(t) == 2 and t[0] in ZONES and t[1] in ("A", "ADV"):
                z = _BANDE_EUX[t[0]]
            elif len(t) == 2 and t[1] in ZONES and (t[0] == self.adv or t[0] in ("ADV", "ADVERSE")):
                z = _BANDE_EUX[t[1]]
            if z:
                self.zones.append((s, e, z, i))
                continue
            side = "own" if t[0] == "UBB" else ("adv" if (t[0] == self.adv or t[0] in ("ADV", "ADVERSE")) else None)
            if side and len(t) >= 2:
                cat = " ".join(t[1:])
                self.ev[(side, cat)].append({"s": s, "e": e, "t": s + DECALAGE.get(cat, (e - s) / 2), "inst": i})
        for k in self.ev:
            self.ev[k].sort(key=lambda x: x["s"])
        self.zones.sort(key=lambda z: z[0])
        self.minute = _minute_fn(ins)
        self.scores = []
        for sc in _scoring_events(ins, _detect_adverse_token(ins)):
            s = float(sc["start"] or 0)
            t = s + (DECALAGE["ESSAI"] if sc["kind"] == "tries" else 5.0)
            self.scores.append({"side": "own" if sc["side"] == "own" else "adv", "kind": sc["kind"], "pts": sc["points"], "t": t})
        self.scores.sort(key=lambda x: x["t"])
        self.poss = [{"s": p["s"], "e": p["e"]} for p in self.ev[("own", "POSSESSION")]]
        self.poss_adv = [{"s": p["s"], "e": p["e"]} for p in self.ev[("adv", "POSSESSION")]]
        self._possessions()

    # --- outils -----------------------------------------------------------------------------
    def zone_at(self, t):
        best = None
        for s, e, z, _i in self.zones:
            if s - 0.5 <= t <= e + 0.5 and (best is None or s >= best[0]):
                best = (s, z)
        if best:
            return best[1]
        avant = [(e, z) for s, e, z, _i in self.zones if e <= t and t - e <= 4]
        return max(avant)[1] if avant else None

    def poss_at(self, t, avant=1.0, apres=3.0):
        """Notre possession dont la fin est la plus proche de t (t dans [début - avant, fin + apres])."""
        c = [p for p in self.poss if p["s"] - avant <= t <= p["e"] + apres]
        return min(c, key=lambda p: abs(p["e"] - t)) if c else None

    # --- possessions : origine, issue, points, zones ----------------------------------------
    def _possessions(self):
        P = self.poss
        for k, p in enumerate(P):
            p["origine"] = self._origine(p["s"])
            p["issue"] = "Autre arrêt"
            p["pts"] = 0
            p["essais"] = 0
            p["z0"] = self.zone_at(p["s"] + 0.5)
            p["z1"] = self.zone_at(max(p["s"], p["e"] - 0.5))
            p["minute"] = self.minute(p["s"])
            p["rucks"] = []
            p["fin"] = P[k + 1]["s"] if k + 1 < len(P) else 1e12
        # Issue : essai ou pénalité obtenue d'abord ; sinon la PREMIÈRE action qui arrête la possession
        # (une pénalité concédée juste après un ballon perdu appartient à la défense qui suit).
        cands = defaultdict(list)
        for cle, issue in ((("adv", "DISCIPLINES"), "Pénalité obtenue"), (("own", "DISCIPLINES"), "Pénalité concédée"),
                           (("own", "PDB"), "Ballon perdu"), (("own", "JEU AU PIED"), "Jeu au pied")):
            for ev in self.ev[cle]:
                p = self.poss_at(ev["t"])
                if p is not None:
                    cands[id(p)].append((ev["t"], issue))
        for p in P:
            c = cands.get(id(p))
            if not c:
                continue
            if any(i == "Pénalité obtenue" for _t, i in c):
                p["issue"] = "Pénalité obtenue"
            else:
                p["issue"] = min(c)[1]
        # Points : toute action de score à nous entre le début de la possession et le début de la suivante.
        for sc in self.scores:
            if sc["side"] != "own" or not sc["pts"]:
                continue
            p = next((p for p in P if p["s"] - 1 <= sc["t"] < p["fin"]), None)
            if p is not None:
                p["pts"] += sc["pts"]
                p["essais"] += 1 if sc["kind"] == "tries" else 0
        # Essai : la possession où l'essai est marqué (le code Essai est posé un peu après la fin de possession).
        for p in P:
            if p["essais"]:
                p["issue"] = "Essai"
        for ev in self.ev[("own", "RUCK RUMBLE")]:
            v = _labels(ev["inst"], "VITESSE RUCK")
            if not v:
                continue
            mid = (ev["s"] + ev["e"]) / 2
            p = next((p for p in P if p["s"] - 1 <= mid <= p["e"] + 1), None)
            if p is not None:
                p["rucks"].append({"v": _normalize_tag(v[0]), "t": mid})

    def _origine(self, s):
        for cle, nom in ((("own", "TOUCHES"), "Touche"), (("own", "MELEES"), "Mêlée")):
            if any(ev["s"] + 2 <= s <= ev["e"] + 1 for ev in self.ev[cle]):
                return nom
        if any(ev["s"] - 1 <= s <= ev["s"] + 12 for ev in self.ev[("own", "RCE")]):
            return "Renvoi reçu"
        if any(s - 4 <= ev["t"] <= s + 2 for ev in self.ev[("adv", "PDB")]):
            return "Ballon récupéré"
        if any(s - 4 <= ev["t"] <= s + 2 for ev in self.ev[("adv", "JEU AU PIED")]):
            return "Jeu au pied adverse"
        if any(s - 90 <= ev["t"] <= s + 2 for ev in self.ev[("adv", "DISCIPLINES")]):
            return "Pénalité jouée vite"
        return "Jeu continu / autre"

    # --- discipline --------------------------------------------------------------------------
    def penalites(self):
        joueurs = []
        for i in self.m["instances"] or []:
            code = _normalize_tag(i.get("code_raw"))
            if not code or code.split()[0] in ("UBB", self.adv, "ADV", "ADVERSE"):
                continue
            if _labels(i, "DISCIPLINES"):
                joueurs.append((float(i.get("start") or 0), i.get("code_raw")))
        out = []
        evs = self.ev[("own", "DISCIPLINES")]
        deja = set()
        for k, ev in enumerate(evs):
            ph = [_normalize_tag(x) for x in _labels(ev["inst"], "DISCIPLINES")]
            phase = PHASES_PEN.get(ph[0], ph[0].capitalize()) if ph else "Non précisé"
            cands = [(abs(js - ev["s"] - 6), nom) for js, nom in joueurs if ev["s"] - 2 <= js <= ev["s"] + 16]
            joueur = min(cands)[1] if cands else None
            # Coût : points adverses marqués avant qu'on récupère le ballon (et avant notre faute suivante),
            # 2 min 30 de vidéo au plus ; chaque point n'est compté qu'une fois.
            prochaine = next((p["s"] for p in self.poss if p["s"] > ev["t"] + 1), 1e12)
            faute_suiv = evs[k + 1]["t"] if k + 1 < len(evs) else 1e12
            borne = min(prochaine + 2, faute_suiv, ev["t"] + 150)
            cout = 0
            for j, sc in enumerate(self.scores):
                if sc["side"] == "adv" and sc["pts"] and ev["t"] - 1 <= sc["t"] <= borne and j not in deja:
                    cout += sc["pts"]
                    deja.add(j)
            out.append({"t": ev["s"], "minute": self.minute(ev["t"]), "phase": phase, "zone": self.zone_at(ev["t"]),
                        "joueur": joueur.title() if joueur else None, "cout": cout})
        return out

    # --- après un score -----------------------------------------------------------------------
    def apres_scores(self):
        """Pour chaque score (essai + transfo regroupés), ce qui se passe ensuite."""
        groupes = []
        for sc in self.scores:
            if not sc["pts"]:
                continue
            g = groupes[-1] if groupes else None
            if g and g["side"] == sc["side"] and sc["t"] - g["t"] < 150:
                g["pts"] += sc["pts"]
                g["t"] = sc["t"]
            else:
                groupes.append({"side": sc["side"], "pts": sc["pts"], "t": sc["t"]})
        out = []
        for g in groupes:
            fen = g["t"] + 300  # 5 minutes de vidéo
            suivant = next((sc for sc in self.scores if sc["t"] > g["t"] + 30 and sc["pts"]), None)
            pris = sum(sc["pts"] for sc in self.scores if sc["side"] == "adv" and g["t"] + 30 < sc["t"] <= fen)
            marques = sum(sc["pts"] for sc in self.scores if sc["side"] == "own" and g["t"] + 30 < sc["t"] <= fen)
            r = {"side": g["side"], "pts": g["pts"], "minute": self.minute(g["t"]), "pris5": pris, "marques5": marques,
                 "suivant": (suivant["side"] if suivant else None)}
            if g["side"] == "own":
                # On reçoit le renvoi : notre première possession derrière.
                rce = next((ev for ev in self.ev[("own", "RCE")] if ev["s"] > g["t"]), None)
                p = next((p for p in self.poss if rce and rce["s"] - 1 <= p["s"] <= rce["e"] + 2), None) if rce else None
                r["premiere"] = p["issue"] if p else None
                r["zone_sortie"] = p["z1"] if p else None
            else:
                # On renvoie : avons-nous récupéré le ballon sur le renvoi ?
                ce = next((ev for ev in self.ev[("own", "CE")] if ev["s"] > g["t"]), None)
                if ce:
                    prem_nous = next((p["s"] for p in self.poss if p["s"] >= ce["s"]), 1e12)
                    prem_eux = next((p["s"] for p in self.poss_adv if p["s"] >= ce["s"]), 1e12)
                    r["recupere"] = prem_nous < prem_eux if (prem_nous < 1e12 or prem_eux < 1e12) else None
                else:
                    r["recupere"] = None
            out.append(r)
        return out


def _pct(a, b):
    return round(100 * a / b) if b else None


def compte_rendu(selected):
    """Calcul complet sur les matchs choisis (un seul en mode match)."""
    ms = [_Match(m) for m in selected if m.get("instances")]
    nb = len(ms) or 1
    P = [p for x in ms for p in x.poss]

    # 2. Issues des possessions
    issues = Counter(p["issue"] for p in P)
    tot = len(P)
    issues_l = [{"nom": n, "n": issues.get(n, 0), "pct": _pct(issues.get(n, 0), tot), "ton": ISSUE_TON[n]} for n in ISSUES]
    pts_tot = sum(p["pts"] for p in P)
    perte_zone = Counter(p["z1"] or "Zone non codée" for p in P if p["issue"] == "Ballon perdu")
    pertes_zone = [{"z": z, "n": perte_zone.get(z, 0)} for z in BANDES + ["Zone non codée"] if perte_zone.get(z)]
    par_zone_depart = []
    for z in BANDES:
        lst = [p for p in P if p["z0"] == z]
        if lst:
            par_zone_depart.append({"z": z, "n": len(lst), "pts": round(sum(p["pts"] for p in lst) / len(lst), 2),
                                    "essai": _pct(sum(1 for p in lst if p["issue"] == "Essai"), len(lst)),
                                    "perdu": _pct(sum(1 for p in lst if p["issue"] in ("Ballon perdu", "Pénalité concédée")), len(lst))})

    # 3. Origine x issue
    origines = []
    for o in ORIGINES:
        lst = [p for p in P if p["origine"] == o]
        if not lst:
            continue
        c = Counter(p["issue"] for p in lst)
        origines.append({"nom": o, "n": len(lst), "par_match": round(len(lst) / nb, 1),
                         "pts": round(sum(p["pts"] for p in lst) / len(lst), 2),
                         "essais": sum(p["essais"] for p in lst),
                         "issues": {n: _pct(c.get(n, 0), len(lst)) for n in ISSUES}})
    utiles = [o for o in origines if o["n"] >= 5]
    meilleure = max(utiles, key=lambda o: o["pts"]) if utiles else None
    pire = min(utiles, key=lambda o: o["pts"]) if utiles else None

    # 4. Zone Gold
    gold = {"entrees": 0, "points": 0, "res": Counter(), "orig": defaultdict(lambda: [0, 0]), "liste": [], "avec_points": 0, "ratios": []}
    for x in ms:
        g = (compute_zone_gold_log(x.m["instances"]) or {}).get("own") or {}
        for r in g.get("rows") or []:
            gold["entrees"] += 1
            pts = r.get("points") or 0
            gold["avec_points"] += 1 if pts else 0
            res = (r.get("result_display") or r.get("result") or "?").capitalize()
            gold["res"][res] += 1
            s = float(r.get("start") or 0)
            p = next((p for p in x.poss if p["s"] - 2 <= s <= p["e"] + 1), None)
            o = p["origine"] if p else "Jeu continu / autre"
            gold["orig"][o][0] += 1
            gold["orig"][o][1] += pts
            gold["liste"].append({"adv": x.m.get("opponent") or "?", "minute": x.minute(s), "res": res,
                                  "ton": r.get("result_class"), "pts": pts, "origine": o})
        if g.get("points") is not None:
            gold["points"] += g["points"]
        if g.get("points_par_entree") is not None:
            gold["ratios"].append(g["points_par_entree"])
    # Même lecture que la page Zone Gold saison : moyenne des points / entrée de chaque match.
    gold["pts_entree"] = round(sum(gold["ratios"]) / len(gold["ratios"]), 2) if gold["ratios"] else None
    gold["sans_points"] = gold["entrees"] - gold["avec_points"]
    gold["res_l"] = gold["res"].most_common()
    gold["orig_l"] = sorted(({"nom": o, "n": v[0], "pts": round(v[1] / v[0], 2)} for o, v in gold["orig"].items()), key=lambda r: -r["n"])
    gold["par_match"] = round(gold["entrees"] / nb, 1)
    del gold["orig"], gold["res"]

    # 5. Vitesse de ruck -> suite de la possession
    vit = []
    for code, nom in VITESSES:
        rows = [(p, r) for p in P for r in p["rucks"] if r["v"] == code]
        if not rows:
            continue
        poss = {id(p): p for p, _r in rows}.values()
        n = len(rows)
        c = Counter(p["issue"] for p, _r in rows)
        vit.append({"code": code, "nom": nom, "n": n, "pct_rucks": None,
                    "essai": _pct(c.get("Essai", 0), n), "pen": _pct(c.get("Pénalité obtenue", 0), n),
                    "perdu": _pct(c.get("Ballon perdu", 0) + c.get("Pénalité concédée", 0), n),
                    "pied": _pct(c.get("Jeu au pied", 0), n),
                    "pts": round(sum(p["pts"] for p, _r in rows) / n, 2)})
    tr = sum(v["n"] for v in vit)
    for v in vit:
        v["pct_rucks"] = _pct(v["n"], tr)

    # 6. Discipline
    pens = []
    for x in ms:
        for r in x.penalites():
            r["adv"] = x.m.get("opponent") or "?"
            pens.append(r)
    pens.sort(key=lambda r: (r["adv"], r["minute"] or 0))
    pen_phase = Counter(r["phase"] for r in pens).most_common()
    pen_zone = [{"z": z, "n": sum(1 for r in pens if r["zone"] == z)} for z in BANDES]
    pen_zone = [r for r in pen_zone if r["n"]]
    tranches = ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80"]
    pen_tr = Counter(tranches[min((r["minute"] - 1) // 10, 7)] for r in pens if r["minute"])
    pen_tranches = [{"t": t, "n": pen_tr.get(t, 0)} for t in tranches]
    pen_joueurs = Counter(r["joueur"] for r in pens if r["joueur"]).most_common(6)
    cout_tot = sum(r["cout"] for r in pens)
    pts_encaisses = sum(sc["pts"] for x in ms for sc in x.scores if sc["side"] == "adv")
    pen_coutantes = sum(1 for r in pens if r["cout"])

    # 7. Après un score
    ap = [r for x in ms for r in x.apres_scores()]
    nous = [r for r in ap if r["side"] == "own"]
    eux = [r for r in ap if r["side"] == "adv"]
    apres = {
        "nous_n": len(nous),
        "nous_enchaine": sum(1 for r in nous if r["suivant"] == "own"),
        "nous_rend": sum(1 for r in nous if r["suivant"] == "adv"),
        "nous_pris5": sum(r["pris5"] for r in nous),
        "nous_pris5_n": sum(1 for r in nous if r["pris5"]),
        "nous_premiere": Counter(r.get("premiere") or "Non retrouvée" for r in nous).most_common(),
        "eux_n": len(eux),
        "eux_recup": sum(1 for r in eux if r.get("recupere")),
        "eux_recup_n": sum(1 for r in eux if r.get("recupere") is not None),
        "eux_reponse": sum(1 for r in eux if r["suivant"] == "own"),
        "eux_encore": sum(1 for r in eux if r["suivant"] == "adv"),
        "eux_marques5": sum(r["marques5"] for r in eux),
        "eux_pris5": sum(r["pris5"] for r in eux),
    }

    return {"nb": len(ms), "possessions": tot, "poss_par_match": round(tot / nb, 1), "pts_par_poss": round(pts_tot / tot, 2) if tot else None,
            "pts_tot": pts_tot, "issues": issues_l, "issues_items": [(r["nom"], r["n"]) for r in issues_l], "pertes_zone": pertes_zone, "par_zone_depart": par_zone_depart,
            "zones_codees": any(x.zones for x in ms),
            "origines": origines, "meilleure": meilleure, "pire": pire, "gold": gold,
            "vitesse": vit, "rucks": tr,
            "pens": pens, "pen_phase": pen_phase, "pen_zone": pen_zone, "pen_tranches": pen_tranches, "pen_joueurs": pen_joueurs,
            "pen_cout": cout_tot, "pen_coutantes": pen_coutantes, "pts_encaisses": pts_encaisses,
            "pen_par_match": round(len(pens) / nb, 1), "apres": apres}
