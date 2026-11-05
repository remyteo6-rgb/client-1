# Configuration de gunicorn (lue automatiquement : le Procfile lance « gunicorn app:app ... »).


def post_worker_init(worker):
    """Dès que le serveur est prêt, on calcule en arrière-plan les pages de saison (voir
    app.demarrer_prechauffage) : la première visite après un déploiement n'attend plus."""
    try:
        import app as _app
        _app.demarrer_prechauffage(delai=2.0)
    except Exception as e:
        print("Préchauffage non lancé :", e, flush=True)
