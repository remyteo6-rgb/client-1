"""Barème de notation des joueurs (note sur 10, par poste).

Principe : chaque critère du barème donne une sous-note de 0 à 10 ; la note de base est la
moyenne pondérée des sous-notes disponibles (un critère sans assez d'actions est ignoré et
son poids est redistribué) ; puis on retire les malus (pénalités, cartons, ballons perdus) et
on ajoute les bonus (essais). Tout est ramené à 80 minutes de jeu pour les volumes.

Deux types de critères :
  - "vol" : valeur par 80 min ; la cible = la valeur qui vaut 10/10.
  - "pct" : réussite en %, de `plancher` (0/10) à `cible` (10/10) ; ignoré sous `min_n` actions.
Les poids et les cibles ci-dessous sont les valeurs de départ ; elles se règlent dans
Saison → Notes → Barème (valeurs enregistrées en base, prioritaires)."""

MIN_MINUTES = 30      # en dessous, le joueur n'est pas noté sur le match
MALUS = {"penalite": 0.3, "penalite_max": 1.5, "jaune": 0.7, "rouge": 2.0, "pdb": 0.05, "pdb_max": 0.5, "total_max": 3.0}
# Passes ratées : réussir une passe est normal (aucun bonus), la rater coûte, surtout aux 9/10.
PASSES_RATEES = {"Charnière": (0.4, 2.5), "Centre": (0.15, 0.75)}
PASSES_RATEES_DEFAUT = (0.1, 0.5)
BONUS = {"essai": 0.5, "essai_max": 1.0}

POSTES = ["Pilier", "Talonneur", "2ème ligne", "3ème ligne", "Charnière", "Centre", "Ailier/Arrière"]


def _v(row, *keys):
    return sum((row or {}).get(k) or 0 for k in keys)


def _pct_expl_auto(ruck, g, p, ga, pa, min_n):
    """Réussite d'après les descripteurs tagués à la main (g/p) s'il y en a assez, sinon d'après
    la réussite chronologique calculée (ga/pa). Renvoie (pourcentage, actions) ou (None, 0)."""
    for gg, pp in ((g, p), (ga, pa)):
        gagnes, perdus = _v(ruck, gg) if gg else 0, _v(ruck, pp) if pp else 0
        if gagnes + perdus >= min_n:
            return 100 * gagnes / (gagnes + perdus), gagnes + perdus
    return None, 0


# Extracteurs : (lignes joueur du match) -> (valeur brute | None, nb d'actions). Pour "vol" la valeur est
# le total du match (ramené à 80 min ensuite).
def _x_contact(r):      return _v(r["attack"], "contact_total"), 0
def _x_offload(r):      return _v(r["attack"], "offload_total"), 0
def _x_defb(r):         return _v(r["attack"], "def_battu"), 0
def _x_break(r):        return _v(r["attack"], "break"), 0
def _x_soutien(r):      return _v(r["attack"], "soutien_total"), 0
def _x_passes(r):       return _v(r["attack"], "passes"), 0
def _x_tackle(r):       return _v(r["defense"], "plaquage_tentes"), 0
def _x_recup(r):        return _v(r["defense"], "ballons_recuperes", "combattant_total"), 0
def _x_jap(r):          return _v(r["ruck"], "jap"), 0
def _x_sauteur(r):      return _v(r["ruck"], "sauteur"), 0
def _x_lifteur(r):      return _v(r["ruck"], "lifteur"), 0
def _x_lanceur(r):      return _v(r["ruck"], "lanceur"), 0
def _x_melee(r):        return _v(r["ruck"], "melee_conquete"), 0


def _p_ratio(num_keys, den_keys, table, min_n):
    def f(r):
        den = _v(r[table], *den_keys)
        if den < min_n:
            return None, den
        return 100 * _v(r[table], *num_keys) / den, den
    return f


