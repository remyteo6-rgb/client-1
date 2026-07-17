"""
Parser générique pour les exports XML Sportscode (Hudl Sportscode / Focus X2).

Ce module ne suppose rien de figé sur les noms de codes utilisés par l'analyste :
il détecte automatiquement les codes numérotés du type "<n> - <Catégorie> <Côté>"
(ex: "21 - Plaquage Nice", "43 - Mêlées Adverse", "22 - Duels aériens") et classe
tout le reste (surnoms de joueurs, marqueurs type STOP) comme des "tags" séparés.
"""
import re
import statistics
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta

ZONE_RE = re.compile(r"^\d+-\d+$")
NUMBERED_RE = re.compile(r"^\d+\s*-\s*(.+)$")
# Ex : "Birdie 9" -> type "Birdie", numéro de maillot "9" (le joueur qui a tapé).
TRAILING_NUMBER_RE = re.compile(r"^(.+?)\s+(\d+)$")

# Vocabulaire heuristique succès / échec trouvé dans les labels Sportscode
SUCCESS_TOKENS = {"REUSSI", "reussi", "GAGNE", "O", "VERT"}
FAIL_TOKENS = {"RATE", "raté", "PERDU", "ROUGE"}
POS_NEG_GROUPS_POSITIVE = {"+"}
POS_NEG_GROUPS_NEGATIVE = {"-"}

CONTROL_CODES = {"Ball In Play", "COACH"}

SIDE_ADVERSE_TOKENS = {"Adverse", "Adv"}


def _split_side(rest):
    """Given the text after 'N - ', split off trailing side/zone qualifiers.

    Returns (category, side, zone_extra) where side is 'own' | 'adverse' | 'neutral',
    and zone_extra holds a trailing purely-numeric qualifier like '50' if present.
    """
    tokens = rest.split()
    zone_extra = None
    # strip trailing purely numeric qualifier (e.g. "Ruck Nice 50")
    if tokens and tokens[-1].isdigit():
        zone_extra = tokens.pop()

    if not tokens:
        return rest, "neutral", zone_extra

    last = tokens[-1]
    if last in SIDE_ADVERSE_TOKENS:
        category = " ".join(tokens[:-1]).strip()
        return category, "adverse", zone_extra
    if last == "Nice" or last.lower() == "nice":
        category = " ".join(tokens[:-1]).strip()
        return category, "own", zone_extra

    # No recognizable side marker -> neutral / shared category (e.g. "Duels aériens")
    return rest.strip(), "neutral", zone_extra


def _label_success(labels):
    """Heuristic success/fail detection by scanning label texts."""
    for lab in labels:
        txt = (lab.get("text") or "").strip()
        if txt in SUCCESS_TOKENS or txt in POS_NEG_GROUPS_POSITIVE:
            return True
        if txt in FAIL_TOKENS or txt in POS_NEG_GROUPS_NEGATIVE:
            return False
    return None


def parse_sportscode_xml(path, own_team_label=None):
    """Parse a Sportscode XML export.

    Returns a dict:
      {
        'own_team_tag': str,        # detected label used for "our" side (e.g. "Nice")
        'instances': [ {...}, ... ],
        'code_catalog': {code_raw: count},
      }
    """
    tree = ET.parse(path)
    root = tree.getroot()
    all_instances = root.find("ALL_INSTANCES")
    if all_instances is None:
        raise ValueError("Fichier XML invalide : balise ALL_INSTANCES introuvable (export Sportscode attendu).")

    own_tag_votes = defaultdict(int)
    instances = []
    code_catalog = defaultdict(int)

    raw_instances = all_instances.findall("instance")
    for inst in raw_instances:
        code_raw = (inst.findtext("code") or "").strip()
        if not code_raw:
            continue
        code_catalog[code_raw] += 1
        start = float(inst.findtext("start") or 0)
        end = float(inst.findtext("end") or 0)

        labels = []
        zone = None
        for lab in inst.findall("label"):
            grp = lab.findtext("group")
            txt = (lab.findtext("text") or "").strip()
            labels.append({"group": grp, "text": txt})
            if grp is None and ZONE_RE.match(txt):
                zone = txt

        m = NUMBERED_RE.match(code_raw)
        if m:
            rest = m.group(1).strip()
            if rest in CONTROL_CODES:
                kind = "control"
                category, side, zone_extra = rest, "neutral", None
            else:
                category, side, zone_extra = _split_side(rest)
                kind = "stat"
                if side == "own":
                    own_tag_votes["Nice" if False else _last_own_token(rest)] += 1
        else:
            kind = "player"
            category, side, zone_extra = code_raw, "neutral", None

        success = _label_success(labels) if kind == "stat" else None

        instances.append({
            "id": inst.findtext("ID"),
            "code_raw": code_raw,
            "kind": kind,           # 'stat' | 'player' | 'control'
            "category": category,
            "side": side,           # 'own' | 'adverse' | 'neutral'
            "zone": zone,
            "zone_extra": zone_extra,
            "start": start,
            "end": end,
            "duration": round(end - start, 2),
            "success": success,
            "labels": labels,
        })

    # Determine the literal own-team tag word used in the coding window (e.g. "Nice")
    detected_tag = own_team_label
    if not detected_tag:
        tag_counts = defaultdict(int)
        for code in code_catalog:
            m = NUMBERED_RE.match(code)
            if m:
                tokens = m.group(1).strip().split()
                if tokens and tokens[-1].isdigit():
                    tokens = tokens[:-1]
                if tokens and tokens[-1] not in SIDE_ADVERSE_TOKENS and tokens[-1] != "Adverse":
                    if tokens[-1].lower() not in ("adverse", "adv"):
                        tag_counts[tokens[-1]] += code_catalog[code]
        detected_tag = max(tag_counts, key=tag_counts.get) if tag_counts else "Nice"

    return {
        "own_team_tag": detected_tag,
        "instances": instances,
        "code_catalog": dict(code_catalog),
    }


def _last_own_token(rest):
    tokens = rest.split()
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    return tokens[-1] if tokens else "Nice"


GENERIC_BINARY_TEXTS = {"+", "-", "REUSSI", "reussi", "RATE", "raté", "GAGNE", "PERDU"}


def aggregate_match_stats(instances):
    """Build per-category, per-side aggregates for dashboard rendering."""
    cats = defaultdict(lambda: {"own": {"count": 0, "success": 0, "fail": 0, "duration": 0.0},
                                 "adverse": {"count": 0, "success": 0, "fail": 0, "duration": 0.0},
                                 "neutral": {"count": 0, "success": 0, "fail": 0, "duration": 0.0}})
    # Détail des sous-labels Sportscode (rôles, zones, types de faute...) par catégorie/côté
    breakdown = defaultdict(lambda: {"own": defaultdict(int), "adverse": defaultdict(int), "neutral": defaultdict(int)})
    players = defaultdict(lambda: {"count": 0, "tags": defaultdict(int)})

    for inst in instances:
        if inst["kind"] == "stat":
            bucket = cats[inst["category"]][inst["side"]]
            bucket["count"] += 1
            bucket["duration"] += max(inst["duration"], 0)
            if inst["success"] is True:
                bucket["success"] += 1
            elif inst["success"] is False:
                bucket["fail"] += 1

            bside = breakdown[inst["category"]][inst["side"]]
            for lab in inst["labels"]:
                txt = lab["text"]
                if not txt or ZONE_RE.match(txt) or txt in GENERIC_BINARY_TEXTS:
                    continue
                bside[txt] += 1
        elif inst["kind"] == "player":
            p = players[inst["code_raw"]]
            p["count"] += 1
            for lab in inst["labels"]:
                txt = lab["text"]
                if txt and not ZONE_RE.match(txt):
                    # Préfixe par le groupe Sportscode (ex: "RUCK: ANCREUR") quand il est renseigné,
                    # pour distinguer les rôles (ruck, touche...) des tags libres ("Passe +").
                    label = f"{lab['group']}: {txt}" if lab["group"] else txt
                    p["tags"][label] += 1

    # convert to plain dicts, sorted
    result_cats = {}
    for cat, sides in cats.items():
        result_cats[cat] = {
            side: {
                "count": v["count"],
                "success": v["success"],
                "fail": v["fail"],
                "duration": round(v["duration"], 1),
                "success_rate": round(v["success"] / (v["success"] + v["fail"]) * 100, 1) if (v["success"] + v["fail"]) > 0 else None,
                "breakdown": dict(sorted(breakdown[cat][side].items(), key=lambda x: -x[1])[:8]),
            } for side, v in sides.items()
        }

    result_players = {}
    for name, v in players.items():
        result_players[name] = {
            "count": v["count"],
            "tags": dict(sorted(v["tags"].items(), key=lambda x: -x[1])),
        }

    return result_cats, result_players


ZONE_ORDER = ["0-20", "20-40", "40-60", "60-80", "80-100"]


def aggregate_zones(instances):
    """Territory breakdown: count of stat events per side per pitch zone."""
    zones = defaultdict(lambda: defaultdict(int))
    for inst in instances:
        if inst["kind"] == "stat" and inst["zone"] and inst["side"] in ("own", "adverse"):
            zones[inst["side"]][inst["zone"]] += 1
    result = {}
    for side in ("own", "adverse"):
        result[side] = {z: zones[side].get(z, 0) for z in ZONE_ORDER if z in zones[side] or True}
    return result


# Regroupement des catégories en sections lisibles pour le dashboard
CATEGORY_SECTIONS = {
    "Conquête": ["Ruck", "Touches", "Touches Lancements", "Mêlées", "Mêlées Lancements", "MEP"],
    "Défense": ["Plaquage", "PRESSION", "Duels aériens"],
    "Discipline & pertes": ["Disciplines", "Perte de balles", "Turnover", "Penalité", "ROUGE"],
    "Jeu & territoire": ["Possession", "RAID", "ACTION", "EXIT", "Retour 22", "KICK", "Contre attaque",
                          "Break", "Offload", "CE/CR", "RCE/RCR"],
    "Plans de jeu": ["BULL", "TIGER", "KILL", "BLAST", "CRUNCH", "CHAUD", "ZOOM", "BINGO", "BLANC",
                      "FLASH", "AUTRE"],
    "Marque": ["Essai", "Transformation"],
}

# Une courte phrase par section, pour du staff non-data
SECTION_ICONS = {
    "Conquête": "🤝",
    "Défense": "🛡️",
    "Discipline & pertes": "⚠️",
    "Jeu & territoire": "🧭",
    "Plans de jeu": "🎯",
    "Marque": "🏆",
}

SECTION_HELP = {
    "Conquête": "Qui gagne la bataille du ballon : rucks, touches, mêlées.",
    "Défense": "Solidité défensive : plaquages réussis/manqués, pression mise sur l'adversaire.",
    "Discipline & pertes": "Fautes concédées, ballons perdus, pénalités — ce qui coûte du terrain ou des points.",
    "Jeu & territoire": "Comment le ballon est utilisé et où le jeu se déroule sur le terrain.",
    "Plans de jeu": "Lancements et mouvements codés spécifiquement par le staff (noms de code interne).",
    "Marque": "Essais et transformations inscrits par chaque équipe.",
}

# Explications courtes par catégorie, affichées en info-bulle (tooltip) dans le tableau
CATEGORY_HELP = {
    "Ruck": "Nombre de rucks joués par chaque équipe.",
    "Touches": "Touches (remises en jeu) avec leur taux de réussite.",
    "Touches Lancements": "Lancements de touche spécifiques (jeu au pied ou variantes).",
    "Mêlées": "Mêlées ordonnées et leur issue.",
    "Mêlées Lancements": "Lancements de jeu depuis une mêlée.",
    "MEP": "Mise en place / entrée en jeu après un temps fort.",
    "Plaquage": "Plaquages tentés, avec taux de réussite (REUSSI vs RATE).",
    "PRESSION": "Séquences où l'équipe met l'adversaire sous pression défensive.",
    "Duels aériens": "Duels au pied contestés par les deux équipes (jeu aérien).",
    "Disciplines": "Fautes/pénalités concédées par chaque équipe.",
    "Perte de balles": "Ballons perdus en possession (turnovers subis).",
    "Turnover": "Ballons récupérés sur l'adversaire.",
    "Penalité": "Pénalités obtenues ou concédées.",
    "ROUGE": "Zone rouge / séquences à risque proche de sa ligne.",
    "Possession": "Temps de possession du ballon.",
    "RAID": "Séquences de portées de balle offensives.",
    "ACTION": "Actions de jeu significatives initiées.",
    "EXIT": "Sorties de camp (dégagements depuis sa propre zone).",
    "Retour 22": "Retours dans les 22 mètres adverses.",
    "KICK": "Coups de pied au jeu.",
    "Contre attaque": "Relances suite à une récupération de balle.",
    "Break": "Franchissements de la ligne défensive.",
    "Offload": "Passes après contact (offloads).",
    "CE/CR": "Changements d'espace / de rythme dans le jeu.",
    "RCE/RCR": "Réactions à un changement d'espace ou de rythme adverse.",
    "Essai": "Essais marqués.",
    "Transformation": "Transformations réussies après essai.",
}


