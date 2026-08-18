import os
import time
import re
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, g, abort, session, Response, jsonify
from parser import (
    parse_sportscode_xml, aggregate_match_stats, aggregate_zones, CATEGORY_SECTIONS,
    SECTION_ICONS, SECTION_HELP, CATEGORY_HELP, generate_highlights, compute_radar_metrics,
    compute_score, compute_phase_timing, compute_attack_sector, compute_defense_sector,
    compute_lineout_detail, compute_scrum_detail, compute_kicking_detail,
    compute_player_attack_table, compute_player_defense_table, compute_player_ruck_table,
    compute_overview_dashboard, compute_ruck_sector, compute_season_dashboard,
    compute_squad_preview, compute_squad_season_stats, SQUAD_ROSTER, SQUAD_POSITION_ORDER,
    is_jiff, compute_jiff_chart,
    compute_transition_sector, compute_player_comparison, compute_player_radar_svg,
    compute_back3_trend, TRAINING_TAXONOMY, compute_training_volume,
    group_training_sessions_by_period, compute_win_loss_analysis, compute_match_kpis,
   PHASE_ICONS, PHASE_HELP, compute_event_timing_multi, compute_match_baseline,
    compute_sector_baselines, compute_player_season_baselines, build_player_cards,
    attach_overview_highlights,
)
from parser_ubb import parse_ubb_xml, compute_ubb_overview
from prod2 import (
    parse_prod2_report, get_team_profile, get_classement_table, compute_player_groups,
    SECTOR_SHEETS, compute_team_trends, compute_most_used_players, build_compare_rows,
    compute_team_kpi_profile, compute_team_threats, compute_team_position_history,
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
# Base de données PostgreSQL persistante (Neon, ou toute autre base Postgres gratuite).
# À définir dans les variables d'environnement Render sous le nom DATABASE_URL.
# Exemple de valeur : postgresql://user:motdepasse@hote.neon.tech/rugby?sslmode=require
DATABASE_URL = os.environ.get("DATABASE_URL")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30MB max upload
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)  # reste connecté 30 jours
ASSET_VERSION = str(int(time.time()))  # change à chaque redémarrage : force le navigateur à
                                        # retélécharger le CSS/JS au lieu de garder une vieille
                                        # version en cache après un déploiement.
# Identité du club : seules ces 2 variables (+ le fichier static/logo.png) changent d'une
# copie du site à l'autre pour un autre club. CLUB_NAME = nom court utilisé dans le texte des
# pages ("Ce que {{ CLUB_NAME }} a fait..."), CLUB_FULL_NAME = nom complet affiché dans le
# logo/titre du site. Définis-les dans les variables d'environnement Render pour un nouveau
# client plutôt que de les changer dans le code.
CLUB_NAME = os.environ.get("CLUB_NAME", "Nice")
CLUB_FULL_NAME = os.environ.get("CLUB_FULL_NAME", "Nissa Rugby")
# Le module Adversaires/Pro D2 (scouting hebdo à partir du rapport Excel de la ligue) est
# spécifique à la Pro D2 française : à désactiver pour un club qui n'y a pas accès (mets
# ENABLE_PROD2=0 dans les variables d'environnement Render). Le quota JIFF (règle LNR,
# indépendante de la Pro D2 en tant que telle) se désactive séparément avec ENABLE_JIFF=0.
# Les deux restent activés par défaut pour ne rien changer au site actuel.
ENABLE_PROD2 = os.environ.get("ENABLE_PROD2", "1") != "0"
ENABLE_JIFF = os.environ.get("ENABLE_JIFF", "1") != "0"
PROD2_ENDPOINTS = {
    "opponents", "opponent_detail", "opponent_joueurs", "opponents_trends",
    "opponent_sector", "prod2_import", "next_match", "next_match_set",
}
JIFF_ENDPOINTS = {"season_jiff"}
# Identifiants du compte ADMIN (peut tout faire : importer/supprimer un match, modifier
# la composition, sauvegarder/restaurer...). Définis ADMIN_EMAIL / ADMIN_PASSWORD dans les
# variables d'environnement Render — sinon ces valeurs par défaut (à changer !) sont utilisées.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@nissarugby.fr")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
# Lien de démo public : quiconque a ce lien voit le site en lecture seule, sans compte,
# avec le mode démo (noms floutés) activé automatiquement. Change cette valeur si tu veux
# un lien différent, et ne le partage qu'avec des prospects (il donne accès en lecture à tout).
DEMO_TOKEN = os.environ.get("DEMO_TOKEN", "decouverte-club1")
# Comptes STAFF (accès lecture seule : navigue partout mais ne voit aucune action de
# modification) : autant de comptes que voulu, chacun avec SON PROPRE email/mot de passe.
# Sur Render, ajoute des variables d'environnement par paire numérotée :
#   STAFF_EMAIL_1 / STAFF_PASSWORD_1
#   STAFF_EMAIL_2 / STAFF_PASSWORD_2
#   STAFF_EMAIL_3 / STAFF_PASSWORD_3
#   ... (jusqu'à 20 comptes staff possibles)
# L'ancienne paire sans numéro (STAFF_EMAIL / STAFF_PASSWORD), si tu l'avais déjà
# configurée, continue aussi de fonctionner comme un compte staff de plus.
def _load_staff_accounts():
    accounts = []
    legacy_email = os.environ.get("STAFF_EMAIL")
    legacy_password = os.environ.get("STAFF_PASSWORD")
    if legacy_email and legacy_password:
        accounts.append((legacy_email.strip().lower(), legacy_password))
    for i in range(1, 21):
        email = os.environ.get(f"STAFF_EMAIL_{i}")
        password = os.environ.get(f"STAFF_PASSWORD_{i}")
        if email and password:
            accounts.append((email.strip().lower(), password))
    return accounts
STAFF_ACCOUNTS = _load_staff_accounts()
# Le site entier est privé : seule une personne connectée (admin OU staff) peut voir
# quoi que ce soit. Seules ces 2 routes restent accessibles sans connexion (sinon
# impossible d'atteindre la page de connexion elle-même).
PUBLIC_ENDPOINTS = {"login", "static", "demo_login", "pwa_manifest", "pwa_service_worker"}
@app.before_request
def require_login_everywhere():
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.full_path))
@app.before_request
def gate_optional_features():
    """Bloque proprement les pages Adversaires/Pro D2 et JIFF quand elles sont désactivées
    pour ce club (voir ENABLE_PROD2 / ENABLE_JIFF), plutôt que de les laisser planter sur
    l'absence de données qu'elles ne pourront jamais avoir."""
    if not ENABLE_PROD2 and request.endpoint in PROD2_ENDPOINTS:
        flash("Cette fonctionnalité n'est pas activée sur ce site.", "error")
        return redirect(url_for("landing"))
    if not ENABLE_JIFF and request.endpoint in JIFF_ENDPOINTS:
        flash("Cette fonctionnalité n'est pas activée sur ce site.", "error")
        return redirect(url_for("landing"))
    # Les espaces Documents / Calendrier / Cahier des charges contiennent des informations
    # internes au staff : ils sont totalement invisibles pour les visiteurs du lien de
    # démonstration.
    if session.get("demo_forced") and request.endpoint and (
        request.endpoint.startswith("documents")
        or request.endpoint.startswith("calendrier")
        or request.endpoint.startswith("cahier_charges")
    ):
        flash("Cet espace n'est pas accessible en mode démonstration.", "error")
        return redirect(url_for("landing"))
