"""
Le couplage entre filtres a déménagé, le bug qu'il produisait aussi.

À l'origine, le tableau de bord portait deux filtres indépendants sur la
même page — inclure les provisoires, et l'ordre du classement de
rentabilité — mais chaque lien ne reconstruisait que son propre paramètre.
Changer l'un remettait l'autre à zéro.

Le classement vit maintenant dans Comparatif, aux côtés d'un second
paramètre : l'onglet actif. Le piège est exactement le même, à un endroit
différent, donc le test suit le bug plutôt que de disparaître avec la page
qu'il surveillait.
"""
import re
from datetime import date, timedelta

from conftest import project_data

import app as flask_app


def _get(client, path):
    flask_app.app.config["CSRF_PROTECT"] = False
    return client.get(path)


def _projet_avance(base):
    """Un projet assez consommé pour que son indice soit fiable : le
    classement ne s'affiche pas en dessous du seuil de fiabilité."""
    pid = base.create_project(project_data(
        status="confirmed", days_per_week=5, duration_value=1,  # 5 jours vendus
        start_date=(date.today() - timedelta(days=3)).isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)  # 1/5 = 20 %
    return pid


def test_changer_le_tri_preserve_l_onglet(base):
    _projet_avance(base)
    client = flask_app.app.test_client()
    html = _get(client, "/comparatif?tab=classement&rank=day_rate").data.decode()

    liens_tri = re.findall(r'href="(/comparatif\?[^"]*rank=[^"]+)"', html)
    assert len(liens_tri) == 3
    assert all("tab=classement" in lien for lien in liens_tri)


def test_le_classement_a_quitte_l_accueil(base):
    """L'accueil ne doit plus porter le classement.

    Il servait à une revue mensuelle, pas à la question « que dois-je faire
    aujourd'hui ». Deux blocs de plus repoussaient la saisie sous la ligne
    de flottaison. Ce test empêche un retour en arrière discret.
    """
    _projet_avance(base)
    client = flask_app.app.test_client()
    html = _get(client, "/").data.decode()

    assert "Les plus rentables" not in html
    assert "Seuil de rentabilité" not in html
    assert re.search(r'href="/\?rank=', html) is None


def test_le_classement_est_bien_dans_comparatif(base):
    _projet_avance(base)
    client = flask_app.app.test_client()
    html = _get(client, "/comparatif?tab=classement").data.decode()

    assert "Les plus rentables" in html