_p_contact = _p_ratio(("contact_plus",), ("contact_total",), "attack", 4)
_p_offload = _p_ratio(("offload_plus", "offload_neutre"), ("offload_total",), "attack", 3)
_p_passes = _p_ratio(("passe_plus", "passe_neutre"), ("passes",), "attack", 8)


def _p_tackle(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    tent = _v(r["defense"], "plaquage_tentes")
    return (100 * made / tent, tent) if tent >= 4 else (None, tent)


def _p_tackle_dom(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    return (100 * _v(r["defense"], "plaquage_dominant") / made, made) if made >= 4 else (None, made)


def _p_melee_off(r):
    return _pct_expl_auto(r["ruck"], "melee_off_gagnee", "melee_off_perdue", "melee_gagne_auto", "melee_perdu_auto", 3)


def _p_melee_def(r):
    return _pct_expl_auto(r["ruck"], "melee_def_gagnee", "melee_def_perdue", None, None, 3)


def _p_lancer(r):
    return _pct_expl_auto(r["ruck"], "lancer_gagne", "lancer_perdu", "lanceur_gagne_auto", "lanceur_perdu_auto", 4)


def _p_sauteur(r):
    return _pct_expl_auto(r["ruck"], None, None, "sauteur_gagne_auto", "sauteur_perdu_auto", 4)


def _p_lifteur(r):
    return _pct_expl_auto(r["ruck"], None, None, "lifteur_gagne_auto", "lifteur_perdu_auto", 4)


def _p_rce(r):
    tot = _v(r["ruck"], "rce_plus", "rce_neutre", "rce_minus")
    return (100 * (_v(r["ruck"], "rce_plus") + 0.5 * _v(r["ruck"], "rce_neutre")) / tot, tot) if tot >= 3 else (None, tot)


def _p_aerien(r):
    tot = _v(r["ruck"], "duels_aer_total")
    return (100 * (_v(r["ruck"], "duels_aer_plus") + 0.5 * _v(r["ruck"], "duels_aer_neutre")) / tot, tot) if tot >= 2 else (None, tot)


def _p_kicks(r):
    tent = _v(r["ruck"], "transfo_tentees", "penalite_tentees", "drop_tentes")
    ok = _v(r["ruck"], "transfo_reussies", "penalite_reussies", "drop_reussis")
    return (100 * ok / tent, tent) if tent >= 2 else (None, tent)


# id -> (libellé, type, extracteur, valeurs par défaut de la cible / du plancher)
CRITERES = {
    "contact":      ("Contacts (volume)", "vol", _x_contact),
    "contact_dom":  ("Dominance au contact (%)", "pct", _p_contact),
    "offload":      ("Offloads (volume)", "vol", _x_offload),
    "offload_pct":  ("Offloads réussis (%)", "pct", _p_offload),
    "def_battu":    ("Défenseurs battus", "vol", _x_defb),
    "break":        ("Franchissements", "vol", _x_break),
    "soutien":      ("Soutiens au ruck", "vol", _x_soutien),
    "passes":       ("Passes (volume)", "vol", _x_passes),
    "passes_pct":   ("Passes propres (%)", "pct", _p_passes),
    "plaquage":     ("Plaquages (volume)", "vol", _x_tackle),
    "plaquage_pct": ("Plaquages réussis (%)", "pct", _p_tackle),
    "plaquage_dom": ("Plaquages dominants (%)", "pct", _p_tackle_dom),
    "recup":        ("Ballons récupérés / combat", "vol", _x_recup),
    "jap":          ("Jeu au pied (volume)", "vol", _x_jap),
    "sauteur":      ("Sauteur (volume)", "vol", _x_sauteur),
    "sauteur_pct":  ("Sauteur : touches gagnées (%)", "pct", _p_sauteur),
    "lifteur":      ("Lifteur (volume)", "vol", _x_lifteur),
    "lifteur_pct":  ("Lifteur : touches gagnées (%)", "pct", _p_lifteur),
    "lanceur":      ("Lancers en touche (volume)", "vol", _x_lanceur),
    "lancer_pct":   ("Lancers gagnés (%)", "pct", _p_lancer),
    "melee":        ("Mêlées (volume)", "vol", _x_melee),
    "melee_off_pct": ("Mêlée off : gagnées (%)", "pct", _p_melee_off),
    "melee_def_pct": ("Mêlée def : gagnées (%)", "pct", _p_melee_def),
    "rce_pct":      ("Réception coup d'envoi (%)", "pct", _p_rce),
    "aerien_pct":   ("Duels aériens (%)", "pct", _p_aerien),
    "kicks_pct":    ("Coups de pied au but réussis (%)", "pct", _p_kicks),
}

# Réglages de départ : poste -> {critère: (poids, cible, plancher)}. « cible » = valeur qui donne
# 10/10 (par 80 min pour un volume, en % pour un pourcentage) ; « plancher » = valeur qui donne 0/10
# (pourcentages seulement, 0 pour un volume).
BAREME_DEFAUT = {
    "Pilier": {
        "melee_off_pct": (14, 90, 40), "melee_def_pct": (6, 80, 30), "plaquage": (10, 9, 0),
        "plaquage_pct": (12, 95, 70), "contact": (8, 11, 0), "contact_dom": (8, 65, 30),
        "soutien": (10, 18, 0), "lifteur_pct": (8, 95, 50), "lifteur": (4, 6, 0),
        "recup": (4, 5, 0), "offload": (2, 3, 0)},
    "Talonneur": {
        "lancer_pct": (16, 95, 50), "lanceur": (4, 14, 0), "melee_off_pct": (10, 90, 40),
        "melee_def_pct": (4, 80, 30), "plaquage": (10, 9, 0), "plaquage_pct": (12, 95, 70),
        "contact": (8, 11, 0), "contact_dom": (8, 65, 30), "soutien": (8, 18, 0),
        "recup": (4, 5, 0), "offload": (2, 3, 0), "passes": (2, 6, 0)},
    "2ème ligne": {
        "sauteur_pct": (14, 95, 50), "sauteur": (8, 12, 0), "lifteur_pct": (8, 95, 50),
        "lifteur": (4, 8, 0), "plaquage": (10, 9, 0), "plaquage_pct": (12, 95, 70),
        "contact": (8, 11, 0), "contact_dom": (8, 65, 30), "soutien": (10, 18, 0),
        "recup": (6, 6, 0), "offload": (2, 3, 0)},
    "3ème ligne": {
        "plaquage": (10, 10, 0), "plaquage_pct": (12, 95, 70), "plaquage_dom": (6, 50, 10),
        "recup": (10, 8, 0), "contact": (8, 12, 0), "contact_dom": (8, 65, 30),
        "soutien": (8, 16, 0), "sauteur_pct": (6, 95, 50), "sauteur": (4, 10, 0),
        "lifteur_pct": (4, 95, 50), "def_battu": (6, 4, 0), "offload": (3, 3, 0), "break": (3, 2, 0)},
    "Charnière": {
        "passes": (6, 50, 0), "jap": (14, 12, 0),
        "kicks_pct": (10, 85, 25), "plaquage": (6, 5, 0), "plaquage_pct": (8, 95, 70),
        "soutien": (6, 8, 0), "contact": (4, 4, 0), "break": (4, 2, 0), "offload": (3, 2, 0),
        "recup": (3, 3, 0)},
    "Centre": {
        "contact": (8, 11, 0), "contact_dom": (10, 65, 30), "def_battu": (10, 5, 0),
        "break": (6, 3, 0), "plaquage": (10, 10, 0), "plaquage_pct": (12, 95, 70),
        "plaquage_dom": (4, 50, 10), "passes": (6, 15, 0),
        "offload": (6, 3, 0), "offload_pct": (3, 100, 50), "jap": (6, 8, 0), "recup": (4, 6, 0)},
    "Ailier/Arrière": {
        "break": (10, 4, 0), "def_battu": (10, 5, 0), "contact_dom": (8, 65, 30),
        "contact": (4, 9, 0), "plaquage": (8, 8, 0), "plaquage_pct": (12, 95, 70),
        "jap": (8, 5, 0), "rce_pct": (8, 75, 25), "aerien_pct": (8, 80, 30),
        "offload": (4, 3, 0), "passes": (2, 6, 0), "recup": (4, 4, 0), "kicks_pct": (4, 85, 25)},
}


def bareme_effectif(overrides=None):
    """Barème de départ + réglages enregistrés. overrides = {(poste, critère): (poids, cible, plancher)}."""
    out = {p: dict(c) for p, c in BAREME_DEFAUT.items()}
    for (poste, crit), val in (overrides or {}).items():
        if poste in out and crit in CRITERES:
            out[poste][crit] = val
    return out


def _clamp(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, x))


def note_match(poste, rows, minutes, jaunes=0, rouges=0, bareme=None):
    """Note /10 d'un joueur sur un match. `rows` = {"attack", "defense", "ruck"} (lignes du joueur
    pour ce match, éventuellement None). Renvoie None si pas assez de minutes ou de matière."""
    if minutes < MIN_MINUTES or poste not in POSTES:
        return None
    reglages = (bareme or BAREME_DEFAUT).get(poste) or {}
    k80 = 80.0 / minutes
    detail, tot_w, tot_s = [], 0.0, 0.0
    for crit, (poids, cible, plancher) in reglages.items():
        if poids <= 0 or crit not in CRITERES:
            continue
        label, kind, fn = CRITERES[crit]
        try:
            val, n = fn(rows)
        except Exception:
            val, n = None, 0
        if val is None:
            continue
        if kind == "vol":
            val80 = val * k80
            score = _clamp(10 * val80 / cible) if cible else 0.0
            shown = round(val80, 1)
        else:
            span = (cible - plancher) or 1
            score = _clamp(10 * (val - plancher) / span)
            shown = round(val)
        detail.append({"critere": crit, "label": label, "type": kind, "valeur": shown, "actions": n,
                       "score": round(score, 1), "poids": poids})
        tot_w += poids
        tot_s += poids * score
    if not detail:
        return None
    base = tot_s / tot_w
    attack = rows.get("attack")
    pen = _v(attack, "penalite_off", "penalite_def")
    pdb = _v(attack, "pdb")
    rates = _v(attack, "passe_minus")
    pr = PASSES_RATEES.get(poste, PASSES_RATEES_DEFAUT)
    essais = _v(rows.get("ruck"), "essai")
    malus = [("Pénalités concédées", min(pen * MALUS["penalite"], MALUS["penalite_max"]), pen),
             ("Cartons jaunes", jaunes * MALUS["jaune"], jaunes),
             ("Cartons rouges", rouges * MALUS["rouge"], rouges),
             ("Passes ratées", min(rates * pr[0], pr[1]), rates),
             ("Ballons perdus", min(pdb * MALUS["pdb"], MALUS["pdb_max"]), pdb)]
    malus = [{"label": l, "points": round(-p, 1), "n": n} for l, p, n in malus if p > 0]
    bonus_pts = min(essais * BONUS["essai"], BONUS["essai_max"])
    bonus = [{"label": "Essais", "points": round(bonus_pts, 1), "n": essais}] if bonus_pts > 0 else []
    total_malus = min(sum(-m["points"] for m in malus), MALUS["total_max"])
    note = _clamp(base - total_malus + bonus_pts)
    return {"note": round(note, 1), "base": round(base, 1), "malus": malus, "bonus": bonus,
            "detail": sorted(detail, key=lambda d: -d["poids"]), "minutes": minutes, "poste": poste}