def admin_required(view):
    """Garde-fou pour les actions réservées à l'admin (import, suppression, validation
    composition, sauvegarde...). Le staff est bien connecté (passe le before_request
    global) mais n'a pas le droit d'exécuter ces actions."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Action réservée à l'administrateur.", "error")
            return redirect(url_for("landing"))
        return view(*args, **kwargs)
    return wrapped
@app.context_processor
def inject_logged_in():
    return {
        "logged_in": session.get("logged_in", False), "is_admin": session.get("is_admin", False),
        "demo_forced": session.get("demo_forced", False),
        "user_email": session.get("user_email", ""),
        "club_name": CLUB_NAME, "club_full_name": CLUB_FULL_NAME,
        "enable_prod2": ENABLE_PROD2, "enable_jiff": ENABLE_JIFF,
        "asset_version": ASSET_VERSION,
    }

# Contenu du service worker, généré en Python pour pouvoir y injecter ASSET_VERSION :
# le nom du cache change donc à chaque redémarrage/déploiement, ce qui vide
# automatiquement l'ancien cache (voir le handler "activate" ci-dessous) sans
# jamais servir une vieille version du CSS/JS aux téléphones qui ont installé
# l'appli.
SERVICE_WORKER_JS = """
const CACHE_NAME = "rugby-analytics-shell-%(v)s";
const APP_SHELL = [
  "/static/style.css",
  "/static/demo-mode.js",
  "/static/sortable.js",
  "/static/calendar.js",
  "/static/logo.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // jamais les POST (uploads, ajouts de docs/événements...)
  const url = new URL(req.url);

  // Fichiers de l'appli (CSS/JS/icônes) : servis depuis le cache pour un chargement
  // instantané, et rafraîchis en arrière-plan dès que le réseau répond.
  if (url.origin === self.location.origin && APP_SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req)
          .then((res) => {
            caches.open(CACHE_NAME).then((cache) => cache.put(req, res.clone()));
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Pages et données (matchs, documents, calendrier...) : toujours le réseau en
  // priorité, pour ne jamais afficher des stats périmées. Le cache ne sert que
  // de filet de sécurité si le téléphone perd la connexion.
  event.respondWith(fetch(req).catch(() => caches.match(req)));
});
""" % {"v": ASSET_VERSION}

# ---------------------------------------------------------------------------
# PWA — rend le site installable sur l'écran d'accueil (iPhone/Android), sans
# passer par l'App Store/Play Store. Manifest généré dynamiquement (pas un
# fichier static) pour reprendre le nom du club configuré par CLUB_NAME /
# CLUB_FULL_NAME. Les deux routes doivent rester accessibles sans connexion
# (voir PUBLIC_ENDPOINTS) : le navigateur les demande dès la page de login,
# avant que la personne ne soit authentifiée.
# ---------------------------------------------------------------------------
@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": f"{CLUB_FULL_NAME} — Rugby Analytics",
        "short_name": CLUB_NAME or "Rugby Analytics",
        "description": "Analyse vidéo, documents, calendrier et suivi du staff, au même endroit.",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#10181f",
        "theme_color": "#530d34",
        "icons": [
            {"src": url_for("static", filename="icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    # mimetype précis (et pas juste application/json) : certains navigateurs sont
    # stricts sur ce point pour proposer l'installation de l'appli.
    return Response(json.dumps(manifest, ensure_ascii=False), mimetype="application/manifest+json")

@app.route("/sw.js")
def pwa_service_worker():
    resp = Response(SERVICE_WORKER_JS, mimetype="application/javascript")
    # Le fichier de service worker ne doit jamais rester en cache navigateur :
    # sinon une mise à jour du site ne serait jamais détectée par les téléphones
    # qui ont déjà installé l'appli.
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/demo/<token>")
def demo_login(token):
    if token != DEMO_TOKEN:
        flash("Lien de démonstration invalide.", "error")
        return redirect(url_for("login"))
    session.permanent = True
    session["logged_in"] = True
    session["is_admin"] = False
    session["demo_forced"] = True
    session["user_email"] = "démo"
    return redirect(url_for("landing", demo="1"))
@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next") or url_for("landing")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_target = request.form.get("next") or url_for("landing")
        if email == ADMIN_EMAIL.lower() and password == ADMIN_PASSWORD:
            session.permanent = True
            session["logged_in"] = True
            session["is_admin"] = True
            session["demo_forced"] = False
            session["user_email"] = email
            flash("Connecté.", "success")
            return redirect(next_target)
        for staff_email, staff_password in STAFF_ACCOUNTS:
            if email == staff_email and password == staff_password:
                session.permanent = True
                session["logged_in"] = True
                session["is_admin"] = False
                session["demo_forced"] = False
                session["user_email"] = email
                flash("Connecté.", "success")
                return redirect(next_target)
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("login.html", next_url=next_url)
@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    session.pop("is_admin", None)
    session.pop("demo_forced", None)
    session.pop("user_email", None)
    flash("Déconnecté.", "success")
    return redirect(url_for("login"))
class DB:
    """Petit adaptateur autour de psycopg2 pour garder l'écriture
    db.execute(sql, params).fetchall() / .fetchone() utilisée partout dans ce fichier,
    exactement comme avec sqlite3 avant. Les lignes se comportent comme des dictionnaires
    (row["colonne"]) grâce à RealDictCursor."""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        return cur
    def commit(self):
        self._conn.commit()
    def close(self):
        self._conn.close()
def _connect():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL n'est pas configurée. Ajoute-la dans les variables "
            "d'environnement Render (elle vient de ta base Neon)."
        )
    return psycopg2.connect(DATABASE_URL)
def get_db():
    if "db" not in g:
        g.db = DB(_connect())
    return g.db
@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = _connect()
    db = DB(conn)
    db.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY,
            created_at TEXT NOT NULL,
            match_date TEXT,
            own_team TEXT,
            opponent TEXT NOT NULL,
            competition TEXT,
            venue TEXT,
            own_team_tag TEXT,
            filename TEXT,
            total_instances INTEGER,
            stats_json TEXT,
            players_json TEXT,
            zones_json TEXT,
            instances_json TEXT
        )
    """)
    # Ajout de la colonne si la base existe déjà sans (anciennes versions du site)
    cur = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'matches'"
    )
    cols = [r["column_name"] for r in cur.fetchall()]
    if "instances_json" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN instances_json TEXT")
    if "manual_stats_json" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN manual_stats_json TEXT")
    if "composition_json" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN composition_json TEXT")
    if "player_match_stats_json" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN player_match_stats_json TEXT")
    if "ubb_overview_json" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN ubb_overview_json TEXT")
    if "score_own" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN score_own INTEGER")
    if "score_opp" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN score_opp INTEGER")
    if "score_ht" not in cols:
        db.execute("ALTER TABLE matches ADD COLUMN score_ht TEXT")
    db.execute("""
        CREATE TABLE IF NOT EXISTS training_sessions (
            id SERIAL PRIMARY KEY,
            created_at TEXT NOT NULL,
            session_date TEXT,
            items_json TEXT
        )
    """)
    # Rapport hebdomadaire Pro D2 (fichier Excel) : une seule ligne à la fois, chaque
    # nouvel import remplace le précédent (pas d'historique semaine par semaine).
    db.execute("""
        CREATE TABLE IF NOT EXISTS prod2_reports (
            id SERIAL PRIMARY KEY,
            uploaded_at TEXT NOT NULL,
            filename TEXT,
            data_json TEXT
        )
    """)
    # Prochain adversaire sélectionné pour la page "Prochain match" : une seule ligne à la
    # fois (comme prod2_reports), remplacée à chaque changement de sélection.
    db.execute("""
        CREATE TABLE IF NOT EXISTS next_opponent (
            id SERIAL PRIMARY KEY,
            team TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Espace Documents du staff : dossiers libres + fichiers stockés DANS la base
    # PostgreSQL (colonne BYTEA) pour survivre aux redéploiements Render (le disque
    # du plan gratuit n'est pas persistant). Les vidéos lourdes passent par des
    # liens (kind='link') plutôt que des fichiers.
    db.execute("""
        CREATE TABLE IF NOT EXISTS doc_folders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id INTEGER REFERENCES doc_folders(id) ON DELETE CASCADE,
            created_by TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER REFERENCES doc_folders(id) ON DELETE CASCADE,
            kind TEXT NOT NULL DEFAULT 'file',
            filename TEXT,
            mimetype TEXT,
            size_bytes INTEGER,
            data BYTEA,
            url TEXT,
            title TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL
        )
    """)
    # Calendrier du staff : événements divers (réunions, déplacements, rendez-vous...),
    # distincts des matchs/entraînements qui restent gérés ailleurs sur le site.
    db.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # Cahier des charges : suivi de tâches/exigences du staff, à 3 statuts.
    db.execute("""
        CREATE TABLE IF NOT EXISTS charges_items (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'a_faire',
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    db.commit()
    db.close()
@app.route("/")
def landing():
    return render_template("landing.html")
@app.route("/matchs")
def index():
    db = get_db()
    rows = db.execute("SELECT * FROM matches ORDER BY COALESCE(match_date, created_at) DESC, id DESC").fetchall()
    opponents = sorted({r["opponent"] for r in rows})
    competitions = sorted({r["competition"] for r in rows if r["competition"]})
    return render_template("index.html", matches=rows, opponents=opponents, competitions=competitions)
@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "GET":
        return render_template("upload.html")
    file = request.files.get("xml_file")
    opponent = request.form.get("opponent", "").strip()
    if not file or file.filename == "":
        flash("Merci de sélectionner un fichier XML Sportscode.", "error")
        return redirect(url_for("upload"))
    if not opponent:
        flash("Merci d'indiquer le nom de l'adversaire.", "error")
        return redirect(url_for("upload"))
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(save_path)
    try:
        parsed = parse_sportscode_xml(save_path)
    except Exception as exc:
        flash(f"Erreur lors de la lecture du fichier XML : {exc}", "error")
        return redirect(url_for("upload"))
    stats, players = aggregate_match_stats(parsed["instances"])
    zones = aggregate_zones(parsed["instances"])
    db = get_db()
    cur = db.execute(
        """INSERT INTO matches
           (created_at, match_date, own_team, opponent, competition, venue, own_team_tag,
            filename, total_instances, stats_json, players_json, zones_json, instances_json)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            datetime.utcnow().isoformat(),
            request.form.get("match_date") or None,
            request.form.get("own_team") or parsed["own_team_tag"],
            opponent,
            request.form.get("competition") or None,
            request.form.get("venue") or None,
            parsed["own_team_tag"],
            file.filename,
            len(parsed["instances"]),
            json.dumps(stats),
            json.dumps(players),
            json.dumps(zones),
            json.dumps(parsed["instances"]),
        ),
    )
    match_id = cur.fetchone()["id"]
    db.commit()
    flash("Match importé avec succès.", "success")
    return redirect(url_for("match_detail", match_id=match_id))
@app.route("/upload-ubb", methods=["GET", "POST"])
@admin_required
def upload_ubb():
    """Import d'un match UBB : un seul fichier XML "Équipe - Action" (convention
    différente de celle de Nice, voir parser_ubb.py). Le score se saisit à la main,
    il n'est pas reconstruit depuis le fichier."""
    if request.method == "GET":
        return render_template("upload_ubb.html")
    file = request.files.get("xml_file")
    opponent = request.form.get("opponent", "").strip()
    if not file or file.filename == "":
        flash("Merci de sélectionner un fichier XML Sportscode.", "error")
        return redirect(url_for("upload_ubb"))
    if not opponent:
        flash("Merci d'indiquer le nom exact de l'adversaire tel qu'il apparaît dans le fichier.", "error")
        return redirect(url_for("upload_ubb"))
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(save_path)
    try:
        instances = parse_ubb_xml(save_path)
        overview = compute_ubb_overview(instances, own_team="Union Bordeaux Begles", opp_team=opponent)
    except Exception as exc:
        flash(f"Erreur lors de la lecture du fichier XML : {exc}", "error")
        return redirect(url_for("upload_ubb"))

    def _to_int(raw):
        raw = (raw or "").strip()
        if raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    db = get_db()
    cur = db.execute(
        """INSERT INTO matches
           (created_at, match_date, own_team, opponent, competition, venue, own_team_tag,
            filename, total_instances, ubb_overview_json, score_own, score_opp, score_ht)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            datetime.utcnow().isoformat(),
            request.form.get("match_date") or None,
            CLUB_FULL_NAME,
            opponent,
            request.form.get("competition") or None,
            request.form.get("venue") or None,
            "Union Bordeaux Begles",
            file.filename,
            len(instances),
            json.dumps(overview),
            _to_int(request.form.get("score_own")),
            _to_int(request.form.get("score_opp")),
            request.form.get("score_ht") or None,
        ),
    )
    match_id = cur.fetchone()["id"]
    db.commit()
    flash("Match UBB importé avec succès.", "success")
    return redirect(url_for("match_detail", match_id=match_id))
def _row_to_match(row):
    m = dict(row)
    m["stats"] = json.loads(m.pop("stats_json") or "{}")
    m["players"] = json.loads(m.pop("players_json") or "{}")
    m["zones"] = json.loads(m.pop("zones_json") or "{}")
    m["instances"] = json.loads(m.pop("instances_json") or "[]")
    m["manual_stats"] = json.loads(m.pop("manual_stats_json", None) or "{}")
    m["composition"] = json.loads(m.pop("composition_json", None) or "[]")
    m["player_match_stats"] = json.loads(m.pop("player_match_stats_json", None) or "{}")
    m["ubb_overview"] = json.loads(m.pop("ubb_overview_json", None) or "{}")
    return m
def _get_match_or_404(match_id):
    db = get_db()
    row = db.execute("SELECT * FROM matches WHERE id = %s", (match_id,)).fetchone()
    if row is None:
        abort(404)
    return _row_to_match(row)
def _no_instances_guard(match):
    """Les matchs importés avant la mise à jour 'secteurs' n'ont pas d'instances brutes stockées."""
    return not match["instances"]
@app.route("/match/<int:match_id>")
def match_detail(match_id):
    match = _get_match_or_404(match_id)
    if match.get("ubb_overview"):
        return render_template("match_ubb.html", match=match, ov=match["ubb_overview"])
    sections = []
    for section_name, cats in CATEGORY_SECTIONS.items():
        section_rows = []
        for cat in cats:
            data = match["stats"].get(cat)
            if not data:
                continue
            own = data.get("own", {"count": 0})
            adv = data.get("adverse", {"count": 0})
            neutral = data.get("neutral", {"count": 0})
            if own.get("count", 0) == 0 and adv.get("count", 0) == 0 and neutral.get("count", 0) == 0:
                continue
            help_text = CATEGORY_HELP.get(cat, "")
            section_rows.append({"category": cat, "own": own, "adverse": adv, "neutral": neutral, "help": help_text})
        if section_rows:
            sections.append({
                "name": section_name,
                "icon": SECTION_ICONS.get(section_name, ""),
                "help": SECTION_HELP.get(section_name, ""),
                "rows": section_rows,
            })
    top_players = sorted(match["players"].items(), key=lambda x: -x[1]["count"])[:15]
    highlights = generate_highlights(match["stats"], match["own_team"], match["opponent"])
    radar = compute_radar_metrics(match["stats"])
    score = None
    phase_timing = None
    dashboard = None
    baseline = None
    if match["instances"]:
        score = compute_score(match["instances"])
        phase_timing = compute_phase_timing(match["instances"])
        dashboard = compute_overview_dashboard(match["instances"], score)
        matches_with_instances, _, _, _ = _season_context()
        baseline = compute_match_baseline(matches_with_instances, exclude_id=match_id)
    return render_template(
        "match.html", match=match, sections=sections, top_players=top_players,
        highlights=highlights, radar=radar, score=score, phase_timing=phase_timing,
        phase_icons=PHASE_ICONS, phase_help=PHASE_HELP, dashboard=dashboard,
        baseline=baseline,
        has_instances=not _no_instances_guard(match),
    )
@app.route("/match/<int:match_id>/attaque")
def match_attaque(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    attack = compute_attack_sector(match["instances"], "own")
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_attaque.html", match=match, data=attack, phase_icons=PHASE_ICONS,
                           baseline=(baselines or {}).get("attaque"))
@app.route("/match/<int:match_id>/defense")
def match_defense(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    attack_adv = compute_attack_sector(match["instances"], "adverse")
    defense = compute_defense_sector(match["instances"], "adverse")
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_defense.html", match=match, data=attack_adv, defense=defense, phase_icons=PHASE_ICONS,
                           baseline=(baselines or {}).get("defense"))
@app.route("/match/<int:match_id>/ruck")
def match_ruck(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    ruck = compute_ruck_sector(match["instances"])
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_ruck.html", match=match, data=ruck, phase_icons=PHASE_ICONS,
                           baseline=(baselines or {}).get("ruck"))
@app.route("/match/<int:match_id>/touches")
def match_touches(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    lineout = compute_lineout_detail(match["instances"])
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_touches.html", match=match, data=lineout,
                           baseline=(baselines or {}).get("touches"))
@app.route("/match/<int:match_id>/melee")
def match_melee(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    scrum = compute_scrum_detail(match["instances"])
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_melee.html", match=match, data=scrum, phase_icons=PHASE_ICONS,
                           baseline=(baselines or {}).get("melee"))
@app.route("/match/<int:match_id>/jap")
def match_jap(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    kicking = compute_kicking_detail(match["instances"])
    matches_with_instances, _, _, _ = _season_context()
    baselines = compute_sector_baselines(matches_with_instances, exclude_id=match_id)
    return render_template("match_jap.html", match=match, data=kicking,
                           baseline=(baselines or {}).get("jap"))
@app.route("/match/<int:match_id>/jap/manual", methods=["POST"])
@admin_required
def match_jap_manual(match_id):
    match = _get_match_or_404(match_id)
    manual = match.get("manual_stats") or {}
    def _to_int(raw):
        raw = (raw or "").strip()
        if raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    manual["kick_distance_own"] = _to_int(request.form.get("kick_distance_own"))
    manual["kick_distance_adverse"] = _to_int(request.form.get("kick_distance_adverse"))
    db = get_db()
    db.execute("UPDATE matches SET manual_stats_json = %s WHERE id = %s", (json.dumps(manual), match_id))
    db.commit()
    flash("Données jeu au pied mises à jour.", "success")
    return redirect(url_for("match_jap", match_id=match_id))
@app.route("/match/<int:match_id>/joueurs")
def match_joueurs(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    attack_table = compute_player_attack_table(match["instances"])
    defense_table = compute_player_defense_table(match["instances"])
    ruck_table = compute_player_ruck_table(match["instances"])
    matches_with_instances, _, _, _ = _season_context()
    player_baselines = compute_player_season_baselines(matches_with_instances, exclude_id=match_id)
    player_cards = attach_overview_highlights(build_player_cards(
        attack_table, defense_table, ruck_table, composition=match.get("composition")))
    return render_template("match_joueurs.html", match=match, attack_table=attack_table,
                           defense_table=defense_table, ruck_table=ruck_table,
                           player_baselines=player_baselines, player_cards=player_cards)
@app.route("/match/<int:match_id>/composition", methods=["GET", "POST"])
def match_composition(match_id):
    match = _get_match_or_404(match_id)
    if request.method == "POST":
        if not session.get("is_admin"):
            flash("Connecte-toi pour valider la composition.", "error")
            return redirect(url_for("login", next=request.path))
        selected = request.form.getlist("player")
        selected = (selected + [""] * 23)[:23]  # toujours 23 emplacements (n°1 à n°23)
        minutes_in = (request.form.getlist("minutes") + [""] * 23)[:23]
        yellow_in = (request.form.getlist("yellow") + [""] * 23)[:23]
        red_in = (request.form.getlist("red") + [""] * 23)[:23]
        def _to_int(v):
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                return 0
        player_stats = {}
        for i, name in enumerate(selected):
            if not name:
                continue
            player_stats[name] = {
                "minutes": _to_int(minutes_in[i]),
                "yellow": _to_int(yellow_in[i]),
                "red": _to_int(red_in[i]),
            }
        db = get_db()
        db.execute(
            "UPDATE matches SET composition_json = %s, player_match_stats_json = %s WHERE id = %s",
            (json.dumps(selected), json.dumps(player_stats), match_id),
        )
        db.commit()
        flash("Composition enregistrée.", "success")
        return redirect(url_for("match_composition", match_id=match_id))
    slots = (match.get("composition") or [])[:23]
    slots = slots + [""] * (23 - len(slots))
    all_players = [
        {"position": position, "players": [{"name": p, "jiff": is_jiff(p)} for p in SQUAD_ROSTER[position]]}
        for position in SQUAD_POSITION_ORDER
    ]
    filled_count = sum(1 for p in slots if p)
    player_stats = match.get("player_match_stats") or {}
    return render_template("match_composition.html", match=match, all_players=all_players,
                           slots=slots, filled_count=filled_count, player_stats=player_stats)
@app.route("/match/<int:match_id>/transition")
def match_transition(match_id):
    match = _get_match_or_404(match_id)
    if _no_instances_guard(match):
        flash("Ce match a été importé avant la mise à jour détaillée par secteur : réimporte le fichier XML pour voir cette page.", "error")
        return redirect(url_for("match_detail", match_id=match_id))
    transition = compute_transition_sector(match["instances"])
    return render_template("match_transition.html", match=match, data=transition)
@app.route("/admin/export")
@admin_required
def export_data():
    """Télécharge une sauvegarde complète (JSON) de tous les matchs enregistrés.
    À utiliser avant toute mise à jour du site pour ne jamais perdre de données."""
    db = get_db()
    rows = db.execute("SELECT * FROM matches ORDER BY id").fetchall()
    data = [dict(r) for r in rows]
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=rugby_analytics_sauvegarde_{ts}.json"},
    )
@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def import_data():
    """Recharge des matchs depuis un fichier de sauvegarde JSON (généré par 'Sauvegarder
    les données'). Sert à restaurer les données juste après un changement de base de
    données, ou si besoin de récupérer une ancienne sauvegarde."""
    if request.method == "GET":
        return render_template("admin_import.html")
    file = request.files.get("backup_file")
    if not file or file.filename == "":
        flash("Merci de sélectionner un fichier de sauvegarde JSON.", "error")
        return redirect(url_for("import_data"))
    try:
        data = json.loads(file.read().decode("utf-8"))
    except Exception as exc:
        flash(f"Fichier de sauvegarde invalide : {exc}", "error")
        return redirect(url_for("import_data"))
    db = get_db()
    imported = 0
    for m in data:
        db.execute(
            """INSERT INTO matches
               (created_at, match_date, own_team, opponent, competition, venue, own_team_tag,
                filename, total_instances, stats_json, players_json, zones_json, instances_json,
                manual_stats_json, composition_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                m.get("created_at"), m.get("match_date"), m.get("own_team"), m.get("opponent"),
                m.get("competition"), m.get("venue"), m.get("own_team_tag"), m.get("filename"),
                m.get("total_instances"), m.get("stats_json"), m.get("players_json"),
                m.get("zones_json"), m.get("instances_json"), m.get("manual_stats_json"),
                m.get("composition_json"),
            ),
        )
        imported += 1
    db.commit()
    flash(f"{imported} match(s) importé(s) depuis la sauvegarde.", "success")
    return redirect(url_for("index"))
@app.route("/match/<int:match_id>/delete", methods=["POST"])
@admin_required
def delete_match(match_id):
    db = get_db()
    db.execute("DELETE FROM matches WHERE id = %s", (match_id,))
    db.commit()
    flash("Match supprimé.", "success")
    return redirect(url_for("index"))
SECTOR_PAGE_META = {
    "attaque": {"title": "Attaque", "icon": "⚔️"},
    "defense": {"title": "Défense", "icon": "🛡️"},
    "discipline": {"title": "Discipline", "icon": "🟨"},
    "touches": {"title": "Touches", "icon": "🤾"},
    "melee": {"title": "Mêlée", "icon": "🔒"},
    "rucks": {"title": "Rucks", "icon": "🤝"},
    "jap": {"title": "Jeu au pied", "icon": "🦶"},
}
def _extract_journee(filename):
    """Numéro de journée extrait du nom du fichier Pro D2 (ex: '..._Journée 1_Finale.xlsx'
    -> 1), pour afficher la fraîcheur des données sans avoir à le ressaisir à la main."""
    if not filename:
        return None
    match = re.search(r"[Jj]ourn[ée]e\s*(\d+)", filename)
    return int(match.group(1)) if match else None
def _load_latest_prod2_row():
    db = get_db()
    return db.execute(
        "SELECT uploaded_at, filename, data_json FROM prod2_reports ORDER BY id DESC LIMIT 1"
    ).fetchone()
def _load_prod2_report():
    """Rapport Pro D2 le plus récent. Chaque import est conservé en base (voir
    _load_prod2_history) pour pouvoir suivre l'évolution d'une équipe semaine après
    semaine, mais toutes les pages "état actuel" du site n'affichent toujours que celui-ci.
    Renvoie None si aucun rapport n'a encore été importé."""
    row = _load_latest_prod2_row()
    if not row:
        return None
    return json.loads(row["data_json"])
def _load_prod2_meta():
    """Date d'import + numéro de journée du dernier rapport, pour afficher la fraîcheur des
    données sur les pages Adversaires (ex: 'Journée 3, importé le 14/07/2026 à 11h56')."""
    row = _load_latest_prod2_row()
    if not row:
        return None
    return {
        "uploaded_at": row["uploaded_at"],
        "filename": row["filename"],
        "journee": _extract_journee(row["filename"]),
    }
def _report_label(report_meta):
    if not report_meta:
        return "aucun rapport importé"
    parts = []
    if report_meta.get("journee"):
        parts.append(f"Journée {report_meta['journee']}")
    if report_meta.get("uploaded_at"):
        try:
            dt = datetime.fromisoformat(report_meta["uploaded_at"])
            parts.append(f"importé le {dt.strftime('%d/%m/%Y à %Hh%M')}")
        except ValueError:
            pass
    return ", ".join(parts) if parts else "dernier rapport importé"
def _load_prod2_history():
    """Tous les rapports Pro D2 importés depuis l'activation de l'historique, du plus
    ancien au plus récent, sous la forme attendue par compute_team_position_history."""
    db = get_db()
    rows = db.execute(
        "SELECT uploaded_at, filename, data_json FROM prod2_reports ORDER BY uploaded_at ASC"
    ).fetchall()
    history = []
    for r in rows:
        journee = _extract_journee(r["filename"])
        label = f"J{journee}" if journee else (r["uploaded_at"] or "")[:10]
        history.append({"label": label, "data": json.loads(r["data_json"])})
    return history
# Comparaison "Nissa vs adversaire" sur la fiche Vue d'ensemble : uniquement des pourcentages
# (jamais des totaux bruts), pour ne jamais mélanger un cumul sur nos matchs codés avec un
# cumul sur toute la saison Pro D2 de l'adversaire — deux échelles différentes qui rendraient
# une comparaison de totaux absurde. Même en pourcentage, nos stats viennent de notre propre
# codage vidéo (Sportscode) et celles de l'adversaire du prestataire officiel Pro D2 : les
# méthodologies de calcul peuvent différer légèrement, d'où l'avertissement affiché avec.
NISSA_VS_OPPONENT_METRICS = [
    ("Possession (%)", "possession", "Possession", "% Possession"),
    ("Plaquages réussis (%)", "tackle_pct", "Défense", "Plaquages réussis %"),
    ("Touches gagnées (%)", "lineout_pct", "Touches", "Touches gagnées %"),
    ("Mêlées gagnées (%)", "scrum_pct", "Mêlées", "Mêlées gagnées %"),
    ("Duels aériens gagnés (%)", "duels_aeriens_pct", "Duels aériens", "Duels aériens gagnés %"),
]
def _compute_nissa_vs_opponent(profile):
    """Compare nos propres stats de saison (calculées sur tous nos matchs codés) à celles de
    l'adversaire dans le rapport Pro D2, limité aux indicateurs en pourcentage pour rester
    comparable malgré les échelles différentes. Renvoie None si on n'a pas encore de match
    codé cette saison."""
    matches_with_instances, selected, selected_ids, qs = _season_context()
    if not selected:
        return None
    instances = _season_instances(selected)
    our_kpis = compute_match_kpis(instances)
    poss_own = poss_adv = 0
    for m in selected:
        poss = m["stats"].get("Possession", {})
        poss_own += poss.get("own", {}).get("duration", 0)
        poss_adv += poss.get("adverse", {}).get("duration", 0)
    poss_total = poss_own + poss_adv
    our_kpis["possession"] = round(poss_own / poss_total * 100, 1) if poss_total else None
    rows = []
    for label, our_key, sheet, column in NISSA_VS_OPPONENT_METRICS:
        our_value = our_kpis.get(our_key)
        their_value = profile["categories"].get(sheet, {}).get(column)
        if our_value is None and their_value is None:
            continue
        rows.append({"label": label, "own": our_value, "opponent": their_value})
    return rows
def _team_profile_or_404(team, report=None):
    if report is None:
        report = _load_prod2_report()
    if not report:
        abort(404)
    profile = get_team_profile(report, team)
    if profile["classement"] is None:
        abort(404)
    return profile
def _sector_sheets(profile, sector_key):
    sheets = []
    for sheet_name in SECTOR_SHEETS[sector_key]:
        team_row = profile["categories"].get(sheet_name, {})
        avg_row = profile["league_avg"].get(sheet_name, {})
        sheets.append({
            "name": sheet_name,
            "team_row": team_row,
            "avg_row": avg_row,
            "rows": build_compare_rows(sheet_name, team_row, avg_row),
        })
    return sheets
@app.route("/opponents")
def opponents():
    report = _load_prod2_report()
    report_meta = _load_prod2_meta()
    classement = get_classement_table(report) if report else []
    return render_template(
        "opponents.html", classement=classement, has_report=report is not None,
        report_label=_report_label(report_meta),
    )
@app.route("/opponents/<team>")
def opponent_detail(team):
    report = _load_prod2_report()
    if not report:
        abort(404)
    profile = _team_profile_or_404(team, report=report)
    possession = profile["categories"].get("Possession", {})
    possession_avg = profile["league_avg"].get("Possession", {})
    possession_rows = build_compare_rows("Possession", possession, possession_avg)
    team_trends = compute_team_trends(report).get(team, {"strengths": [], "weaknesses": []})
    kpi_profile = compute_team_kpi_profile(report, team)
    report_meta = _load_prod2_meta()
    history = compute_team_position_history(_load_prod2_history(), team)
    nissa_compare = _compute_nissa_vs_opponent(profile)
    return render_template(
        "opponent.html", team=team, profile=profile,
        possession=possession, possession_avg=possession_avg, possession_rows=possession_rows,
        team_trends=team_trends, kpi_profile=kpi_profile, report_label=_report_label(report_meta),
        history=history, nissa_compare=nissa_compare,
        team_mode=True, active="overview",
    )
@app.route("/opponents/<team>/joueurs")
def opponent_joueurs(team):
    profile = _team_profile_or_404(team)
    groups = compute_player_groups(profile["players"])
    most_used = compute_most_used_players(profile["players"])
    threats = compute_team_threats(profile["players"])
    report_meta = _load_prod2_meta()
    return render_template(
        "opponent_joueurs.html", team=team, profile=profile, groups=groups, most_used=most_used,
        threats=threats, report_label=_report_label(report_meta),
        team_mode=True, active="joueurs",
    )
@app.route("/opponents/tendances")
def opponents_trends():
    report = _load_prod2_report()
    if not report:
        flash("Importe d'abord le rapport Pro D2 pour voir les tendances.", "error")
        return redirect(url_for("opponents"))
    trends = compute_team_trends(report)
    report_meta = _load_prod2_meta()
    return render_template(
        "opponent_trends.html", teams=report["team_names"], trends=trends,
        report_label=_report_label(report_meta),
    )
@app.route("/opponents/<team>/<sector>")
def opponent_sector(team, sector):
    if sector not in SECTOR_SHEETS:
        abort(404)
    profile = _team_profile_or_404(team)
    sheets = _sector_sheets(profile, sector)
    meta = SECTOR_PAGE_META[sector]
    report_meta = _load_prod2_meta()
    return render_template(
        "opponent_sector.html", team=team, profile=profile, sheets=sheets,
        team_mode=True, active=sector, page_title=meta["title"], page_icon=meta["icon"],
        report_label=_report_label(report_meta),
    )
def _head_to_head(matches_with_instances, team):
    """Historique de nos matchs déjà codés face à cet adversaire précis (comparaison de
    noms insensible à la casse), du plus ancien au plus récent, pour la page Prochain match."""
    team_norm = (team or "").strip().lower()
    rows = []
    wins = draws = losses = 0
    for m in matches_with_instances:
        if (m.get("opponent") or "").strip().lower() != team_norm:
            continue
        sc = compute_score(m["instances"])
        if sc["own"] > sc["adverse"]:
            result = "V"
            wins += 1
        elif sc["own"] < sc["adverse"]:
            result = "D"
            losses += 1
        else:
            result = "N"
            draws += 1
        rows.append({
            "match_id": m["id"], "date": m.get("match_date"), "competition": m.get("competition"),
            "own_score": sc["own"], "adverse_score": sc["adverse"], "result": result,
        })
    return {"rows": rows, "wins": wins, "draws": draws, "losses": losses, "total": len(rows)}
def _load_next_opponent():
    db = get_db()
    row = db.execute("SELECT team FROM next_opponent ORDER BY id DESC LIMIT 1").fetchone()
    return row["team"] if row else None
@app.route("/prochain-match")
def next_match():
    """Page de préparation de la semaine : notre forme récente (5 derniers matchs codés) +
    scouting du prochain adversaire (repris des pages Adversaires) + historique face à cette
    équipe, réunis au même endroit plutôt que de naviguer entre Bilan de saison et
    Adversaires séparément avant une réunion de préparation."""
    report = _load_prod2_report()
    report_meta = _load_prod2_meta()
    team_names = report["team_names"] if report else []
    selected_team = _load_next_opponent()
    profile = None
    possession_rows = None
    team_trends = None
    kpi_profile = None
    threats = None
    stale_team = False
    if report and selected_team:
        profile = get_team_profile(report, selected_team)
        if profile["classement"] is None:
            profile = None
            stale_team = True
        else:
            possession = profile["categories"].get("Possession", {})
            possession_avg = profile["league_avg"].get("Possession", {})
            possession_rows = build_compare_rows("Possession", possession, possession_avg)
            team_trends = compute_team_trends(report).get(selected_team, {"strengths": [], "weaknesses": []})
            kpi_profile = compute_team_kpi_profile(report, selected_team)
            threats = compute_team_threats(profile["players"])
    matches_with_instances, _, _, _ = _season_context()
    recent = matches_with_instances[-5:]
    recent_dashboard = compute_season_dashboard(recent) if recent else None
    recent_stats = aggregate_match_stats(_season_instances(recent))[0] if recent else {}
    head_to_head = _head_to_head(matches_with_instances, selected_team) if selected_team else None
    return render_template(
        "next_match.html", team_names=team_names, selected_team=selected_team,
        has_report=report is not None, stale_team=stale_team,
        profile=profile, possession_rows=possession_rows, team_trends=team_trends,
        kpi_profile=kpi_profile, threats=threats, report_label=_report_label(report_meta),
        recent_dashboard=recent_dashboard, recent_stats=recent_stats, recent_count=len(recent),
        head_to_head=head_to_head,
    )
@app.route("/prochain-match/set", methods=["POST"])
@admin_required
def next_match_set():
    team = (request.form.get("team") or "").strip()
    db = get_db()
    db.execute("DELETE FROM next_opponent")
    if team:
        db.execute(
            "INSERT INTO next_opponent (team, updated_at) VALUES (%s, %s)",
            (team, datetime.utcnow().isoformat()),
        )
    db.commit()
    flash("Prochain adversaire mis à jour." if team else "Prochain adversaire effacé.", "success")
    return redirect(url_for("next_match"))
@app.route("/admin/prod2/import", methods=["GET", "POST"])
@admin_required
def prod2_import():
    """Import du rapport hebdomadaire Pro D2 (fichier Excel). Le rapport le plus récent est
    toujours celui affiché partout sur le site, mais chaque import est conservé en base (au
    lieu d'écraser le précédent) pour pouvoir suivre l'évolution d'une équipe au fil des
    semaines (voir compute_team_position_history)."""
    if request.method == "GET":
        return render_template("admin_prod2_import.html")
    file = request.files.get("xlsx_file")
    if not file or file.filename == "":
        flash("Merci de sélectionner un fichier Excel Pro D2.", "error")
        return redirect(url_for("prod2_import"))
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_name = f"{ts}_{file.filename}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(save_path)
    try:
        report = parse_prod2_report(save_path)
    except Exception as exc:
        flash(f"Erreur lors de la lecture du fichier Excel : {exc}", "error")
        return redirect(url_for("prod2_import"))
    if not report["team_names"]:
        flash("Aucune équipe trouvée dans ce fichier — vérifie qu'il s'agit bien du bon export Pro D2.", "error")
        return redirect(url_for("prod2_import"))
    db = get_db()
    db.execute(
        "INSERT INTO prod2_reports (uploaded_at, filename, data_json) VALUES (%s, %s, %s)",
        (datetime.utcnow().isoformat(), file.filename, json.dumps(report)),
    )
    db.commit()
    flash(f"Rapport Pro D2 importé : {len(report['team_names'])} équipes.", "success")
    return redirect(url_for("opponents"))
def _season_context():
    """Matchs sélectionnés pour le cumul saison (case à cocher sur /season, conservée via
    ?m=id&m=id... sur toutes les pages secteur saison) + la query string à réutiliser dans
    les liens du sous-menu pour ne pas perdre la sélection en changeant d'onglet."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM matches ORDER BY COALESCE(match_date, created_at)"
    ).fetchall()
    all_matches = [_row_to_match(r) for r in rows]
    matches_with_instances = [m for m in all_matches if m["instances"]]
    selected_ids_param = request.args.getlist("m")
    if selected_ids_param:
        selected_ids = {int(x) for x in selected_ids_param if x.isdigit()}
        selected = [m for m in matches_with_instances if m["id"] in selected_ids]
    else:
        selected_ids = {m["id"] for m in matches_with_instances}
        selected = matches_with_instances
    qs = "&".join(f"m={i}" for i in sorted(selected_ids))
    return matches_with_instances, selected, selected_ids, qs
def _season_instances(selected):
    combined = []
    for m in selected:
        combined.extend(m["instances"])
    return combined
def _sum_manual(selected, key):
    vals = [m["manual_stats"].get(key) for m in selected if m.get("manual_stats", {}).get(key) is not None]
    return sum(vals) if vals else None
@app.route("/season")
def season():
    matches_with_instances, selected, selected_ids, qs = _season_context()
    dashboard = compute_season_dashboard(selected) if selected else None
    stats = aggregate_match_stats([i for m in selected for i in m["instances"]])[0] if selected else {}
    return render_template(
        "season.html",
        all_matches=matches_with_instances,
        selected_ids=selected_ids,
        selected_count=len(selected),
        total_count=len(matches_with_instances),
        dashboard=dashboard,
        stats=stats,
        season_mode=True, active="overview", qs=qs,
    )
@app.route("/season/attaque")
def season_attaque():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    attack = compute_attack_sector(instances, "own") if instances else None
    if attack:
        attack["try_timing"] = compute_event_timing_multi(selected, "Essai", "own")
        attack["break_timing"] = compute_event_timing_multi(selected, "Break", "own")
    back3_trend = compute_back3_trend(selected) if selected else []
    return render_template("season_attaque.html", data=attack, back3_trend=back3_trend, season_mode=True,
                           active="attaque", qs=qs, selected_count=len(selected))
@app.route("/season/defense")
def season_defense():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    attack_adv = compute_attack_sector(instances, "adverse") if instances else None
    if attack_adv:
        attack_adv["try_timing"] = compute_event_timing_multi(selected, "Essai", "adverse")
        attack_adv["break_timing"] = compute_event_timing_multi(selected, "Break", "adverse")
    defense = compute_defense_sector(instances, "adverse") if instances else None
    return render_template("season_defense.html", data=attack_adv, defense=defense, season_mode=True,
                           active="defense", qs=qs, selected_count=len(selected))
@app.route("/season/ruck")
def season_ruck():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    ruck = compute_ruck_sector(instances) if instances else None
    return render_template("season_ruck.html", data=ruck, season_mode=True, active="ruck", qs=qs,
                           phase_icons=PHASE_ICONS, selected_count=len(selected))
@app.route("/season/touches")
def season_touches():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    lineout = compute_lineout_detail(instances) if instances else None
    return render_template("season_touches.html", data=lineout, season_mode=True, active="touches", qs=qs,
                           selected_count=len(selected))
@app.route("/season/melee")
def season_melee():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    scrum = compute_scrum_detail(instances) if instances else None
    return render_template("season_melee.html", data=scrum, season_mode=True, active="melee", qs=qs,
                           phase_icons=PHASE_ICONS, selected_count=len(selected))
@app.route("/season/jap")
def season_jap():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    kicking = compute_kicking_detail(instances) if instances else None
    manual_totals = {
        "kick_distance_own": _sum_manual(selected, "kick_distance_own"),
        "kick_distance_adverse": _sum_manual(selected, "kick_distance_adverse"),
    }
    return render_template("season_jap.html", data=kicking, manual_totals=manual_totals, season_mode=True,
                           active="jap", qs=qs, selected_count=len(selected))
@app.route("/season/joueurs")
def season_joueurs():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    groups = compute_squad_season_stats(instances, selected)
    attack_table = compute_player_attack_table(instances)
    defense_table = compute_player_defense_table(instances)
    ruck_table = compute_player_ruck_table(instances)
    player_cards = attach_overview_highlights(build_player_cards(attack_table, defense_table, ruck_table))
    return render_template("season_joueurs.html", groups=groups, season_mode=True, active="joueurs",
                           qs=qs, selected_count=len(selected),
                           attack_table=attack_table, defense_table=defense_table,
                           ruck_table=ruck_table, player_cards=player_cards)
@app.route("/season/comparateur")
def season_comparateur():
    _, selected, selected_ids, qs = _season_context()
    instances = _season_instances(selected)
    player_a = request.args.get("a") or ""
    player_b = request.args.get("b") or ""
    comparison = None
    radar_svg = None
    if instances and player_a and player_b:
        comparison = compute_player_comparison(instances, player_a, player_b)
        radar_svg = compute_player_radar_svg(comparison["attack_rows"], comparison["defense_rows"],
                                              comparison["ruck_rows"])
    all_players = [
        {"position": position, "players": SQUAD_ROSTER[position]}
        for position in SQUAD_POSITION_ORDER
    ]
    return render_template(
        "season_comparateur.html", data=comparison, radar_svg=radar_svg, all_players=all_players,
        player_a=player_a, player_b=player_b, selected_ids=selected_ids,
        season_mode=True, active="comparateur", qs=qs, selected_count=len(selected),
    )
@app.route("/season/jiff")
def season_jiff():
    _, selected, _, qs = _season_context()
    jiff_data = compute_jiff_chart(selected)
    return render_template("season_jiff.html", data=jiff_data, season_mode=True, active="jiff", qs=qs,
                           selected_count=len(selected))
@app.route("/season/transition")
def season_transition():
    _, selected, _, qs = _season_context()
    instances = _season_instances(selected)
    transition = compute_transition_sector(instances) if instances else None
    return render_template("season_transition.html", data=transition, season_mode=True, active="transition", qs=qs,
                           selected_count=len(selected))
def _load_training_sessions():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM training_sessions ORDER BY session_date DESC, id DESC"
    ).fetchall()
    sessions = []
    for r in rows:
        sessions.append({
            "id": r["id"],
            "session_date": r["session_date"],
            "items": json.loads(r["items_json"] or "[]"),
        })
    return sessions
@app.route("/season/entrainement", methods=["GET", "POST"])
def season_entrainement():
    if request.method == "POST":
        if not session.get("is_admin"):
            flash("Connecte-toi pour enregistrer une séance.", "error")
            return redirect(url_for("login", next=request.path))
        session_date = request.form.get("session_date", "").strip()
        if not session_date:
            flash("Merci d'indiquer une date de séance.", "error")
            return redirect(url_for("season_entrainement"))
        items = []
        for cat_name, subcats in TRAINING_TAXONOMY.items():
            for subcat_name, elements in subcats.items():
                for element in elements:
                    check_field = f"item::{cat_name}::{subcat_name}::{element}"
                    if not request.form.get(check_field):
                        continue
                    minutes_raw = request.form.get(f"minutes::{cat_name}::{subcat_name}::{element}", "").strip()
                    minutes = int(minutes_raw) if minutes_raw.isdigit() else None
                    items.append({
                        "category": cat_name, "subcategory": subcat_name,
                        "element": element, "minutes": minutes,
                    })
        if not items:
            flash("Sélectionne au moins un élément travaillé lors de la séance.", "error")
            return redirect(url_for("season_entrainement"))
        db = get_db()
        db.execute(
            "INSERT INTO training_sessions (created_at, session_date, items_json) VALUES (%s, %s, %s)",
            (datetime.utcnow().isoformat(), session_date, json.dumps(items)),
        )
        db.commit()
        flash("Séance enregistrée.", "success")
        return redirect(url_for("season_entrainement"))
    sessions = _load_training_sessions()
    weeks = group_training_sessions_by_period(sessions, "week")
    months = group_training_sessions_by_period(sessions, "month")
    return render_template(
        "season_entrainement.html", weeks=weeks, months=months, sessions=sessions, taxonomy=TRAINING_TAXONOMY,
        today=datetime.utcnow().strftime("%Y-%m-%d"),
        season_mode=True, active="entrainement", qs="",
    )
@app.route("/season/entrainement/<int:session_id>/delete", methods=["POST"])
@admin_required
def delete_training_session(session_id):
    db = get_db()
    db.execute("DELETE FROM training_sessions WHERE id = %s", (session_id,))
    db.commit()
    flash("Séance supprimée.", "success")
    return redirect(url_for("season_entrainement"))
@app.route("/season/analyse")
def season_analyse():
    _, selected, _, qs = _season_context()
    analysis = compute_win_loss_analysis(selected)
    return render_template(
        "season_analyse.html", analysis=analysis, season_mode=True, active="analyse",
        qs=qs, selected_count=len(selected),
    )
# ---------------------------------------------------------------------------
# ESPACE DOCUMENTS — plateforme centrale du staff
# ---------------------------------------------------------------------------
# Chaque membre du staff (connecté avec son compte) peut créer des dossiers,
# déposer des fichiers (PDF, présentations, images, Excel... max 30 Mo) et
# ajouter des liens vidéo (Hudl, YouTube, Drive...). Les fichiers sont stockés
# dans PostgreSQL : ils survivent aux redéploiements Render.
# Règles : tout le staff peut déposer ; chacun peut supprimer SES dépôts ;
# l'admin peut tout supprimer ; le mode démo ne voit rien (voir gate_optional_features).

DOC_TYPE_ICONS = {
    "pdf": "📕", "doc": "📘", "docx": "📘", "ppt": "📙", "pptx": "📙", "key": "📙",
    "xls": "📗", "xlsx": "📗", "csv": "📗", "png": "🖼️", "jpg": "🖼️", "jpeg": "🖼️",
    "gif": "🖼️", "heic": "🖼️", "webp": "🖼️", "mp4": "🎬", "mov": "🎬", "zip": "🗜️",
    "txt": "📄", "xml": "📄",
}
# Extensions dont l'aperçu peut s'ouvrir directement dans le navigateur.
DOC_INLINE_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"}

def _doc_ext(filename):
    return (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""

def _doc_icon(doc):
    if doc["kind"] == "link":
        return "🔗"
    return DOC_TYPE_ICONS.get(_doc_ext(doc["filename"]), "📄")

def _doc_can_delete(row):
    """L'admin supprime tout ; un membre du staff supprime ce qu'il a déposé lui-même."""
    if session.get("is_admin"):
        return True
    email = session.get("user_email", "")
    return bool(email) and (row.get("uploaded_by") or row.get("created_by")) == email

def _folder_or_404(db, folder_id):
    folder = db.execute("SELECT * FROM doc_folders WHERE id = %s", (folder_id,)).fetchone()
    if not folder:
        abort(404)
    return folder

def _folder_breadcrumb(db, folder):
    """Remonte la chaîne des parents pour afficher le fil d'Ariane."""
    crumbs = []
    current = folder
    while current:
        crumbs.append(current)
        current = (
            db.execute("SELECT * FROM doc_folders WHERE id = %s", (current["parent_id"],)).fetchone()
            if current["parent_id"] else None
        )
    return list(reversed(crumbs))

def _human_size(size_bytes):
    if not size_bytes:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} o"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} Ko"
    return f"{size_bytes / (1024 * 1024):.1f} Mo"

@app.route("/documents")
@app.route("/documents/dossier/<int:folder_id>")
def documents(folder_id=None):
    db = get_db()
    folder = _folder_or_404(db, folder_id) if folder_id else None
    breadcrumb = _folder_breadcrumb(db, folder) if folder else []
    if folder_id:
        subfolders = db.execute(
            "SELECT * FROM doc_folders WHERE parent_id = %s ORDER BY name", (folder_id,)
        ).fetchall()
        docs = db.execute(
            """SELECT id, folder_id, kind, filename, mimetype, size_bytes, url, title,
                      uploaded_by, uploaded_at
               FROM documents WHERE folder_id = %s
               ORDER BY uploaded_at DESC, id DESC""",
            (folder_id,),
        ).fetchall()
    else:
        subfolders = db.execute(
            "SELECT * FROM doc_folders WHERE parent_id IS NULL ORDER BY name"
        ).fetchall()
        docs = db.execute(
            """SELECT id, folder_id, kind, filename, mimetype, size_bytes, url, title,
                      uploaded_by, uploaded_at
               FROM documents WHERE folder_id IS NULL
               ORDER BY uploaded_at DESC, id DESC"""
        ).fetchall()
    # Nombre d'éléments par sous-dossier (dossiers enfants + documents) pour l'affichage.
    counts = {}
    for sub in subfolders:
        n_docs = db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE folder_id = %s", (sub["id"],)
        ).fetchone()["n"]
        n_dirs = db.execute(
            "SELECT COUNT(*) AS n FROM doc_folders WHERE parent_id = %s", (sub["id"],)
        ).fetchone()["n"]
        counts[sub["id"]] = n_docs + n_dirs
    docs_view = []
    for d in docs:
        d = dict(d)
        d["icon"] = _doc_icon(d)
        d["ext"] = _doc_ext(d["filename"])
        d["inline"] = d["kind"] == "file" and d["ext"] in DOC_INLINE_EXTENSIONS
        d["size_human"] = _human_size(d["size_bytes"])
        d["can_delete"] = _doc_can_delete(d)
        d["date_human"] = (d["uploaded_at"] or "")[:10]
        docs_view.append(d)
    return render_template(
        "documents.html", folder=folder, breadcrumb=breadcrumb,
        subfolders=subfolders, folder_counts=counts, docs=docs_view,
        can_delete_folder={s["id"]: _doc_can_delete(dict(s)) for s in subfolders},
    )

