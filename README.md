# Rugby Analytics — Dashboard Sportscode

Petite application web pour importer tes exports XML Sportscode, suivre tes statistiques de match (saison) et celles de tes adversaires (scouting), accessible en ligne par toi et ton staff.

## Comment ça marche

1. Tu termines ton codage dans Sportscode.
2. Tu exportes le fichier XML (`ALL_INSTANCES`).
3. Tu l'importes sur le site (`+ Importer un match`), en indiquant l'adversaire, la date, la compétition.
4. Le site range automatiquement les événements par catégorie (Ruck, Plaquage, Touches, Mêlées, Turnovers, discipline, plans de jeu nommés type BULL/TIGER/KILL...) et par côté : **ton équipe** vs **adversaire**, en se basant sur les codes tels que `21 - Plaquage Nice` / `56 - Ruck Adverse`.
5. Le tableau de bord d'un match affiche les indicateurs clés, un graphique nous/eux, un graphique de territoire par zone de terrain, le détail complet par catégorie et l'implication de chaque joueur codé.
6. La page **Adversaires** cumule automatiquement tous les matchs joués contre une même équipe pour te donner une vue scouting.

## Notes importantes

- **Détection "nous" vs "adversaire"** : le parseur détecte automatiquement le mot utilisé dans tes codes pour désigner ton équipe (ex. "Nice") en comptant les codes numérotés se terminant par ce mot, à l'opposé de "Adverse"/"Adv". Si ta fenêtre de code change de nom d'une saison à l'autre, ça continue de fonctionner sans réglage.
- **Taux de réussite** : calculé de façon heuristique en repérant des libellés comme REUSSI/RATE, GAGNE/PERDU, +/- dans les labels Sportscode. C'est une approximation utile mais pas garantie à 100% correcte pour toutes les catégories (les rôles de ruck comme ANCREUR/RASEUR/GRATTEUR ne sont pas interprétés comme succès/échec, par exemple). Les **comptages bruts**, eux, sont toujours exacts.
- **Métadonnées de match** (adversaire, date, compétition) : Sportscode n'exporte pas ces infos, tu les renseignes toi-même à l'import.
- **Multi-utilisateurs** : l'app n'a pas d'authentification par défaut — toute personne avec l'URL peut voir/importer des matchs. Si tu veux la protéger, vois la section "Ajouter un mot de passe" plus bas.

## Lancer en local (pour tester)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Puis ouvre `http://localhost:5000`.

## Déployer en ligne (accès pour toi + ton staff)

La façon la plus simple, gratuite pour ce niveau d'usage : **Render**.

1. Crée un compte sur [render.com](https://render.com).
2. Mets ce dossier dans un dépôt Git (GitHub, GitLab...). Si tu ne sais pas faire, dis-le et on peut passer par l'upload direct de Render ou un autre hébergeur.
3. Sur Render : **New +** → **Web Service** → connecte ton dépôt.
4. Renseigne :
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app`
   - **Instance type** : Free
5. Render te donne une URL du type `https://ton-app.onrender.com` — partage-la à ton staff.

⚠️ Sur le plan gratuit de Render, le disque n'est pas garanti persistant après redéploiement : si tu veux conserver l'historique des matchs sur le long terme, prévois d'ajouter un disque persistant (payant, ~1$/mois) dans les réglages du service, ou exporte régulièrement tes données.

**Alternative** : Railway.app fonctionne de façon très similaire (mêmes commandes) et propose aussi un disque persistant.

## Ajouter un mot de passe (optionnel)

Pour une protection simple, tu peux ajouter une authentification basique en front de l'app (ex. via les réglages "Basic Auth" de certains hébergeurs, ou un middleware Flask). Dis-le-moi si tu veux que je l'ajoute.

## Structure du projet

```
app.py            → routes Flask (liste des matchs, import, dashboard match, scouting adversaire)
parser.py         → parseur générique du XML Sportscode + agrégation des stats
templates/        → pages HTML (Jinja2)
static/style.css  → styles
requirements.txt  → dépendances Python
Procfile          → commande de démarrage pour l'hébergeur
```