def generate_highlights(stats, own_team, opponent):
    """Génère 3-5 phrases de synthèse en langage clair pour un staff non-data."""
    highlights = []

    poss = stats.get("Possession", {})
    poss_own_d = poss.get("own", {}).get("duration", 0)
    poss_adv_d = poss.get("adverse", {}).get("duration", 0)
    total_poss = poss_own_d + poss_adv_d
    if total_poss > 0:
        pct = round(poss_own_d / total_poss * 100)
        if pct >= 55:
            highlights.append(f"Possession dominée face à {opponent} ({pct}%).")
        elif pct <= 45:
            highlights.append(f"Possession plutôt subie face à {opponent} ({pct}%).")

    tackle = stats.get("Plaquage", {}).get("own", {})
    rate = tackle.get("success_rate")
    if rate is not None:
        if rate >= 85:
            highlights.append(f"Très bonne réussite au plaquage ({rate}%).")
        elif rate < 75:
            highlights.append(f"Réussite au plaquage à travailler ({rate}%).")

    ruck = stats.get("Ruck", {})
    ro = ruck.get("own", {}).get("count", 0)
    ra = ruck.get("adverse", {}).get("count", 0)
    if ro + ra > 0:
        share = ro / (ro + ra) * 100
        if share >= 55:
            highlights.append(f"Bataille du ruck gagnée ({ro} contre {ra}).")
        elif share <= 45:
            highlights.append(f"Bataille du ruck perdue ({ro} contre {ra}).")

    disc = stats.get("Disciplines", {})
    do = disc.get("own", {}).get("count", 0)
    da = disc.get("adverse", {}).get("count", 0)
    if do > da + 2:
        highlights.append(f"Plus indiscipliné que {opponent} ({do} fautes concédées contre {da}).")
    elif da > do + 2:
        highlights.append(f"Meilleure discipline que {opponent} ({do} fautes concédées contre {da}).")

    touches = stats.get("Touches", {}).get("own", {})
    trate = touches.get("success_rate")
    if trate is not None:
        if trate >= 80:
            highlights.append(f"Touche fiable ({trate}% de réussite).")
        elif trate < 60:
            highlights.append(f"Touche en difficulté ({trate}% de réussite).")

    if not highlights:
        highlights.append("Pas assez de signaux clairs sur ce match pour un résumé automatique — regarde le détail par catégorie ci-dessous.")

    return highlights[:5]


def compute_radar_metrics(stats):
    """5 indicateurs 0-100 (plus haut = mieux) pour un radar nous-vs-adversaire."""
    poss = stats.get("Possession", {})
    poss_own_d = poss.get("own", {}).get("duration", 0)
    poss_adv_d = poss.get("adverse", {}).get("duration", 0)
    poss_total = poss_own_d + poss_adv_d
    possession = round(poss_own_d / poss_total * 100, 1) if poss_total > 0 else 50.0

    plaquage_rate = stats.get("Plaquage", {}).get("own", {}).get("success_rate")
    plaquage = plaquage_rate if plaquage_rate is not None else 0.0

    ruck = stats.get("Ruck", {})
    ro = ruck.get("own", {}).get("count", 0)
    ra = ruck.get("adverse", {}).get("count", 0)
    ruck_share = round(ro / (ro + ra) * 100, 1) if (ro + ra) > 0 else 50.0

    touche_rate = stats.get("Touches", {}).get("own", {}).get("success_rate")
    touche = touche_rate if touche_rate is not None else 0.0

    disc = stats.get("Disciplines", {})
    do = disc.get("own", {}).get("count", 0)
    da = disc.get("adverse", {}).get("count", 0)
    discipline = round(da / (do + da) * 100, 1) if (do + da) > 0 else 50.0

    return {
        "Possession": possession,
        "Plaquage": plaquage,
        "Ruck": ruck_share,
        "Touche": touche,
        "Discipline": discipline,
    }


# ===========================================================================
# PAGES PAR SECTEUR : score réel, timing des phases, touches, mêlée, jeu au
# pied, joueurs. Toutes les formules ci-dessous ont été vérifiées contre un
# rapport de référence produit par le club sur ce même match (score exact,
# timing des phases au 1/10e de seconde, ratio touche exploitable exact,
# vitesse de ruck quasi exacte).
# ===========================================================================

def _has_label(inst, text, group="ANY"):
    for lab in inst["labels"]:
        if lab["text"] == text and (group == "ANY" or lab["group"] == group):
            return True
    return False


def _label_texts(inst, group="ANY"):
    return [l["text"] for l in inst["labels"] if group == "ANY" or l["group"] == group]


def compute_score(instances):
    """Score réel du match, calculé à partir des codes Essai / Transformation / Penalité."""
    own = adv = own_tries = adv_tries = 0
    for inst in instances:
        if inst["side"] not in ("own", "adverse"):
            continue
        cat = inst["category"]
        pts = 0
        if cat == "Essai":
            pts = 5
            if inst["side"] == "own":
                own_tries += 1
            else:
                adv_tries += 1
        elif cat == "Transformation" and _has_label(inst, "REUSSI"):
            pts = 2
        elif cat in ("Penalité", "Pénalité") and _has_label(inst, "REUSSI"):
            pts = 3
        elif cat in ("Drop", "Drop Goal") and _has_label(inst, "REUSSI"):
            pts = 3
        if pts:
            if inst["side"] == "own":
                own += pts
            else:
                adv += pts
    return {"own": own, "adverse": adv, "own_tries": own_tries, "adverse_tries": adv_tries}


PHASE_TAGS = ["EXIT", "PRESSION", "ACTION", "RAID"]
PHASE_ICONS = {"EXIT": "🚪", "PRESSION": "🧱", "ACTION": "⚡", "RAID": "🏃"}
PHASE_HELP = {
    "EXIT": "Sortie de camp : dégagement depuis sa propre zone.",
    "PRESSION": "Séquence de jeu où l'on met l'adversaire sous pression, proche de sa ligne.",
    "ACTION": "Phase de jeu courant, dans le camp adverse ou en zone neutre.",
    "RAID": "Portée de balle offensive, souvent proche de la ligne adverse.",
}