@app.route("/documents/dossier", methods=["POST"])
def documents_create_folder():
    name = request.form.get("name", "").strip()
    parent_id = request.form.get("parent_id") or None
    if not name:
        flash("Merci d'indiquer un nom de dossier.", "error")
    else:
        db = get_db()
        db.execute(
            "INSERT INTO doc_folders (name, parent_id, created_by, created_at) VALUES (%s, %s, %s, %s)",
            (name, parent_id, session.get("user_email", ""), datetime.utcnow().isoformat()),
        )
        db.commit()
        flash(f"Dossier « {name} » créé.", "success")
    return redirect(url_for("documents", folder_id=parent_id) if parent_id else url_for("documents"))

@app.route("/documents/upload", methods=["POST"])
def documents_upload():
    folder_id = request.form.get("folder_id") or None
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Merci de sélectionner au moins un fichier.", "error")
        return redirect(url_for("documents", folder_id=folder_id) if folder_id else url_for("documents"))
    db = get_db()
    saved = 0
    for file in files:
        payload = file.read()
        if not payload:
            continue
        db.execute(
            """INSERT INTO documents
               (folder_id, kind, filename, mimetype, size_bytes, data, uploaded_by, uploaded_at)
               VALUES (%s, 'file', %s, %s, %s, %s, %s, %s)""",
            (
                folder_id, file.filename,
                file.mimetype or "application/octet-stream",
                len(payload), psycopg2.Binary(payload),
                session.get("user_email", ""), datetime.utcnow().isoformat(),
            ),
        )
        saved += 1
    db.commit()
    flash(f"{saved} fichier{'s' if saved > 1 else ''} déposé{'s' if saved > 1 else ''}.", "success")
    return redirect(url_for("documents", folder_id=folder_id) if folder_id else url_for("documents"))

