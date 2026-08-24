"""
Le tableau de bord a deux filtres indépendants sur la même page — inclure
les provisoires, et l'ordre du classement de rentabilité — mais chaque lien
ne reconstruisait que son propre paramètre. Changer l'un remettait l'autre
à zéro : cliquer « marge / jour » après avoir masqué les provisoires les
faisait réapparaître sans qu'on l'ait demandé.
"""
import re

import app as flask_app
from conftest import project_data


def _get(client, path):
    flask_app.app.config["CSRF_PROTECT"] = False
    return client.get(path)


def test_changer_le_tri_preserve_include_provisional(base):
    # La section "Les plus rentables" — celle qui porte les trois liens de
    # tri — ne s'affiche que si au moins un projet a un indice fiable
    # (>= min_consumption_pct de budget consommé).
    from datetime import date, timedelta
    pid = base.create_project(project_data(
        status="confirmed", days_per_week=5, duration_value=1,  # 5 jours vendus
        start_date=(date.today() - timedelta(days=3)).isoformat()))
    base.create_entry(pid, date.today().isoformat(), 100, 7)  # 1/5 = 20% consommé

    client = flask_app.app.test_client()
    html = _get(client, "/?include_provisional=0&rank=day_rate").data.decode()

    liens_tri = re.findall(r'href="(/\?rank=[^"]+)"', html)
    assert len(liens_tri) == 3
    assert all("include_provisional=0" in lien for lien in liens_tri)


def test_changer_include_provisional_preserve_le_tri(base):
    client = flask_app.app.test_client()
    html = _get(client, "/?rank=index").data.decode()

    lien = re.search(r'href="(/\?include_provisional=[^"]+)"', html)
    assert lien is not None
    assert "rank=index" in lien.group(1)
