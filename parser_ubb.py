"""
Parser dedie au client UBB (Union Bordeaux Begles) : lit un export Sportscode ou
chaque instance a un code du type "<Equipe> - <Action>" (ex: "Union Bordeaux Begles -
Possession", "Montpellier Herault Rugby - Essai"), une convention DIFFERENTE de celle
utilisee par Nice sur son propre site (codes numerotes "21 - Plaquage Nice") -- donc pas
reutilisable telle quelle, mais tres proche du fichier "fournisseur" deja gere pour le
client Roumanie (meme principe "Equipe - Action" partout).

Phase 1 (urgence entretien UBB) : on se limite aux indicateurs directement lisibles et
non ambigus (comptages et durees de possession), sans reconstruire un score ou un calcul
de territoire qui demanderait plus de validation -- le score se saisit a la main comme
pour la Roumanie.
"""
import xml.etree.ElementTree as ET
from collections import defaultdict


def _parse_instances(path):
    tree = ET.parse(path)
    root = tree.getroot()
    out = []
    for inst in root.findall(".//instance"):
        code = (inst.findtext("code") or "").strip()
        start = float(inst.findtext("start") or 0)
        end = float(inst.findtext("end") or 0)
        labels = [
            {"group": (lab.findtext("group") or "").strip(), "text": (lab.findtext("text") or "").strip()}
            for lab in inst.findall("label")
        ]
        out.append({"code": code, "start": start, "end": end, "duration": max(end - start, 0), "labels": labels})
    return out


def parse_ubb_xml(path):
    """Renvoie la liste brute des instances (code, start, end, duration, labels), sans
    aucune hypothese sur quelle equipe est UBB -- ca se precise au moment du calcul
    (compute_ubb_overview), une fois l'adversaire connu (saisi par Teo a l'import)."""
    return _parse_instances(path)


def _pct(a, b):
    total = a + b
    if total <= 0:
        return (0.0, 0.0)
    return (round(a / total * 100, 1), round(b / total * 100, 1))


def _count(instances, team, action):
    suffix = f" - {action}"
    return sum(1 for inst in instances if inst["code"] == f"{team}{suffix}")


def _duration(instances, team, action):
    suffix = f" - {action}"
    return sum(inst["duration"] for inst in instances if inst["code"] == f"{team}{suffix}")


def _card_counts(instances, team):
    """Compte les cartons par couleur via le label 'Carton' (Jaune/Rouge) plutôt que de
    se contenter d'un total, pour rester fidèle au codage (un carton rouge ne se lit pas
    pareil qu'un jaune)."""
    yellow = red = 0
    for inst in instances:
        if inst["code"] != f"{team} - Carton":
            continue
        color = next((lab["text"] for lab in inst["labels"] if lab["group"] == "Carton"), "")
        if color.lower().startswith("jaune"):
            yellow += 1
        elif color.lower().startswith("rouge"):
            red += 1
    return {"yellow": yellow, "red": red}


def compute_ubb_overview(instances, own_team="Union Bordeaux Begles", opp_team=None):
    """Calcule les indicateurs Phase 1 pour un match UBB, à partir des codes
    '<Equipe> - <Action>'. own_team est toujours 'Union Bordeaux Begles' (nom exact
    utilisé dans les 3 fichiers Top 14 fournis) ; opp_team doit être le nom EXACT tel
    qu'il apparaît dans le fichier (ex: 'Montpellier Herault Rugby', 'RC Toulon',
    'ASM Clermont Auvergne') -- sinon les comptages adverses ressortiront à 0."""
    own_poss = _duration(instances, own_team, "Possession")
    opp_poss = _duration(instances, opp_team, "Possession") if opp_team else 0
    poss_own_pct, poss_opp_pct = _pct(own_poss, opp_poss)

    own_cards = _card_counts(instances, own_team)
    opp_cards = _card_counts(instances, opp_team) if opp_team else {"yellow": 0, "red": 0}

    return {
        "possession": {"own": poss_own_pct, "opp": poss_opp_pct},
        "tries": {
            "own": _count(instances, own_team, "Essai"),
            "opp": _count(instances, opp_team, "Essai") if opp_team else 0,
        },
        "penalties_conceded": {
            "own": _count(instances, own_team, "Penalite"),
            "opp": _count(instances, opp_team, "Penalite") if opp_team else 0,
        },
        "turnovers_lost": {
            "own": _count(instances, own_team, "Ballon perdu"),
            "opp": _count(instances, opp_team, "Ballon perdu") if opp_team else 0,
        },
        "lineouts": {
            "own": _count(instances, own_team, "Touche"),
            "opp": _count(instances, opp_team, "Touche") if opp_team else 0,
        },
        "scrums": {
            "own": _count(instances, own_team, "Melee"),
            "opp": _count(instances, opp_team, "Melee") if opp_team else 0,
        },
        "cards": {"own": own_cards, "opp": opp_cards},
    }