@app.route("/documents/lien", methods=["POST"])
def documents_add_link():
    folder_id = request.form.get("folder_id") or None
    title = request.form.get("title", "").strip()
    url = request.form.get("url", "").strip()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        flash("Merci de coller un lien complet (commençant par http:// ou https://).", "error")
    else:
        db = get_db()
        db.execute(
            """INSERT INTO documents (folder_id, kind, url, title, uploaded_by, uploaded_at)
               VALUES (%s, 'link', %s, %s, %s, %s)""",
            (folder_id, url, title or url, session.get("user_email", ""), datetime.utcnow().isoformat()),
        )
        db.commit()
        flash("Lien ajouté.", "success")
    return redirect(url_for("documents", folder_id=folder_id) if folder_id else url_for("documents"))

def _serve_document(doc_id, inline):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()
    if not doc or doc["kind"] != "file":
        abort(404)
    data = bytes(doc["data"])
    disposition = "inline" if inline else "attachment"
    filename = (doc["filename"] or "document").replace('"', "")
    return Response(
        data,
        mimetype=doc["mimetype"] or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )

@app.route("/documents/<int:doc_id>/telecharger")
def documents_download(doc_id):
    return _serve_document(doc_id, inline=False)

@app.route("/documents/<int:doc_id>/apercu")
def documents_preview(doc_id):
    return _serve_document(doc_id, inline=True)