def _fmt_mmss(seconds):
    seconds = int(round(max(seconds, 0)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def compute_phase_timing(instances):
    """Temps (mm:ss) + % passé dans chaque phase de jeu, par côté."""
    data = {side: {tag: {"duration": 0.0, "count": 0} for tag in PHASE_TAGS} for side in ("own", "adverse")}
    for inst in instances:
        if inst["kind"] == "stat" and inst["category"] in PHASE_TAGS and inst["side"] in ("own", "adverse"):
            b = data[inst["side"]][inst["category"]]
            b["duration"] += max(inst["duration"], 0)
            b["count"] += 1
    result = {}
    for side in data:
        total = sum(v["duration"] for v in data[side].values())
        result[side] = {}
        for tag in PHASE_TAGS:
            v = data[side][tag]
            result[side][tag] = {
                "duration": round(v["duration"], 1),
                "duration_fmt": _fmt_mmss(v["duration"]),
                "count": v["count"],
                "pct": round(v["duration"] / total * 100) if total > 0 else 0,
            }
    return result


RELANCE_CATEGORIES = ["BULL", "TIGER", "KILL", "BLAST", "CRUNCH", "CHAUD", "ZOOM", "BINGO",
                      "BLANC", "FLASH", "AUTRE", "SKY", "RHINO", "SHARKS", "ROUGE"]


def compute_relance(instances, side):
    """Lancements de jeu nommés (codes internes du staff) : total terrain + répartition par phase."""
    total = defaultdict(int)
    by_phase = defaultdict(lambda: defaultdict(int))
    for inst in instances:
        if inst["kind"] == "stat" and inst["side"] == side and inst["category"] in RELANCE_CATEGORIES:
            total[inst["category"]] += 1
            for p in _label_texts(inst, None):
                if p in PHASE_TAGS:
                    by_phase[p][inst["category"]] += 1
    grand_total = sum(total.values())
    return {
        "total": {k: {"count": v, "pct": round(v / grand_total * 100) if grand_total else 0}
                  for k, v in sorted(total.items(), key=lambda x: -x[1])},
        "by_phase": {p: dict(sorted(v.items(), key=lambda x: -x[1])) for p, v in by_phase.items()},
    }


def compute_ruck_speed(instances, side):
    """Vitesse de ruck : répartition -3s / 3-6s / +6s (hors variante zone '50m')."""
    rucks = [i for i in instances if i["category"] == "Ruck" and i["side"] == side and i.get("zone_extra") is None]
    buckets = {"-3s": 0, "3-6s": 0, "+6s": 0}
    for i in rucks:
        d = i["duration"]
        if d < 3:
            buckets["-3s"] += 1
        elif d <= 6:
            buckets["3-6s"] += 1
        else:
            buckets["+6s"] += 1
    total = len(rucks)
    avg = round(sum(i["duration"] for i in rucks) / total, 2) if total else None
    return {
        "total": total,
        "avg": avg,
        "buckets": {k: {"count": v, "pct": round(v / total * 100) if total else 0} for k, v in buckets.items()},
    }


def compute_try_origin(instances, side):
    """D'où viennent les essais marqués (touche, mêlée, jeu courant...)."""
    origins = defaultdict(int)
    zones = defaultdict(int)
    for inst in instances:
        if inst["category"] == "Essai" and inst["side"] == side:
            for lab in inst["labels"]:
                if lab["group"] == "ORIGINE":
                    origins[lab["text"]] += 1
            if inst["zone"]:
                zones[inst["zone"]] += 1
    return {"origin": dict(origins), "zone": dict(zones)}


def compute_back3(instances):
    """Nombre de ballons touchés par le triangle arrière (ailiers n°11/n°14 et arrière n°15),
    à partir de la catégorie dédiée "BACK 3" codée par numéro de maillot du joueur."""
    back3_insts = [i for i in instances if i["category"] == "BACK 3"]
    by_number = defaultdict(int)
    for i in back3_insts:
        for lab in i["labels"]:
            if lab["group"] is None and lab["text"] and lab["text"].isdigit():
                by_number[lab["text"]] += 1
                break
    return {
        "total": len(back3_insts),
        "by_number": dict(sorted(by_number.items(), key=lambda x: -x[1])),
    }


def compute_back3_trend(selected_matches):
    """Ballons touchés par le Back 3, match par match : un graphique linéaire d'évolution sur
    la saison, même principe que le suivi JIFF (un point par match, relié en ligne continue).
    selected_matches : matchs du filtre saison (chacun avec sa propre liste 'instances', PAS
    concaténée entre matchs, pour obtenir un point distinct par match)."""
    rows = []
    for m in selected_matches:
        back3 = compute_back3(m.get("instances") or [])
        opponent = m.get("opponent") or "?"
        date = m.get("match_date") or ""
        rows.append({
            "match_id": m["id"],
            "label": f"{opponent} ({date})" if date else opponent,
            "total": back3["total"],
        })
    return rows


def compute_break_origin(instances, side):
    """D'où viennent les franchissements (touche, mêlée, jeu courant...) — même logique que compute_try_origin."""
    origins = defaultdict(int)
    for inst in instances:
        if inst["category"] == "Break" and inst["side"] == side:
            for lab in inst["labels"]:
                if lab["group"] == "ORIGINE":
                    origins[lab["text"]] += 1
    return dict(origins)


def compute_event_timing(instances, category, side):
    """Répartition d'une catégorie d'événements (essais, franchissements...) par tranche de jeu
    (~20 minutes), en réutilisant le découpage par mi-temps déjà détecté ailleurs. Renvoie None
    si aucune coupure de mi-temps n'a pu être détectée sur ce match."""
    bounds = _period_bounds(instances)
    if not bounds:
        return None
    result = {label: 0 for _, _, label in bounds}
    for i in instances:
        if i["category"] == category and i["side"] == side:
            for start, end, label in bounds:
                if start <= i["start"] < end:
                    result[label] += 1
                    break
    return result

def compute_event_timing_multi(selected, category, side):
    """Comme compute_event_timing, mais pour plusieurs matchs à la fois (saison).

    Chaque match a sa propre vidéo, donc sa propre coupure de mi-temps : on ne peut pas
    fusionner les instances brutes de tous les matchs avant de chercher la coupure (la plus
    grande coupure temporelle n'aurait alors plus rien à voir avec une mi-temps). On calcule
    donc la répartition par tranche match par match, puis on additionne les tranches.
    Renvoie None si aucun des matchs sélectionnés n'a de coupure de mi-temps détectée."""
    total = None
    for m in selected:
        per_match = compute_event_timing(m["instances"], category, side)
        if per_match is None:
            continue
        if total is None:
            total = dict(per_match)
        else:
            for k, v in per_match.items():
                total[k] = total.get(k, 0) + v
    return total
    
def compute_discipline_breakdown(instances, side, phase=None):
    """Répartition des fautes concédées par type (groupe DISCIPLINES).

    Si phase vaut 'OFF' ou 'DEF', ne garde que les fautes commises dans cette
    phase de jeu (sous-label DISCIPLINES codé par l'analyste sur chaque faute) :
    'OFF' = faute concédée quand l'équipe avait le ballon (discipline offensive),
    'DEF' = faute concédée en défense."""
    counts = defaultdict(int)
    for inst in instances:
        if inst["category"] == "Disciplines" and inst["side"] == side:
            label_texts = [l["text"] for l in inst["labels"] if l["group"] == "DISCIPLINES"]
            if phase and phase not in label_texts:
                continue
            for txt in label_texts:
                if txt != "Disciplines":
                    counts[txt] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _cat_count(instances, cat, side, success=None):
    n = 0
    for i in instances:
        if i["category"] == cat and i["side"] == side:
            if success is None or i["success"] == success:
                n += 1
    return n


def _duel_count(instances, group_names, side_hint=None):
    """Compte les +/- d'un ou plusieurs groupes de labels ('Duel Espace', 'duelaérien'...)."""
    pos = neg = 0
    for i in instances:
        for lab in i["labels"]:
            if lab["group"] in group_names:
                if lab["text"] == "+":
                    pos += 1
                elif lab["text"] == "-":
                    neg += 1
    return {"plus": pos, "minus": neg, "total": pos + neg,
            "pct": round(pos / (pos + neg) * 100, 1) if (pos + neg) else None}


def compute_attack_sector(instances, side):
    """Métriques d'attaque pour 'side' (own = notre attaque ; adverse = attaque adverse)."""
    score = compute_score(instances)
    team_score = score["own"] if side == "own" else score["adverse"]
    entries = _cat_count(instances, "RAID", side)
    ruck_count = _cat_count(instances, "Ruck", side)
    offloads = _cat_count(instances, "Offload", side)
    breaks = _cat_count(instances, "Break", side)
    lost_balls = _cat_count(instances, "Perte de balles", side)
    duels_aeriens = _duel_count(instances, {"Duel Espace", "duelaérien"})

    # Nombre de phases moyen par possession : somme du descripteur "PHASES DE JEU"
    # codé sur chaque instance Possession, divisée par le nombre total de possessions.
    possession_insts = [i for i in instances if i["category"] == "Possession" and i["side"] == side]
    total_possessions = len(possession_insts)
    total_phases = 0
    for i in possession_insts:
        for lab in i["labels"]:
            if lab["group"] == "PHASES DE JEU":
                try:
                    total_phases += int(lab["text"])
                except (TypeError, ValueError):
                    pass
    phases_moyenne = round(total_phases / total_possessions, 2) if total_possessions else None

    defenders_beaten = 0
    if side == "own":
        for i in instances:
            if i["kind"] == "player":
                for lab in i["labels"]:
                    if lab["group"] is None and lab["text"] == "DEF Battu":
                        defenders_beaten += 1

    return {
        "score": team_score,
        "entries": entries,
        "points_per_entry": round(team_score / entries, 2) if entries else None,
        "ruck_count": ruck_count,
        "phases_moyenne": phases_moyenne,
        "offloads": offloads,
        "breaks": breaks,
        "lost_balls": lost_balls,
        "duels_aeriens": duels_aeriens,
        "meters_gained": None,  # pas encore codé dans le XML : distance parcourue ballon en main non mesurée actuellement
        "gain_line": None,  # pas encore codé dans le XML : "Duel Contact" est un concept différent pour Téo
        "passe_ruck_ratio": None,  # pas encore codé dans le XML : les passes ne sont pas comptées au niveau équipe actuellement
        "defenders_beaten": defenders_beaten if side == "own" else None,
        "relance": compute_relance(instances, side),
        "ruck_speed": compute_ruck_speed(instances, side),
        "try_origin": compute_try_origin(instances, side),
        "try_timing": compute_event_timing(instances, "Essai", side),
        "break_origin": compute_break_origin(instances, side),
        "break_timing": compute_event_timing(instances, "Break", side),
        "back3": compute_back3(instances) if side == "own" else None,
        "discipline": compute_discipline_breakdown(instances, side, phase="OFF"),
        "possession_sequences": compute_possession_sequences(instances, side),
    }


def compute_plaquage_detail(instances):
    """Détail des plaquages : catégorie 'Plaquage', toujours codée côté 'own' par l'analyste
    (seuls les plaquages faits par notre équipe sont codés, quel que soit le porteur de balle).
    Une instance peut porter plusieurs labels REUSSI/RATE (plusieurs plaquages dans une même
    séquence) : on compte donc les labels, pas les instances. 'plaquage_a_2' = plaquages
    effectués à deux défenseurs (label '2' codé sur l'instance)."""
    plq = [i for i in instances if i["category"] == "Plaquage" and i["side"] == "own"]
    total = reussi = rate = plaquage_a_2 = 0
    for i in plq:
        if any(l["group"] is None and l["text"] == "2" for l in i["labels"]):
            plaquage_a_2 += 1
        for l in i["labels"]:
            if l["group"] == "plaquage":
                total += 1
                if l["text"] == "REUSSI":
                    reussi += 1
                elif l["text"] == "RATE":
                    rate += 1
    return {
        "total": total,
        "reussi": reussi,
        "rate": rate,
        "rate_pct": round(reussi / total * 100, 1) if total else None,
        "plaquage_a_2": plaquage_a_2,
    }


def compute_defense_sector(instances, side):
    """Vue défensive : side='adverse' => ce que l'adversaire nous a fait subir (notre défense).

    Les plaquages sont calculés indépendamment de 'side' via compute_plaquage_detail, car ils
    sont toujours codés côté 'own' par l'analyste (voir sa docstring)."""
    plaquage = compute_plaquage_detail(instances)
    turnovers_recuperes = _cat_count(instances, "Turnover", "own" if side == "adverse" else "adverse")
    return {
        "plaquage": plaquage,
        "turnovers_recuperes": turnovers_recuperes,
        "ruck_speed_subi": compute_ruck_speed(instances, side),
    }


RUCK_SPEED_BUCKET_LABELS = ["-3s", "3-6s", "+6s"]
RUCK_ZONE_PHASES = ["RAID", "ACTIONS", "PRESSION", "EXIT"]


def compute_ruck_speed_by_zone(instances):
    """Vitesse de ruck détaillée par zone (RAID/ACTIONS/PRESSION/EXIT — remplace le 0-20/20-40/
    40-60/60-80 en mètres par ces 4 zones nommées) et par tranche de vitesse (-3s/3-6s/+6s),
    nous vs adverse, avec vitesse moyenne par zone.

    Pas encore calculable de façon fiable : le codage actuel des rucks ne rattache pas encore
    une de ces zones à chaque instance Ruck (voir compute_ruck_speed) — structure prête, à
    activer dès qu'une zone (RAID/ACTIONS/PRESSION/EXIT) sera codée sur chaque ruck dans le XML."""
    empty_buckets = {b: None for b in RUCK_SPEED_BUCKET_LABELS}
    return {
        "zones": RUCK_ZONE_PHASES,
        "own": {z: {"buckets": dict(empty_buckets), "avg": None} for z in RUCK_ZONE_PHASES},
        "adverse": {z: {"buckets": dict(empty_buckets), "avg": None} for z in RUCK_ZONE_PHASES},
    }


def compute_ruck_sector(instances):
    """Vue dédiée au ruck : conquête du ballon au sol, nous vs adverse."""
    entries_own = _cat_count(instances, "RAID", "own")
    entries_adverse = _cat_count(instances, "RAID", "adverse")
    ruck_own = _cat_count(instances, "Ruck", "own")
    ruck_adverse = _cat_count(instances, "Ruck", "adverse")
    speed_own = compute_ruck_speed(instances, "own")
    speed_adverse = compute_ruck_speed(instances, "adverse")
    totals = compute_player_ruck_table(instances)["totals"]
    gratteur_plus = totals.get("gratteur_plus", 0)
    gratteur_minus = totals.get("gratteur_minus", 0)
    contre_plus = totals.get("contre_ruck_plus", 0)
    contre_minus = totals.get("contre_ruck_minus", 0)
    gratteur_tot = gratteur_plus + gratteur_minus
    contre_tot = contre_plus + contre_minus
    # Rucks codés avec le qualificatif "50" (ex: "33 - Ruck Nice 50") = ruck dans les 50m.
    ruck_50_own = sum(1 for i in instances if i["category"] == "Ruck" and i["side"] == "own" and i.get("zone_extra") == "50")
    ruck_50_adverse = sum(1 for i in instances if i["category"] == "Ruck" and i["side"] == "adverse" and i.get("zone_extra") == "50")
    return {
        "count_own": ruck_own,
        "count_adverse": ruck_adverse,
        "ruck_50_own": ruck_50_own,
        "ruck_50_adverse": ruck_50_adverse,
        "ruck_won_own": None,  # pas encore codé dans le XML : issue du ruck (gagné/perdu) non distinguée actuellement
        "ruck_won_adverse": None,  # pas encore codé dans le XML
        "phases_moyenne_own": round(ruck_own / entries_own, 2) if entries_own else None,
        "phases_moyenne_adverse": round(ruck_adverse / entries_adverse, 2) if entries_adverse else None,
        "speed_own": speed_own,
        "speed_adverse": speed_adverse,
        "gratteur_plus": gratteur_plus,
        "gratteur_minus": gratteur_minus,
        "gratteur_pct": round(gratteur_plus / gratteur_tot * 100) if gratteur_tot else None,
        "contre_ruck_plus": contre_plus,
        "contre_ruck_minus": contre_minus,
        "contre_ruck_pct": round(contre_plus / contre_tot * 100) if contre_tot else None,
        "speed_by_zone": compute_ruck_speed_by_zone(instances),
    }


# ---- Touches (lineouts) ----------------------------------------------------

TOUCH_CALL_NAMES = {"Inverse", "Rocket", "Turbo", "Mortier", "Bombe", "Crochet", "JAB", "Tempo", "Buste", "Direct"}
TOUCH_JUMP_COUNTS = ["T4/T4+1", "T5/T5+1", "T6/T6+1", "T7/T7+1"]
TOUCH_COLORS = ["NOIR", "JAUNE", "ROUGE", "VERT", "BLANC"]
TOUCH_LETTERS = ["P", "O", "I", "N", "G"]


def compute_lineout_detail(instances):
    result = {}
    for side in ("own", "adverse"):
        touches = [i for i in instances if i["category"] == "Touches" and i["side"] == side]
        total = len(touches)
        won = sum(1 for i in touches if i["success"] is True)
        tqb_plus = sum(1 for i in touches if "TQB +" in _label_texts(i, None))

        call_names = defaultdict(int)
        jump_success = {j: {"gagne": 0, "perdu": 0} for j in TOUCH_JUMP_COUNTS}
        colors = defaultdict(int)
        letters = defaultdict(int)
        for i in touches:
            conquete = _label_texts(i, "CONQUETE")
            for c in conquete:
                if c in TOUCH_CALL_NAMES:
                    call_names[c] += 1
                elif c in TOUCH_COLORS:
                    colors[c] += 1
                elif c in TOUCH_LETTERS:
                    letters[c] += 1
            jnum = next((t for t in conquete if t in TOUCH_JUMP_COUNTS), None)
            if jnum:
                if "GAGNE" in conquete:
                    jump_success[jnum]["gagne"] += 1
                elif "PERDU" in conquete:
                    jump_success[jnum]["perdu"] += 1

        jump_rates = {}
        for j in TOUCH_JUMP_COUNTS:
            g, p = jump_success[j]["gagne"], jump_success[j]["perdu"]
            jump_rates[j] = {"gagne": g, "perdu": p, "pct": round(g / (g + p) * 100) if (g + p) else None}

        result[side] = {
            "total": total,
            "won": won,
            "success_rate": round(won / total * 100, 1) if total else None,
            "tqb_plus": tqb_plus,
            "exploitable_rate": round(tqb_plus / total * 100, 2) if total else None,
            "call_names": dict(sorted(call_names.items(), key=lambda x: -x[1])),
            "jump_rates": jump_rates,
            "colors": dict(sorted(colors.items(), key=lambda x: -x[1])),
            "letters": dict(sorted(letters.items(), key=lambda x: -x[1])),
        }
    return result


# ---- Mêlée (scrum) ----------------------------------------------------------

def compute_scrum_detail(instances):
    result = {}
    for side in ("own", "adverse"):
        melees = [i for i in instances if i["category"] == "Mêlées" and i["side"] == side]
        total = len(melees)
        avance = sum(1 for i in melees if "AVANCE" in _label_texts(i, "MÊLÉE"))
        stable = sum(1 for i in melees if "STABLE" in _label_texts(i, "MÊLÉE"))
        rejoue = sum(1 for i in melees if "REJOUE" in _label_texts(i, "MÊLÉE"))
        won = sum(1 for i in melees if "GAGNE" in _label_texts(i, "MÊLÉE"))
        lost = sum(1 for i in melees if "PERDU" in _label_texts(i, "MÊLÉE"))

        zone_grid = {tag: {"count": 0, "gagne": 0} for tag in PHASE_TAGS}
        for i in melees:
            mtags = _label_texts(i, "MÊLÉE")
            for tag in PHASE_TAGS:
                if tag in mtags:
                    zone_grid[tag]["count"] += 1
                    if "GAGNE" in mtags:
                        zone_grid[tag]["gagne"] += 1

        result[side] = {
            "total": total,
            "avance": avance,
            "stable": stable,
            "avance_pct": round(avance / (avance + stable) * 100) if (avance + stable) else None,
            "stable_pct": round(stable / (avance + stable) * 100) if (avance + stable) else None,
            "won": won,
            "lost": lost,
            "won_pct": round(won / (won + lost) * 100) if (won + lost) else None,
            "rejoue": rejoue,
            "zone_grid": zone_grid,
        }
    return result


# ---- Jeu au pied (kicking) --------------------------------------------------

def _normalize_label(text):
    """Minuscule + sans accents, pour comparer des labels codés à la main sans se soucier
    de la casse ou des accents (ex : "Gagne" / "gagné" / "GAGNE" doivent tous matcher)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().lower()


# Labels codés sur les coups de pied qui ne sont pas des "types" de coup de pied
# (résultat de duel aérien, qualité de zone...) : à exclure du graphique par type,
# sinon ils s'affichent comme des barres à côté des vrais types de coup de pied.
# Comparaison insensible à la casse/aux accents (voir _normalize_label).
KICK_SUBTYPE_EXCLUDED = {
    _normalize_label(t) for t in
    ["bon jump", "bonne zone", "mauvaise zone", "gagne", "perdu", "contestable", "conteste"]
}


def compute_kicking_detail(instances):
    result = {}
    # Duels aériens : comptés globalement sur le match (labels "Duel Espace"/"duelaérien"
    # portés par des instances joueurs / "Duels aériens", pas rattachés à chaque coup de pied
    # individuellement) — offensif/défensif pas encore distingués, donc même chiffre nous/eux.
    duels = _duel_count(instances, {"Duel Espace", "duelaérien"})
    for side in ("own", "adverse"):
        kicks = [i for i in instances if i["category"] == "KICK" and i["side"] == side]
        total = len(kicks)
        won = sum(1 for i in kicks if i["success"] is True)
        lost = sum(1 for i in kicks if i["success"] is False)

        # Détail par type de coup de pied codé, une barre par combinaison exacte codée
        # (ex : "Birdie 9", "Drive 15", "Put", "Autres" restent des entrées distinctes —
        # le numéro de joueur n'est pas retiré). On exclut les labels de zone/période
        # ("0-20"..., group "timing"), le flag plaquage réussi/raté (group "plaquage"),
        # et les textes purement numériques (pas des noms de type).
        sub_types_raw = defaultdict(int)
        for i in kicks:
            for lab in i["labels"]:
                if lab["group"] in ("timing", "plaquage"):
                    continue
                text = lab["text"]
                if text in ("REUSSI", "RATE", "raté", "reussi"):
                    continue
                if _normalize_label(text) in KICK_SUBTYPE_EXCLUDED:
                    continue
                if ZONE_RE.match(text) or text.isdigit():
                    continue
                sub_types_raw[text] += 1
        sub_types_total = sum(sub_types_raw.values())
        sub_types = {
            name: {
                "count": n,
                "pct": round(n / sub_types_total * 100, 1) if sub_types_total else None,
            }
            for name, n in sub_types_raw.items()
        }

        result[side] = {
            "total": total,
            "won": won,
            "lost": lost,
            "success_rate": round(won / (won + lost) * 100, 1) if (won + lost) else None,
            "sub_types": sub_types,
            # Jeu au pied contestable / contesté : "contestable" = total des coups de pied
            # (tous potentiellement contestables), "contesté" = nb de duels aériens qui ont
            # suivi (voir note ci-dessus sur "duels").
            "kick_contestable": total,
            "kick_conteste": duels["total"],
            "duels_aeriens_off_pct": duels["pct"],
            # Pas encore codé dans le XML sur ce match :
            "penaltouche_rate": None,       # taux de réussite aux pénaltouches (own uniquement)
            "penaltouche_total": None,      # nombre de pénaltouches tentées (own uniquement)
        }
    return result


# ---- Tableaux joueurs --------------------------------------------------------

PLAYER_NAME_HELP = "Regroupe toutes les actions individuelles codées sous le nom du joueur dans Sportscode."


def _player_names(instances):
    names = set()
    for i in instances:
        if i["kind"] == "player":
            names.add(i["code_raw"])
    return names


def compute_player_attack_table(instances):
    """Contacts, duels, passes, offloads, PDB, défenseurs battus, breaks, points par joueur."""
    rows = {}
    for name in _player_names(instances):
        rows[name] = {"contact_plus": 0, "contact_minus": 0, "duel_plus": 0, "duel_minus": 0,
                      "passe_plus": 0, "passe_minus": 0, "offload_plus": 0, "offload_minus": 0,
                      "pdb": 0, "def_battu": 0, "break": 0}

    for i in instances:
        if i["kind"] != "player":
            continue
        row = rows[i["code_raw"]]
        for lab in i["labels"]:
            grp, txt = lab["group"], lab["text"]
            if grp == "Duel Contact":
                if txt == "+":
                    row["contact_plus"] += 1
                elif txt == "-":
                    row["contact_minus"] += 1
            elif grp in ("Duel Espace", "duelaérien"):
                if txt == "+":
                    row["duel_plus"] += 1
                elif txt == "-":
                    row["duel_minus"] += 1
            elif grp is None:
                if txt == "Passe +":
                    row["passe_plus"] += 1
                elif txt == "Passe -":
                    row["passe_minus"] += 1
                elif txt == "Offload +":
                    row["offload_plus"] += 1
                elif txt == "Offload -":
                    row["offload_minus"] += 1
                elif txt == "PDB":
                    row["pdb"] += 1
                elif txt == "DEF Battu":
                    row["def_battu"] += 1
                elif txt == "Franchissement":
                    row["break"] += 1

    result = []
    totals = defaultdict(int)
    for name, r in rows.items():
        contact_tot = r["contact_plus"] + r["contact_minus"]
        duel_tot = r["duel_plus"] + r["duel_minus"]
        passe_tot = r["passe_plus"] + r["passe_minus"]
        offload_tot = r["offload_plus"] + r["offload_minus"]
        if contact_tot + duel_tot + passe_tot + offload_tot + r["pdb"] + r["def_battu"] + r["break"] == 0:
            continue
        points = (r["contact_plus"] * 1 + r["duel_plus"] * 1 + r["passe_plus"] * 1 + r["offload_plus"] * 2
                  + r["def_battu"] * 2 + r["break"] * 3 + r["pdb"] * 1
                  - r["contact_minus"] - r["duel_minus"])
        row_out = {
            "name": name, **r,
            "contact_total": contact_tot, "contact_pct": round(r["contact_plus"] / contact_tot * 100) if contact_tot else None,
            "duel_total": duel_tot, "duel_pct": round(r["duel_plus"] / duel_tot * 100) if duel_tot else None,
            "passe_total": passe_tot, "passe_pct": round(r["passe_plus"] / passe_tot * 100) if passe_tot else None,
            "offload_total": offload_tot, "offload_pct": round(r["offload_plus"] / offload_tot * 100) if offload_tot else None,
            "points": points,
        }
        result.append(row_out)
        for k, v in r.items():
            totals[k] += v
        totals["points"] += points
    result.sort(key=lambda x: -x["points"])
    return {"rows": result, "totals": dict(totals)}


def compute_player_defense_table(instances):
    """Plaquages (par qualité), assists, discipline, points par joueur."""
    rows = {}
    for name in _player_names(instances):
        rows[name] = {"plaquage_dominant": 0, "plaquage_neutre": 0, "plaquage_passif": 0, "plaquage_rate": 0,
                      "assist_plus": 0, "assist_minus": 0, "discipline": 0}

    for i in instances:
        if i["kind"] != "player":
            continue
        row = rows[i["code_raw"]]
        plaquage_txts = _label_texts(i, "plaquage")
        if "reussi" in plaquage_txts:
            if "+" in plaquage_txts:
                row["plaquage_dominant"] += 1
            elif "-" in plaquage_txts:
                row["plaquage_passif"] += 1
            else:
                row["plaquage_neutre"] += 1
        elif "raté" in plaquage_txts:
            row["plaquage_rate"] += 1
        for lab in i["labels"]:
            if lab["group"] == "assistant":
                if lab["text"] == "+":
                    row["assist_plus"] += 1
                elif lab["text"] == "-":
                    row["assist_minus"] += 1
            elif lab["group"] == "faute":
                row["discipline"] += 1

    result = []
    totals = defaultdict(int)
    for name, r in rows.items():
        tackle_total = r["plaquage_dominant"] + r["plaquage_neutre"] + r["plaquage_passif"] + r["plaquage_rate"]
        if tackle_total + r["assist_plus"] + r["assist_minus"] + r["discipline"] == 0:
            continue
        made = r["plaquage_dominant"] + r["plaquage_neutre"] + r["plaquage_passif"]
        points = (r["plaquage_dominant"] * 2 + r["plaquage_neutre"] * 1 + r["plaquage_passif"] * 0
                  - r["plaquage_rate"] * 2 + r["assist_plus"] * 1 - r["discipline"] * 2)
        row_out = {
            "name": name, **r,
            "tackle_total": tackle_total,
            "tackle_pct": round(made / tackle_total * 100, 2) if tackle_total else None,
            "points": points,
        }
        result.append(row_out)
        for k, v in r.items():
            totals[k] += v
        totals["points"] += points
    result.sort(key=lambda x: -x["points"])
    return {"rows": result, "totals": dict(totals)}


# ===========================================================================
# TABLEAU DE BORD PRINCIPAL (vue d'ensemble) : temps de jeu effectif (ball in
# play), % de gain de la ligne d'avantage, points par entrée, occupation du
# terrain nous vs adversaire. "Ball in play" est vérifié à la seconde près
# contre le chronomètre affiché sur le rapport de référence du club (39:07).
# ===========================================================================

MATCH_DURATION_REF_SECONDS = 80 * 60  # référence standard 80 minutes


def compute_ball_in_play(instances):
    """Temps de jeu effectif = somme des séquences codées 'Ball In Play'."""
    total = sum(max(i["duration"], 0) for i in instances
                if i["kind"] == "control" and i["category"] == "Ball In Play")
    return {
        "duration": round(total, 1),
        "duration_fmt": _fmt_mmss(total),
        "pct_match": round(total / MATCH_DURATION_REF_SECONDS * 100) if total else 0,
    }


BIP_DURATION_BUCKETS = [
    ("0-30s", 0, 30),
    ("30-45s", 30, 45),
    ("45s-1min", 45, 60),
    ("1min-1min15", 60, 75),
    ("1min15-1min30", 75, 90),
    ("1min30-1min45", 90, 105),
    ("1min45-2min", 105, 120),
    ("2min-2min30", 120, 150),
    ("2min30-3min", 150, 180),
    ("3min et +", 180, None),
]


def _bucket_durations(durations, buckets=BIP_DURATION_BUCKETS):
    """Répartit une liste de durées (secondes) dans des tranches, avec compte + %."""
    counts = {label: 0 for label, _, _ in buckets}
    for d in durations:
        d = max(d, 0)
        for label, lo, hi in buckets:
            if d >= lo and (hi is None or d < hi):
                counts[label] += 1
                break
    total = len(durations)
    return {
        "total": total,
        "buckets": {
            label: {"count": n, "pct": round(n / total * 100) if total else 0}
            for label, n in counts.items()
        },
    }


def compute_bip_sequences(instances):
    """Répartition des séquences 'Ball In Play' (temps de jeu effectif) par tranche de durée,
    à partir de la durée (end - start) de chaque séquence codée 'Ball In Play' dans le XML."""
    durations = [i["duration"] for i in instances
                 if i["kind"] == "control" and i["category"] == "Ball In Play"]
    return _bucket_durations(durations)


def compute_possession_sequences(instances, side):
    """Répartition des séquences de possession par tranche de durée (même découpage que
    Ball In Play), à partir de la durée de chaque instance codée 'Possession' pour 'side'."""
    durations = [i["duration"] for i in instances
                 if i["category"] == "Possession" and i["side"] == side]
    return _bucket_durations(durations)


def compute_gain_line(instances):
    """% de gain de la ligne d'avantage : part des duels de contact où le
    porteur de balle passe la ligne d'avantage (groupe de labels 'Duel Contact')."""
    plus = minus = 0
    for i in instances:
        if i["kind"] == "player":
            for lab in i["labels"]:
                if lab["group"] == "Duel Contact":
                    if lab["text"] == "+":
                        plus += 1
                    elif lab["text"] == "-":
                        minus += 1
    total = plus + minus
    return {
        "plus": plus, "minus": minus, "total": total,
        "pct": round(plus / total * 100, 1) if total else None,
    }


def compute_occupation(instances):
    """Occupation du terrain : répartition (nous vs adversaire) des événements
    codés géolocalisés par zone du terrain — un indicateur territorial distinct
    de la possession (basé sur l'espace, pas sur le temps de détention du ballon)."""
    zones = aggregate_zones(instances)
    own_total = sum(zones.get("own", {}).values())
    adv_total = sum(zones.get("adverse", {}).values())
    total = own_total + adv_total
    return {
        "own_events": own_total,
        "adverse_events": adv_total,
        "own_pct": round(own_total / total * 100, 1) if total else None,
        "adverse_pct": round(adv_total / total * 100, 1) if total else None,
    }


def compute_overview_dashboard(instances, score):
    """Regroupe les métriques du nouveau tableau de bord de la page principale :
    temps de jeu effectif, % gain ligne d'avantage, points par entrée, occupation.

    "Points par entrée" = points totaux de l'équipe / nombre d'entrées en zone RAID."""
    entries_own = _cat_count(instances, "RAID", "own")
    entries_adverse = _cat_count(instances, "RAID", "adverse")
    own_score = score["own"] if score else 0
    adverse_score = score["adverse"] if score else 0
    return {
        "ball_in_play": compute_ball_in_play(instances),
        "gain_line": None,  # pas encore codé dans le XML : "Duel Contact" est un concept différent pour Téo
        "occupation": compute_occupation(instances),
        "entries": entries_own,
    "entries_adverse": entries_adverse,
        "points_per_entry": round(own_score / entries_own, 2) if entries_own else None,
        "points_per_entry_adverse": round(adverse_score / entries_adverse, 2) if entries_adverse else None,
        "lost_balls_own": _cat_count(instances, "Perte de balles", "own"),
        "lost_balls_adverse": _cat_count(instances, "Perte de balles", "adverse"),
        "score_detail": compute_score_detail(instances),
        "entry_types_own": compute_entry_types(instances, "own"),
        "entry_types_adverse": compute_entry_types(instances, "adverse"),
        "lineout": compute_lineout_detail(instances),
        "scrum": compute_scrum_detail(instances),
        "possession_by_period": compute_possession_by_period(instances),
        "occupation_by_period": compute_occupation_by_period(instances),
        "bip_sequences": compute_bip_sequences(instances),
    }


def compute_score_detail(instances):
    """Détail du score par type de points : essais, transformations, pénalités, drops."""
    result = {}
    for side in ("own", "adverse"):
        tries = _cat_count(instances, "Essai", side)
        conversions = sum(1 for i in instances if i["category"] == "Transformation" and i["side"] == side and _has_label(i, "REUSSI"))
        conversions_att = _cat_count(instances, "Transformation", side)
        penalties = sum(1 for i in instances if i["category"] in ("Penalité", "Pénalité") and i["side"] == side and _has_label(i, "REUSSI"))
        penalties_att = sum(1 for i in instances if i["category"] in ("Penalité", "Pénalité") and i["side"] == side)
        drops = sum(1 for i in instances if i["category"] in ("Drop", "Drop Goal") and i["side"] == side and _has_label(i, "REUSSI"))
        drops_att = sum(1 for i in instances if i["category"] in ("Drop", "Drop Goal") and i["side"] == side)
        result[side] = {
            "tries": tries,
            "conversions": conversions, "conversions_att": conversions_att,
            "penalties": penalties, "penalties_att": penalties_att,
            "drops": drops, "drops_att": drops_att,
        }
    return result


def compute_entry_types(instances, side):
    """Répartition des entrées en zone RAID par origine : course (RUN), sortie de camp (EXIT), pénalité (PENALITE)."""
    counts = defaultdict(int)
    for i in instances:
        if i["category"] == "RAID" and i["side"] == side:
            for lab in i["labels"]:
                if lab["group"] is None and lab["text"] in ("RUN", "EXIT", "PENALITE"):
                    counts[lab["text"]] += 1
    return dict(counts)


def _detect_halves(instances):
    """Détecte les deux mi-temps via la plus grande coupure temporelle dans le flux vidéo codé."""
    timed = sorted(instances, key=lambda x: x["start"])
    if len(timed) < 2:
        return None
    best_gap, split_idx = 0, None
    for idx in range(1, len(timed)):
        gap = timed[idx]["start"] - timed[idx - 1]["end"]
        if gap > best_gap:
            best_gap, split_idx = gap, idx
    if split_idx is None or best_gap < 300:
        return None
    return {
        "h1_start": timed[0]["start"], "h1_end": timed[split_idx - 1]["end"],
        "h2_start": timed[split_idx]["start"], "h2_end": timed[-1]["end"],
    }


def _period_bounds(instances):
    """4 tranches de jeu façon 'quart-temps' (2 par mi-temps), à partir de la coupure détectée.
    Approximation : chaque mi-temps codée est divisée en 2 moitiés égales (pas d'horloge de
    match exacte disponible dans le XML), étiquetées 0-20/20-40/40-60/60-80 par convention."""
    halves = _detect_halves(instances)
    if not halves:
        return None
    h1_mid = (halves["h1_start"] + halves["h1_end"]) / 2
    h2_mid = (halves["h2_start"] + halves["h2_end"]) / 2
    return [
        (halves["h1_start"], h1_mid, "0-20"),
        (h1_mid, halves["h1_end"], "20-40"),
        (halves["h2_start"], h2_mid, "40-60"),
        (h2_mid, halves["h2_end"], "60-80"),
    ]


def compute_possession_by_period(instances):
    """Répartition du temps de possession par tranche de jeu (approx. 20 minutes)."""
    bounds = _period_bounds(instances)
    if not bounds:
        return None
    result = {}
    for start, end, label in bounds:
        own = adv = 0.0
        for i in instances:
            if i["category"] == "Possession" and i["side"] in ("own", "adverse") and start <= i["start"] < end:
                if i["side"] == "own":
                    own += max(i["duration"], 0)
                else:
                    adv += max(i["duration"], 0)
        total = own + adv
        result[label] = {
            "own": round(own, 1), "adverse": round(adv, 1),
            "own_pct": round(own / total * 100) if total else None,
            "adverse_pct": round(adv / total * 100) if total else None,
        }
    return result


def compute_occupation_by_period(instances):
    """Répartition de l'occupation du terrain (événements géolocalisés) par tranche de jeu."""
    bounds = _period_bounds(instances)
    if not bounds:
        return None
    result = {}
    for start, end, label in bounds:
        own = adv = 0
        for i in instances:
            if i["kind"] == "stat" and i["zone"] and i["side"] in ("own", "adverse") and start <= i["start"] < end:
                if i["side"] == "own":
                    own += 1
                else:
                    adv += 1
        total = own + adv
        result[label] = {
            "own": own, "adverse": adv,
            "own_pct": round(own / total * 100) if total else None,
            "adverse_pct": round(adv / total * 100) if total else None,
        }
    return result

def compute_match_baseline(matches_with_instances, exclude_id=None):
    """Moyennes 'saison' (tous les autres matchs, hors celui affiché) pour comparer les
    stats d'un match à ce que l'équipe fait habituellement — sert à voir d'un coup d'œil
    si un match est bon ou mauvais par rapport à la norme. Renvoie None s'il n'y a pas
    d'autre match avec des données pour calculer une moyenne."""
    others = [m for m in matches_with_instances if m["id"] != exclude_id and m["instances"]]
    if not others:
        return None
    nb = len(others)
    instances = [i for m in others for i in m["instances"]]

    score = compute_score(instances)
    dash = compute_overview_dashboard(instances, score)
    stats, _ = aggregate_match_stats(instances)

    poss = stats.get("Possession", {})
    poss_own = poss.get("own", {}).get("duration", 0)
    poss_adv = poss.get("adverse", {}).get("duration", 0)
    poss_total = poss_own + poss_adv

    plaquage = stats.get("Plaquage", {}).get("own", {})
    ruck = stats.get("Ruck", {})
    discipline = stats.get("Disciplines", {})
    lineout = dash.get("lineout") or {}
    scrum = dash.get("scrum") or {}

    return {
        "points_per_entry": dash.get("points_per_entry"),
        "discipline_own": round(discipline.get("own", {}).get("count", 0) / nb, 1),
        "lost_balls_own": round(dash.get("lost_balls_own", 0) / nb, 1),
        "possession_pct": round(poss_own / poss_total * 100, 1) if poss_total else None,
        "plaquage_success_rate": plaquage.get("success_rate"),
        "ruck_own": round(ruck.get("own", {}).get("count", 0) / nb, 1),
        "lineout_success_rate": (lineout.get("own") or {}).get("success_rate"),
        "scrum_won_pct": (scrum.get("own") or {}).get("won_pct"),
    }
    
def compute_season_dashboard(selected_matches):
    """Bilan cumulé sur plusieurs matchs (saison complète ou sélection personnalisée par Téo).

    Concatène les instances brutes des matchs choisis pour recalculer, avec les mêmes
    fonctions que la page d'un match, le détail du score, les entrées, la touche/mêlée, etc.
    Ajoute en plus un bilan Victoires/Nuls/Défaites et une répartition possession/occupation
    match par match (les tranches de 20 minutes n'ont pas de sens une fois plusieurs matchs
    mis bout à bout, donc on les remplace ici)."""
    all_instances = []
    record = {"wins": 0, "draws": 0, "losses": 0}
    points_for = points_against = 0
    possession_by_match = []
    occupation_by_match = []

    for m in selected_matches:
        insts = m["instances"]
        all_instances.extend(insts)

        sc = compute_score(insts)
        points_for += sc["own"]
        points_against += sc["adverse"]
        if sc["own"] > sc["adverse"]:
            record["wins"] += 1
        elif sc["own"] < sc["adverse"]:
            record["losses"] += 1
        else:
            record["draws"] += 1

        label = f"{m['match_date'] or '?'} {m['opponent']}"

        poss = m["stats"].get("Possession", {})
        poss_own = poss.get("own", {}).get("duration", 0)
        poss_adv = poss.get("adverse", {}).get("duration", 0)
        poss_total = poss_own + poss_adv
        possession_by_match.append({
            "label": label,
            "own_pct": round(poss_own / poss_total * 100) if poss_total else None,
            "adverse_pct": round(poss_adv / poss_total * 100) if poss_total else None,
        })

        occ = compute_occupation(insts)
        occupation_by_match.append({
            "label": label,
            "own_pct": occ["own_pct"],
            "adverse_pct": occ["adverse_pct"],
        })

    combined_score = {"own": points_for, "adverse": points_against}
    dash = compute_overview_dashboard(all_instances, combined_score)

    total_bip = sum(max(i["duration"], 0) for i in all_instances
                    if i["kind"] == "control" and i["category"] == "Ball In Play")
    nb = len(selected_matches) or 1
    dash["ball_in_play"] = {
        "duration": round(total_bip, 1),
        "duration_fmt": _fmt_mmss(total_bip),
        "avg_per_match_fmt": _fmt_mmss(total_bip / nb),
    }
    dash["possession_by_period"] = None
    dash["occupation_by_period"] = None
    dash["possession_by_match"] = possession_by_match
    dash["occupation_by_match"] = occupation_by_match
    dash["record"] = record
    dash["points_for"] = points_for
    dash["points_against"] = points_against
    dash["nb_matches"] = len(selected_matches)
    return dash


def compute_player_ruck_table(instances):
    """Ruck offensif (arrivée 1/2/3/4+, ancreur, raseur) + ruck défensif (gratteur, contre-ruck) par joueur."""
    rows = {}
    for name in _player_names(instances):
        rows[name] = {"arr1_plus": 0, "arr1_minus": 0, "arr2_plus": 0, "arr2_minus": 0,
                      "ancreur_plus": 0, "ancreur_minus": 0, "raseur_plus": 0, "raseur_minus": 0,
                      "gratteur_plus": 0, "gratteur_minus": 0, "contre_ruck_plus": 0, "contre_ruck_minus": 0}

    for i in instances:
        if i["kind"] != "player":
            continue
        row = rows[i["code_raw"]]
        ruck_txts = _label_texts(i, "RUCK")
        off_txts = _label_texts(i, "ruck off")
        def_txts = _label_texts(i, "ruck def")
        is_off = "RUCK OFF" in ruck_txts
        is_def = "RUCK DEF" in ruck_txts
        sign_off = "+" if "+" in off_txts else ("-" if "-" in off_txts else None)
        sign_def = "+" if "+" in def_txts else ("-" if "-" in def_txts else None)

        if is_off and sign_off:
            if "1" in off_txts:
                row["arr1_plus" if sign_off == "+" else "arr1_minus"] += 1
            elif "2" in off_txts:
                row["arr2_plus" if sign_off == "+" else "arr2_minus"] += 1
            if "ANCREUR" in ruck_txts:
                row["ancreur_plus" if sign_off == "+" else "ancreur_minus"] += 1
            elif "RASEUR" in ruck_txts:
                row["raseur_plus" if sign_off == "+" else "raseur_minus"] += 1
        if is_def and sign_def and "GRATTEUR" in ruck_txts:
            row["gratteur_plus" if sign_def == "+" else "gratteur_minus"] += 1
        if "CONTRE" in " ".join(ruck_txts).upper():
            key = "contre_ruck_plus" if sign_off == "+" or sign_def == "+" else "contre_ruck_minus"
            row[key] += 1

    result = []
    totals = defaultdict(int)
    for name, r in rows.items():
        total_involvement = sum(r.values())
        if total_involvement == 0:
            continue
        gratteur_tot = r["gratteur_plus"] + r["gratteur_minus"]
        contre_tot = r["contre_ruck_plus"] + r["contre_ruck_minus"]
        points = (r["arr1_plus"] + r["arr2_plus"] * 1 + r["ancreur_plus"] * 1 + r["raseur_plus"] * 1
                  + r["gratteur_plus"] * 3 + r["contre_ruck_plus"] * 3
                  - r["arr1_minus"] - r["arr2_minus"] - r["ancreur_minus"] - r["raseur_minus"]
                  - r["gratteur_minus"] - r["contre_ruck_minus"])
        row_out = {
            "name": name, **r,
            "gratteur_pct": round(r["gratteur_plus"] / gratteur_tot * 100) if gratteur_tot else None,
            "contre_ruck_pct": round(r["contre_ruck_plus"] / contre_tot * 100) if contre_tot else None,
            "points": points,
        }
        result.append(row_out)
        for k, v in r.items():
            totals[k] += v
        totals["points"] += points
    result.sort(key=lambda x: -x["points"])
    return {"rows": result, "totals": dict(totals)}


# ---- Comparateur de joueurs (saison) ------------------------------------------

def _zero_attack_row(name):
    return {
        "name": name, "contact_plus": 0, "contact_minus": 0, "contact_total": 0, "contact_pct": None,
        "duel_plus": 0, "duel_minus": 0, "duel_total": 0, "duel_pct": None,
        "passe_plus": 0, "passe_minus": 0, "passe_total": 0, "passe_pct": None,
        "offload_plus": 0, "offload_minus": 0, "offload_total": 0, "offload_pct": None,
        "pdb": 0, "def_battu": 0, "break": 0, "points": 0,
    }


def _zero_defense_row(name):
    return {
        "name": name, "plaquage_dominant": 0, "plaquage_neutre": 0, "plaquage_passif": 0,
        "plaquage_rate": 0, "tackle_total": 0, "tackle_pct": None,
        "assist_plus": 0, "assist_minus": 0, "discipline": 0, "points": 0,
    }


def _zero_ruck_row(name):
    return {
        "name": name, "arr1_plus": 0, "arr1_minus": 0, "arr2_plus": 0, "arr2_minus": 0,
        "ancreur_plus": 0, "ancreur_minus": 0, "raseur_plus": 0, "raseur_minus": 0,
        "gratteur_plus": 0, "gratteur_minus": 0, "gratteur_pct": None,
        "contre_ruck_plus": 0, "contre_ruck_minus": 0, "contre_ruck_pct": None,
        "points": 0,
    }


def _find_or_zero(rows, name, zero_fn):
    """Comparaison insensible à la casse : le XML Sportscode tague les joueurs en
    MAJUSCULES ('ROUET') alors que la feuille de poste utilise la casse normale
    ('Rouet') — sans ça, un joueur avec des vraies stats afficherait des zéros."""
    target = name.strip().casefold()
    for r in rows:
        if r["name"].strip().casefold() == target:
            out = dict(r)
            out["name"] = name
            return out
    return zero_fn(name)


def compute_player_comparison(instances, player_a, player_b):
    """Page Comparateur (saison) : reprend exactement les 3 tableaux de la page Joueurs
    (attaque/défense/ruck), calculés sur les matchs sélectionnés, et ne garde que les
    2 lignes des joueurs choisis dans les menus déroulants. Si un joueur n'a aucune
    statistique sur la sélection (n'a pas joué, ou pas de data codée), on affiche une
    ligne à zéro plutôt que de le faire disparaître."""
    attack = compute_player_attack_table(instances)
    defense = compute_player_defense_table(instances)
    ruck = compute_player_ruck_table(instances)
    return {
        "attack_rows": [_find_or_zero(attack["rows"], player_a, _zero_attack_row),
                        _find_or_zero(attack["rows"], player_b, _zero_attack_row)],
        "defense_rows": [_find_or_zero(defense["rows"], player_a, _zero_defense_row),
                          _find_or_zero(defense["rows"], player_b, _zero_defense_row)],
        "ruck_rows": [_find_or_zero(ruck["rows"], player_a, _zero_ruck_row),
                      _find_or_zero(ruck["rows"], player_b, _zero_ruck_row)],
    }
def _normalize_count(value_a, value_b):
    """Ramène 2 valeurs brutes (volumes, pas des %) sur une échelle 0-100 pour un radar :
    100 pour la plus grande des deux, proportionnel pour l'autre (0 partout si les deux
    valent 0). Comparaison relative entre les 2 joueurs, pas un score absolu."""
    top = max(value_a, value_b, 0)
    if top == 0:
        return 0, 0
    return round(value_a / top * 100), round(value_b / top * 100)


def _normalize_count_inverted(value_a, value_b):
    """Comme _normalize_count mais 'moins = mieux' (ex: fautes de discipline) : la valeur la
    plus basse obtient 100, l'autre proportionnellement moins. 100 partout si les deux valent 0
    (aucune faute)."""
    top = max(value_a, value_b, 0)
    if top == 0:
        return 100, 100
    return round((top - value_a) / top * 100), round((top - value_b) / top * 100)


def compute_comparison_radars(attack_rows, defense_rows, ruck_rows):
    """3 radars (Attaque/Défense/Ruck) pour la page Comparateur, en plus des tableaux déjà
    affichés : une vue d'ensemble visuelle des 2 joueurs superposés sur un même graphique.
    Chaque axe est ramené sur 0-100 : les % déjà calculés (contact_pct, tackle_pct...) sont
    gardés tels quels (0 si pas de data), les volumes bruts (def_battu, plaquage_dominant...)
    sont normalisés relativement entre les 2 joueurs via _normalize_count, et la discipline
    (moins de fautes = mieux) est inversée via _normalize_count_inverted."""
    a_atk, b_atk = attack_rows
    a_def, b_def = defense_rows
    a_ruck, b_ruck = ruck_rows

    def_battu_a, def_battu_b = _normalize_count(a_atk["def_battu"], b_atk["def_battu"])
    break_a, break_b = _normalize_count(a_atk["break"], b_atk["break"])
    attack_radar = {
        "labels": ["Contact %", "Duel %", "Passe %", "Offload %", "Déf. battus", "Breaks"],
        "a": [a_atk["contact_pct"] or 0, a_atk["duel_pct"] or 0, a_atk["passe_pct"] or 0,
              a_atk["offload_pct"] or 0, def_battu_a, break_a],
        "b": [b_atk["contact_pct"] or 0, b_atk["duel_pct"] or 0, b_atk["passe_pct"] or 0,
              b_atk["offload_pct"] or 0, def_battu_b, break_b],
    }

    plaq_dom_a, plaq_dom_b = _normalize_count(a_def["plaquage_dominant"], b_def["plaquage_dominant"])
    assist_a, assist_b = _normalize_count(max(a_def["assist_plus"] - a_def["assist_minus"], 0),
                                           max(b_def["assist_plus"] - b_def["assist_minus"], 0))
    discipline_a, discipline_b = _normalize_count_inverted(a_def["discipline"], b_def["discipline"])
    defense_radar = {
        "labels": ["Plaquages dominants", "Taux plaquage %", "Assists", "Discipline"],
        "a": [plaq_dom_a, a_def["tackle_pct"] or 0, assist_a, discipline_a],
        "b": [plaq_dom_b, b_def["tackle_pct"] or 0, assist_b, discipline_b],
    }

    ancreur_a, ancreur_b = _normalize_count(a_ruck["ancreur_plus"], b_ruck["ancreur_plus"])
    raseur_a, raseur_b = _normalize_count(a_ruck["raseur_plus"], b_ruck["raseur_plus"])
    arrivees_a, arrivees_b = _normalize_count(a_ruck["arr1_plus"] + a_ruck["arr2_plus"],
                                               b_ruck["arr1_plus"] + b_ruck["arr2_plus"])
    ruck_radar = {
        "labels": ["Ancreur", "Raseur", "Gratteur %", "Contre-ruck %", "Arrivées 1er/2e"],
        "a": [ancreur_a, raseur_a, a_ruck["gratteur_pct"] or 0, a_ruck["contre_ruck_pct"] or 0, arrivees_a],
        "b": [ancreur_b, raseur_b, b_ruck["gratteur_pct"] or 0, b_ruck["contre_ruck_pct"] or 0, arrivees_b],
    }

    return {"attack": attack_radar, "defense": defense_radar, "ruck": ruck_radar}

# ---- Effectif de la saison (feuille de poste, pas de hiérarchie/statut) ------

SQUAD_ROSTER = {
    "Pilier": ["Thompson Stringer", "Gonzalez", "Martinez", "Kapanadze", "Falgoux", "Pupuma",
               "Ciancio", "Mudariki", "Farrance", "Aouad", "Kodad", "Navarrete"],
    "Talonneur": ["Martinez", "Strippoli", "Chauvin", "Moreno", "Leafa"],
    "2ème ligne": ["Rey", "Van der Merwe", "Kpoku", "Motoc", "Olmstead", "Wolsink", "Fender"],
    "3ème ligne": ["Vignolles", "Sarrasin", "Berenguel", "Bachelier", "Labadie", "Sirgel",
                   "Dakuwaqa", "Laurans", "Blondin", "Bossorey", "Bergamaschi"],
    "Charnière": ["Gimbert", "Rouet", "Idjellidaine", "Williams", "Barraque", "Ortolan",
                  "Zamora", "Zelioli", "Asquini"],
    "Centre": ["Septar", "Ezcurra", "Saili", "Morgan", "Lafond", "Flambart",
               "Bielle-Biarrey", "Khaindrava"],
    "Ailier/Arrière": ["Egiziano", "Rattez", "Nalaga", "Patilla", "Goutard", "Farnoux",
                       "Provencel", "Niel", "Berger", "Castaignede"],
}
SQUAD_POSITION_ORDER = list(SQUAD_ROSTER.keys())


def _placeholder_attack_row(name):
    return {
        "name": name, "contact_plus": "—", "contact_minus": "—", "contact_total": "—", "contact_pct": None,
        "duel_plus": "—", "duel_minus": "—", "duel_total": "—", "duel_pct": None,
        "passe_plus": "—", "passe_minus": "—", "passe_total": "—", "passe_pct": None,
        "offload_plus": "—", "offload_minus": "—", "offload_total": "—", "offload_pct": None,
        "pdb": "—", "def_battu": "—", "break": "—", "points": "—",
    }


def _placeholder_defense_row(name):
    return {
        "name": name, "plaquage_dominant": "—", "plaquage_neutre": "—", "plaquage_passif": "—",
        "plaquage_rate": "—", "tackle_total": "—", "tackle_pct": None,
        "assist_plus": "—", "assist_minus": "—", "discipline": "—", "points": "—",
    }


def _placeholder_ruck_row(name):
    return {
        "name": name, "arr1_plus": "—", "arr1_minus": "—", "arr2_plus": "—", "arr2_minus": "—",
        "ancreur_plus": "—", "ancreur_minus": "—", "raseur_plus": "—", "raseur_minus": "—",
        "gratteur_plus": "—", "gratteur_minus": "—", "gratteur_pct": None,
        "contre_ruck_plus": "—", "contre_ruck_minus": "—", "contre_ruck_pct": None,
        "points": "—",
    }

def compute_squad_preview():
    """Effectif complet groupé par poste (feuille de poste fournie par Téo, sans ordre
    de hiérarchie ni statut), avec les 3 tableaux (attaque/défense/ruck) identiques à
    ceux de la page Joueurs d'un match. Toutes les valeurs sont des placeholders '—' :
    le cumul des vraies stats sur plusieurs matchs de la saison n'est pas encore branché,
    ceci sert à valider le rendu (regroupement par poste) avant de le faire."""
    groups = []
    for position in SQUAD_POSITION_ORDER:
        names = SQUAD_ROSTER[position]
        groups.append({
            "position": position,
            "attack_rows": [_placeholder_attack_row(n) for n in names],
            "defense_rows": [_placeholder_defense_row(n) for n in names],
            "ruck_rows": [_placeholder_ruck_row(n) for n in names],
        })
    return groups


def compute_player_tracking(selected_matches):
    """Suivi individuel (minutes jouées, cartons, matchs de suite) à partir des données
    saisies à la main match par match sur la page Composition. selected_matches doit être
    trié chronologiquement du plus ancien au plus récent (c'est déjà le cas de la liste
    renvoyée par _season_context()). Pour chaque match où la saisie minutes/cartons a été
    faite (match['player_match_stats'] non vide) : un joueur avec 0 minute ou absent de la
    composition ce match-là est considéré comme n'ayant pas joué, ce qui coupe sa série de
    matchs de suite. Les matchs pas encore renseignés (saisie vide) sont ignorés plutôt que
    comptés comme "pas joué", pour ne pas casser artificiellement les séries en cours."""
    tracking = {
        name: {"minutes": 0, "matches_played": 0, "yellow": 0, "red": 0, "streak": 0}
        for names in SQUAD_ROSTER.values() for name in names
    }
    for m in selected_matches:
        pstats = m.get("player_match_stats") or {}
        if not pstats:
            continue
        for name, row in tracking.items():
            s = pstats.get(name) or {}
            minutes = s.get("minutes") or 0
            yellow = s.get("yellow") or 0
            red = s.get("red") or 0
            row["minutes"] += minutes
            row["yellow"] += yellow
            row["red"] += red
            if minutes > 0:
                row["matches_played"] += 1
                row["streak"] += 1
            else:
                row["streak"] = 0
    return tracking


def compute_squad_season_stats(instances, selected_matches=None):
    """Effectif complet groupé par poste, avec les VRAIES statistiques cumulées sur les
    matchs sélectionnés (mêmes 3 tableaux attaque/défense/ruck que sur les autres pages),
    plus un tableau de suivi (minutes/cartons/matchs de suite) basé sur les données saisies
    à la main sur la page Composition de chaque match. Remplace compute_squad_preview (qui
    n'affichait que des '—' placeholder) maintenant que le calcul season-cumulé par joueur
    existe (voir compute_player_comparison). Un joueur de l'effectif qui n'a aucune stat sur
    la sélection (blessé, n'a pas joué...) affiche une ligne à zéro plutôt que de disparaître,
    pour garder tout l'effectif visible."""
    attack = compute_player_attack_table(instances)
    defense = compute_player_defense_table(instances)
    ruck = compute_player_ruck_table(instances)
    tracking = compute_player_tracking(selected_matches or [])
    groups = []
    for position in SQUAD_POSITION_ORDER:
        names = SQUAD_ROSTER[position]
        groups.append({
            "position": position,
            "attack_rows": [_find_or_zero(attack["rows"], n, _zero_attack_row) for n in names],
            "defense_rows": [_find_or_zero(defense["rows"], n, _zero_defense_row) for n in names],
            "ruck_rows": [_find_or_zero(ruck["rows"], n, _zero_ruck_row) for n in names],
            "tracking_rows": [{"name": n, **tracking[n]} for n in names],
        })
    return groups

# ---- Suivi JIFF (quota LNR) --------------------------------------------------
# Liste des joueurs NON-JIFF de l'effectif (donnée par Téo) ; tous les autres
# joueurs de SQUAD_ROSTER sont considérés JIFF par défaut.
NON_JIFF_PLAYERS = {
    "Thompson Stringer", "Kapanadze", "Leafa", "Pupuma", "Mudariki", "Kpoku", "Olmstead",
    "Sirgel", "Dakuwaqa", "Williams", "Ezcurra", "Morgan", "Saili",
    "Navarrete", "Fender", "Wolsink", "Bergamaschi", "Khaindrava",
}

JIFF_QUOTA = 14  # quota LNR (moyenne de joueurs JIFF par feuille de match sur la saison)


def is_jiff(player_name):
    return player_name not in NON_JIFF_PLAYERS


def compute_jiff_chart(selected_matches):
    """selected_matches : liste de matchs (dicts, avec une clé 'composition' = liste de
    noms de joueurs) déjà filtrés sur la sélection saison. Ne garde que les matchs dont
    la composition a été validée (liste non vide), et calcule pour chacun le nombre de
    JIFF sur les joueurs alignés, l'écart par rapport au quota LNR, et la moyenne saison."""
    rows = []
    for m in selected_matches:
        comp = [p for p in (m.get("composition") or []) if p]
        if not comp:
            continue
        jiff_count = sum(1 for p in comp if is_jiff(p))
        non_jiff_count = len(comp) - jiff_count
        diff = jiff_count - JIFF_QUOTA
        opponent = m.get("opponent") or "?"
        date = m.get("match_date") or ""
        rows.append({
            "match_id": m["id"],
            "label": f"{opponent} ({date})" if date else opponent,
            "jiff_count": jiff_count,
            "non_jiff_count": non_jiff_count,
            "total": len(comp),
            "diff": diff,
            "respecte": diff >= 0,
        })

    if rows:
        avg_jiff = round(sum(r["jiff_count"] for r in rows) / len(rows), 1)
        avg_diff = round(avg_jiff - JIFF_QUOTA, 1)
        respecte_saison = avg_diff >= 0
        total_jiff = sum(r["jiff_count"] for r in rows)
        total_required = JIFF_QUOTA * len(rows)
        total_diff = total_jiff - total_required
    else:
        avg_jiff = None
        avg_diff = None
        respecte_saison = False
        total_jiff = None
        total_required = None
        total_diff = None

    return {
        "rows": rows,
        "quota": JIFF_QUOTA,
        "avg_jiff": avg_jiff,
        "avg_diff": avg_diff,
        "respecte_saison": respecte_saison,
        "total_jiff": total_jiff,
        "total_required": total_required,
        "total_diff": total_diff,
    }


# ---- Transition (contre-attaques et turnovers) -------------------------------

def compute_transition_sector(instances):
    """Nouvelle stat 'Transition' (contre-attaques et turnovers) que Téo va coder avec
    son propre système cette saison : rien n'est encore branché sur le XML (même le
    volume), donc tout reste en placeholder '—' pour l'instant. Le paramètre `instances`
    est gardé pour la même signature que les autres compute_* une fois le codage prêt."""
    result = {}
    for key in ("contre_attaque", "turnover"):
        result[key] = {}
        for side in ("own", "adverse"):
            result[key][side] = {
                "count": None,  # pas encore codé dans le XML
                "phases_avant_perte": None,  # pas encore codé dans le XML
                "resultat": {},  # pas encore codé dans le XML (répartition par type de résultat)
                "zone": {"Proche": None, "Milieu": None, "Large": None},  # pas encore codé dans le XML
            }
    return result
    # ---- Entraînement (suivi du volume par thème, saisie manuelle) ---------------
# Taxonomie fournie par Téo (grille de suivi du coach) : 5 grandes catégories, chacune
# divisée en sous-catégories, chacune listant des éléments précis travaillés à l'entraînement.
# Rien ici ne vient du XML Sportscode : c'est une liste de référence saisie manuellement,
# séance par séance, pour suivre combien de fois (et combien de temps) chaque thème est abordé.
TRAINING_TAXONOMY = {
    "Attaque": {
        "Individual Attack": [
            "Catch and pass", "Passing under pressure", "Ball carry techniques",
            "Footwork and evasion", "Acceleration into contact", "Offloads",
            "Finishing", "Kicking in attack", "Decision making",
        ],
        "Unit Attack": [
            "Pod play", "Forward handling", "Backline attack", "Strike plays",
            "Multi-phase attack", "Support lines", "Width and depth", "Continuity",
        ],
        "Team Attack": [
            "Shape", "Tempo", "Territory vs possession", "Transition attack",
            "Counter attack", "Red zone attack", "Exit attack", "Advantage play",
        ],
    },
    "Défense": {
        "Individual Defence": [
            "Tackle technique", "Tracking", "Dominant collisions", "Chop tackles",
            "Ball steals", "Defensive footwork",
        ],
        "Unit Defence": [
            "Fold defence", "Drift defence", "Blitz defence", "Line speed",
            "Connection", "Edge defence", "Goal-line defence",
        ],
        "Team Defence": [
            "Defensive systems", "Transition defence", "Kick chase",
            "Defensive communication", "Turnover response", "Pressure strategies",
        ],
        "Defensive Ruck": [
            "Jackal", "Counter-ruck", "Defensive decision making", "Ruck organisation",
        ],
    },
    "Contact": {
        "Ball Carry": [
            "Winning collisions", "Leg drive", "Body position",
            "Ball presentation", "Fighting through contact",
        ],
        "Cleanout": [
            "Accuracy", "Power", "Decision making", "Speed",
        ],
    },
    "Set Piece": {
        "Scrum": [
            "Individual technique", "Unit cohesion", "Stability",
            "Attack from scrum", "Defensive scrum", "Scrum exits",
        ],
        "Lineout": [
            "Throwing", "Jumping", "Lifting", "Calling", "Movement",
            "Maul launch", "Defensive lineout",
        ],
        "Restart": [
            "Kick receipt", "Contestable restarts", "Receiving organisation", "Exit structures",
        ],
    },
    "Jeu au pied": {
        "Technique": [
            "Punt", "Spiral", "Box kick", "Chip", "Grubber", "Drop-out", "Goal kicking",
        ],
        "Tactique": [
            "Exit kicking", "Contestable kicking", "Territory",
            "Kick return", "Kick pressure", "Kick chase",
        ],
    },
}


def compute_training_volume(sessions):
    """sessions : liste de séances (dicts avec 'id' et 'items' = liste de
    {category, subcategory, element, minutes}). Calcule, pour chaque catégorie >
    sous-catégorie > élément de TRAINING_TAXONOMY, le nombre de séances distinctes où cet
    élément a été coché et le total de minutes renseignées. 'minutes' reste None pour un
    élément si aucune séance n'a jamais précisé de durée dessus (distingue '0 minute
    saisie' de 'pas de data'), pour ne pas afficher un faux 0 dans le template."""
    stats = defaultdict(lambda: {"session_ids": set(), "minutes": 0, "has_minutes": False})
    for s in sessions:
        for item in (s.get("items") or []):
            key = (item.get("category"), item.get("subcategory"), item.get("element"))
            stats[key]["session_ids"].add(s["id"])
            if item.get("minutes") is not None:
                stats[key]["minutes"] += item["minutes"]
                stats[key]["has_minutes"] = True

    categories = []
    for cat_name, subcats in TRAINING_TAXONOMY.items():
        cat_session_ids = set()
        cat_minutes = 0
        cat_has_minutes = False
        subcat_list = []
        for subcat_name, elements in subcats.items():
            subcat_session_ids = set()
            subcat_minutes = 0
            subcat_has_minutes = False
            element_list = []
            for element in elements:
                d = stats.get((cat_name, subcat_name, element))
                if d:
                    element_list.append({
                        "name": element,
                        "session_count": len(d["session_ids"]),
                        "minutes": d["minutes"] if d["has_minutes"] else None,
                    })
                    subcat_session_ids |= d["session_ids"]
                    subcat_minutes += d["minutes"]
                    subcat_has_minutes = subcat_has_minutes or d["has_minutes"]
                else:
                    element_list.append({"name": element, "session_count": 0, "minutes": None})
            subcat_list.append({
                "name": subcat_name,
                "session_count": len(subcat_session_ids),
                "minutes": subcat_minutes if subcat_has_minutes else None,
                "elements": element_list,
            })
            cat_session_ids |= subcat_session_ids
            cat_minutes += subcat_minutes
            cat_has_minutes = cat_has_minutes or subcat_has_minutes
        categories.append({
            "name": cat_name,
            "session_count": len(cat_session_ids),
            "minutes": cat_minutes if cat_has_minutes else None,
            "subcategories": subcat_list,
        })

    return {"categories": categories, "total_sessions": len(sessions)}


def compute_subcategory_breakdown(volume):
    """Aplatit les sous-catégories d'un volume (compute_training_volume) en une liste prête
    pour un graphique camembert : une part par sous-catégorie effectivement travaillée sur
    la période. Utilise les minutes comme poids si TOUTES les sous-catégories actives ont
    une durée renseignée cette période, sinon retombe sur le nombre de séances — pour ne
    jamais mélanger deux unités différentes (minutes et séances) dans le même camembert."""
    active = [sub for cat in volume["categories"] for sub in cat["subcategories"] if sub["session_count"] > 0]
    if not active:
        return {"labels": [], "values": [], "metric": None}
    use_minutes = all(s["minutes"] is not None for s in active)
    if use_minutes:
        return {"labels": [s["name"] for s in active], "values": [s["minutes"] for s in active], "metric": "minutes"}
    return {"labels": [s["name"] for s in active], "values": [s["session_count"] for s in active], "metric": "séances"}


_MONTHS_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]


# Lundi de la "Semaine 1" de la saison (reprise de la présaison, donné par Téo :
# la semaine du 13 au 19 juillet 2026 est la Semaine 3, donc la Semaine 1 démarre
# le lundi 29 juin 2026). Toute la numérotation "Semaine N" du rapport se cale sur
# cette date plutôt que sur le numéro de semaine calendaire (ISO), qui ne correspond
# pas au découpage du club.
SEASON_WEEK1_MONDAY = datetime(2026, 6, 29).date()


def _week_label(d):
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    week_number = (monday - SEASON_WEEK1_MONDAY).days // 7 + 1
    if week_number < 1:
        return f"Avant-saison ({monday.strftime('%d/%m')} au {sunday.strftime('%d/%m/%Y')})"
    return f"Semaine {week_number} ({monday.strftime('%d/%m')} au {sunday.strftime('%d/%m/%Y')})"


def _month_label(d):
    return f"{_MONTHS_FR[d.month - 1]} {d.year}"


def group_training_sessions_by_period(sessions, period="week"):
    """Regroupe les séances par semaine calendaire (ISO) ou par mois, et calcule pour
    chaque période le volume par catégorie/sous-catégorie/élément (compute_training_volume)
    sur les seules séances de cette période. Pour le rapport coach hebdo/mensuel : pas
    besoin du détail jour par jour, juste le temps passé par thème sur la période. Périodes
    triées de la plus récente à la plus ancienne. Les séances sans date valide sont ignorées."""
    groups = {}
    for s in sessions:
        raw_date = s.get("session_date")
        if not raw_date:
            continue
        try:
            d = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if period == "month":
            key = (d.year, d.month)
            label = _month_label(d)
        else:
            iso_year, iso_week, _ = d.isocalendar()
            key = (iso_year, iso_week)
            label = _week_label(d)
        groups.setdefault(key, {"label": label, "sessions": []})
        groups[key]["sessions"].append(s)

    periods = []
    for key in sorted(groups.keys(), reverse=True):
        g = groups[key]
        volume = compute_training_volume(g["sessions"])
        periods.append({
            "label": g["label"],
            "session_count": len(g["sessions"]),
            "volume": volume,
            "breakdown": compute_subcategory_breakdown(volume),
        })
    return periods
# ---- Data Analyse (corrélation indicateurs / victoire-défaite) --------------
# Jeu d'indicateurs par match, réutilisant les fonctions déjà utilisées sur les pages
# sectorielles (Attaque/Défense/Ruck/Touches/Mêlée). 'higher_is_better' indique le sens
# souhaité (ex: plus de réussite au plaquage = mieux, mais moins de pertes de balle = mieux)
# pour que le calcul d'écart victoire/défaite pointe dans le bon sens.
KPI_DEFINITIONS = [
    {"key": "tackle_pct", "label": "Réussite au plaquage", "unit": "%", "higher_is_better": True},
    {"key": "lineout_pct", "label": "Réussite en touche", "unit": "%", "higher_is_better": True},
    {"key": "scrum_pct", "label": "Réussite en mêlée", "unit": "%", "higher_is_better": True},
    {"key": "points_per_entry", "label": "Points par entrée en zone d'attaque", "unit": "", "higher_is_better": True},
    {"key": "offloads", "label": "Offloads", "unit": "", "higher_is_better": True},
    {"key": "breaks", "label": "Franchissements (breaks)", "unit": "", "higher_is_better": True},
    {"key": "lost_balls", "label": "Pertes de balle", "unit": "", "higher_is_better": False},
    {"key": "turnovers_won", "label": "Turnovers gagnés", "unit": "", "higher_is_better": True},
    {"key": "discipline", "label": "Fautes concédées (discipline)", "unit": "", "higher_is_better": False},
    {"key": "ruck_speed_fast_pct", "label": "Rucks joués en moins de 3s", "unit": "%", "higher_is_better": True},
    {"key": "duels_aeriens_pct", "label": "Réussite duels aériens", "unit": "%", "higher_is_better": True},
    {"key": "gratteur_plus", "label": "Ballons grattés en défense", "unit": "", "higher_is_better": True},
]


def compute_match_kpis(instances):
    """Extrait un jeu d'indicateurs numériques pour UN match (instances de ce seul match,
    pas concaténées), à partir des mêmes fonctions déjà utilisées sur les pages secteur.
    Une valeur peut être None si rien n'est codé sur ce match pour cet indicateur précis
    (le match est alors simplement ignoré pour cet indicateur dans la comparaison)."""
    plaquage = compute_plaquage_detail(instances)
    lineout = compute_lineout_detail(instances)
    scrum = compute_scrum_detail(instances)
    attack = compute_attack_sector(instances, "own")
    defense_adv = compute_defense_sector(instances, "adverse")  # turnovers_recuperes = gagnés par nous
    ruck_speed_own = compute_ruck_speed(instances, "own")
    ruck_totals = compute_player_ruck_table(instances)["totals"]

    ruck_fast_pct = ruck_speed_own["buckets"]["-3s"]["pct"] if ruck_speed_own["total"] else None

    return {
        "tackle_pct": plaquage["rate_pct"],
        "lineout_pct": lineout["own"]["success_rate"],
        "scrum_pct": scrum["own"]["won_pct"],
        "points_per_entry": attack["points_per_entry"],
        "offloads": attack["offloads"],
        "breaks": attack["breaks"],
        "lost_balls": attack["lost_balls"],
        "turnovers_won": defense_adv["turnovers_recuperes"],
        "discipline": _cat_count(instances, "Disciplines", "own"),
        "ruck_speed_fast_pct": ruck_fast_pct,
        "duels_aeriens_pct": attack["duels_aeriens"]["pct"],
        "gratteur_plus": ruck_totals.get("gratteur_plus", 0),
    }


def _pooled_std(a, b):
    """Écart-type combiné de 2 échantillons (pour un effet de type Cohen's d), None si
    l'un des 2 groupes a moins de 2 valeurs (variance non calculable)."""
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    var1 = statistics.variance(a)
    var2 = statistics.variance(b)
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    return pooled_var ** 0.5


def compute_win_loss_analysis(matches):
    """matches : liste de matchs du filtre saison (dicts avec 'instances' = liste NON
    concaténée, propre à chaque match). Classe chaque match victoire/défaite via
    compute_score (les matchs nuls sont ignorés, trop rares/ambigus en rugby pour ce
    calcul), calcule les indicateurs de KPI_DEFINITIONS pour chaque match, puis compare
    la moyenne en victoire vs en défaite pour chaque indicateur. Le classement utilise un
    effet standardisé façon Cohen's d (écart / écart-type combiné) plutôt que l'écart brut,
    pour pouvoir comparer équitablement des indicateurs à échelles différentes (% vs
    comptages). Renvoie 'insufficient_data': True s'il n'y a pas au moins 2 victoires ET
    2 défaites sur la sélection (comparaison non fiable en dessous)."""
    wins_kpis, losses_kpis = [], []
    for m in matches:
        instances = m.get("instances") or []
        if not instances:
            continue
        score = compute_score(instances)
        if score["own"] > score["adverse"]:
            wins_kpis.append(compute_match_kpis(instances))
        elif score["own"] < score["adverse"]:
            losses_kpis.append(compute_match_kpis(instances))

    n_wins, n_losses = len(wins_kpis), len(losses_kpis)
    if n_wins < 2 or n_losses < 2:
        return {"insufficient_data": True, "n_wins": n_wins, "n_losses": n_losses}

    rows = []
    for kpi_def in KPI_DEFINITIONS:
        key = kpi_def["key"]
        win_values = [k[key] for k in wins_kpis if k[key] is not None]
        loss_values = [k[key] for k in losses_kpis if k[key] is not None]
        if len(win_values) < 2 or len(loss_values) < 2:
            continue  # pas assez de matchs avec cette donnée codée pour comparer

        win_avg = sum(win_values) / len(win_values)
        loss_avg = sum(loss_values) / len(loss_values)
        raw_gap = win_avg - loss_avg
        pooled_std = _pooled_std(win_values, loss_values)
        if pooled_std:
            effect = raw_gap / pooled_std
        else:
            # Valeurs constantes dans les 2 groupes mais différentes entre eux :
            # séparation totale, donc un signal fort malgré une variance nulle.
            effect = 0.0 if raw_gap == 0 else (3.0 if raw_gap > 0 else -3.0)
        signed_effect = effect if kpi_def["higher_is_better"] else -effect

        rows.append({
            "label": kpi_def["label"],
            "unit": kpi_def["unit"],
            "win_avg": round(win_avg, 1),
            "loss_avg": round(loss_avg, 1),
            "higher_is_better": kpi_def["higher_is_better"],
            "signed_effect": round(signed_effect, 2),
            "effect_abs": round(abs(signed_effect), 2),
            "bar_pct": round(min(abs(signed_effect) / 3, 1) * 100, 1),
            "n_win": len(win_values),
            "n_loss": len(loss_values),
        })

    strengths = sorted([r for r in rows if r["signed_effect"] > 0], key=lambda r: -r["signed_effect"])
    weaknesses = sorted([r for r in rows if r["signed_effect"] < 0], key=lambda r: r["signed_effect"])
    all_rows = sorted(rows, key=lambda r: -abs(r["signed_effect"]))

    return {
        "insufficient_data": False,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "all_rows": all_rows,
    }