@app.route("/documents/<int:doc_id>/supprimer", methods=["POST"])
def documents_delete(doc_id):
    db = get_db()
    doc = db.execute(
        "SELECT id, folder_id, kind, filename, title, uploaded_by FROM documents WHERE id = %s",
        (doc_id,),
    ).fetchone()
    if not doc:
        abort(404)
    if not _doc_can_delete(dict(doc)):
        flash("Tu ne peux supprimer que tes propres dépôts (ou demande à l'admin).", "error")
    else:
        db.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        db.commit()
        flash("Document supprimé.", "success")
    folder_id = doc["folder_id"]
    return redirect(url_for("documents", folder_id=folder_id) if folder_id else url_for("documents"))

@app.route("/documents/dossier/<int:folder_id>/supprimer", methods=["POST"])
def documents_delete_folder(folder_id):
    db = get_db()
    folder = _folder_or_404(db, folder_id)
    parent_id = folder["parent_id"]
    if not _doc_can_delete(dict(folder)):
        flash("Tu ne peux supprimer que les dossiers que tu as créés (ou demande à l'admin).", "error")
        return redirect(url_for("documents", folder_id=folder_id))
    n_docs = db.execute(
        "SELECT COUNT(*) AS n FROM documents WHERE folder_id = %s", (folder_id,)
    ).fetchone()["n"]
    n_dirs = db.execute(
        "SELECT COUNT(*) AS n FROM doc_folders WHERE parent_id = %s", (folder_id,)
    ).fetchone()["n"]
    if (n_docs or n_dirs) and not session.get("is_admin"):
        flash("Ce dossier n'est pas vide : seul l'admin peut le supprimer avec son contenu.", "error")
        return redirect(url_for("documents", folder_id=folder_id))
    db.execute("DELETE FROM doc_folders WHERE id = %s", (folder_id,))
    db.commit()
    flash(f"Dossier « {folder['name']} » supprimé.", "success")
    return redirect(url_for("documents", folder_id=parent_id) if parent_id else url_for("documents"))

@app.route("/documents/dossier/<int:folder_id>/renommer", methods=["POST"])
def documents_rename_folder(folder_id):
    db = get_db()
    folder = _folder_or_404(db, folder_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Merci d'indiquer un nom de dossier.", "error")
    elif not _doc_can_delete(dict(folder)):
        flash("Tu ne peux renommer que les dossiers que tu as créés (ou demande à l'admin).", "error")
    else:
        db.execute("UPDATE doc_folders SET name = %s WHERE id = %s", (name, folder_id))
        db.commit()
        flash("Dossier renommé.", "success")
    return redirect(url_for("documents", folder_id=folder_id))

# ---------------------------------------------------------------------------
# ANALYSE VIDÉO — page d'entrée du pôle qui regroupe tout ce qui existait sur
# le site avant l'ajout des espaces Documents / Calendrier / Cahier des charges
# (Matchs, Bilan de saison, Adversaires, Tendances, Prochain match, Effectif).
# ---------------------------------------------------------------------------
@app.route("/analyse-video")
def analyse_video():
    return render_template("analyse_video.html")

# ---------------------------------------------------------------------------
# CALENDRIER — agenda d'événements du staff (réunions, déplacements, rendez-vous
# médicaux...), distinct des matchs/entraînements gérés ailleurs sur le site.
# Règles identiques à l'espace Documents : tout le staff ajoute, chacun gère ses
# propres événements, l'admin gère tout ; invisible en mode démo.
# ---------------------------------------------------------------------------
def _cal_can_edit(row):
    """Réutilise la même règle que les documents : admin = tout, sinon = ses propres
    événements uniquement (comparaison sur created_by)."""
    return _doc_can_delete(dict(row))

def _cal_event_json(row):
    r = dict(row)
    return {
        "id": r["id"],
        "title": r["title"],
        "description": r.get("description") or "",
        "event_date": r["event_date"],
        "event_time": r.get("event_time") or "",
        "created_by": r.get("created_by") or "",
        "can_edit": _cal_can_edit(r),
    }

@app.route("/calendrier")
def calendrier():
    # La page est rendue côté client (vue Jour / Semaine / Mois interactive, façon iPhone) :
    # ce endpoint ne fait que servir le squelette HTML + la date du jour. Les événements
    # sont chargés en JSON via /calendrier/api/events selon la période affichée.
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return render_template("calendrier.html", today=today)

@app.route("/calendrier/api/events")
def calendrier_api_events():
    """Renvoie les événements dans une période [start, end] (AAAA-MM-JJ, bornes incluses).
    Sans paramètres, renvoie tout (utilisé en secours)."""
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    db = get_db()
    if start and end:
        rows = db.execute(
            "SELECT * FROM calendar_events WHERE event_date >= %s AND event_date <= %s "
            "ORDER BY event_date ASC, COALESCE(event_time, '99:99') ASC, id ASC",
            (start, end),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM calendar_events ORDER BY event_date ASC, COALESCE(event_time, '99:99') ASC, id ASC"
        ).fetchall()
    return jsonify({"events": [_cal_event_json(r) for r in rows]})

@app.route("/calendrier/api/ajouter", methods=["POST"])
def calendrier_api_add():
    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    event_date = (data.get("event_date") or "").strip()
    event_time = (data.get("event_time") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not event_date:
        return jsonify({"error": "Merci d'indiquer au moins un titre et une date."}), 400
    db = get_db()
    row = db.execute(
        """INSERT INTO calendar_events (title, description, event_date, event_time, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (title, description or None, event_date, event_time or None,
         session.get("user_email", ""), datetime.utcnow().isoformat()),
    ).fetchone()
    db.commit()
    return jsonify({"event": _cal_event_json(row)})

@app.route("/calendrier/api/<int:event_id>/modifier", methods=["POST"])
def calendrier_api_edit(event_id):
    db = get_db()
    row = db.execute("SELECT * FROM calendar_events WHERE id = %s", (event_id,)).fetchone()
    if not row:
        return jsonify({"error": "Introuvable."}), 404
    if not _cal_can_edit(row):
        return jsonify({"error": "Tu ne peux modifier que tes propres événements (ou demande à l'admin)."}), 403
    data = request.get_json(silent=True) or request.form
    title = (data.get("title") or "").strip()
    event_date = (data.get("event_date") or "").strip()
    event_time = (data.get("event_time") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not event_date:
        return jsonify({"error": "Merci d'indiquer au moins un titre et une date."}), 400
    updated = db.execute(
        """UPDATE calendar_events SET title = %s, description = %s, event_date = %s, event_time = %s
           WHERE id = %s RETURNING *""",
        (title, description or None, event_date, event_time or None, event_id),
    ).fetchone()
    db.commit()
    return jsonify({"event": _cal_event_json(updated)})

@app.route("/calendrier/api/<int:event_id>/supprimer", methods=["POST"])
def calendrier_api_delete(event_id):
    db = get_db()
    row = db.execute("SELECT * FROM calendar_events WHERE id = %s", (event_id,)).fetchone()
    if not row:
        return jsonify({"error": "Introuvable."}), 404
    if not _cal_can_edit(row):
        return jsonify({"error": "Tu ne peux supprimer que tes propres événements (ou demande à l'admin)."}), 403
    db.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
    db.commit()
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# CAHIER DES CHARGES — suivi de tâches/exigences du staff, en 3 colonnes
# (à faire / en cours / fait). Tout le staff peut créer une tâche et déplacer
# n'importe quelle tâche d'une colonne à l'autre (travail collaboratif) ; seule
# la personne qui l'a créée (ou l'admin) peut la supprimer. Invisible en démo.
# ---------------------------------------------------------------------------
CHARGES_STATUSES = ["a_faire", "en_cours", "fait"]
CHARGES_STATUS_LABELS = {"a_faire": "À faire", "en_cours": "En cours", "fait": "Fait"}

@app.route("/cahier-des-charges")
def cahier_charges():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM charges_items ORDER BY created_at DESC, id DESC"
    ).fetchall()
    columns = {status: [] for status in CHARGES_STATUSES}
    for r in rows:
        r = dict(r)
        r["can_delete"] = _doc_can_delete(r)
        r["date_human"] = (r["created_at"] or "")[:10]
        columns.setdefault(r["status"], []).append(r)
    return render_template(
        "cahier_charges.html", columns=columns, statuses=CHARGES_STATUSES,
        status_labels=CHARGES_STATUS_LABELS, total_count=len(rows),
    )

@app.route("/cahier-des-charges/ajouter", methods=["POST"])
def cahier_charges_add():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title:
        flash("Merci d'indiquer au moins un titre.", "error")
    else:
        db = get_db()
        db.execute(
            """INSERT INTO charges_items (title, description, status, created_by, created_at)
               VALUES (%s, %s, 'a_faire', %s, %s)""",
            (title, description or None, session.get("user_email", ""), datetime.utcnow().isoformat()),
        )
        db.commit()
        flash("Tâche ajoutée.", "success")
    return redirect(url_for("cahier_charges"))

@app.route("/cahier-des-charges/<int:item_id>/statut", methods=["POST"])
def cahier_charges_status(item_id):
    new_status = request.form.get("status", "")
    if new_status not in CHARGES_STATUSES:
        abort(400)
    db = get_db()
    row = db.execute("SELECT id FROM charges_items WHERE id = %s", (item_id,)).fetchone()
    if not row:
        abort(404)
    db.execute(
        "UPDATE charges_items SET status = %s, updated_at = %s WHERE id = %s",
        (new_status, datetime.utcnow().isoformat(), item_id),
    )
    db.commit()
    flash(f"Tâche déplacée vers « {CHARGES_STATUS_LABELS[new_status]} ».", "success")
    return redirect(url_for("cahier_charges"))

@app.route("/cahier-des-charges/<int:item_id>/supprimer", methods=["POST"])
def cahier_charges_delete(item_id):
    db = get_db()
    row = db.execute("SELECT * FROM charges_items WHERE id = %s", (item_id,)).fetchone()
    if not row:
        abort(404)
    if not _doc_can_delete(dict(row)):
        flash("Tu ne peux supprimer que les tâches que tu as créées (ou demande à l'admin).", "error")
    else:
        db.execute("DELETE FROM charges_items WHERE id = %s", (item_id,))
        db.commit()
        flash("Tâche supprimée.", "success")
    return redirect(url_for("cahier_charges"))

init_db()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
