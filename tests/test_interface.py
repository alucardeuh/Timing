"""
Garanties d'interface : hors ligne, thème, palette, impression.

Chaque test ici correspond à une chose qui marchait mal sans qu'aucun test
ne puisse le voir, parce qu'elle ne se manifestait qu'à l'écran.
"""
from pathlib import Path

import app as flask_app
from conftest import project_data

RACINE = Path(__file__).parent.parent


def _client():
    flask_app.app.config["CSRF_PROTECT"] = False
    return flask_app.app.test_client()


def test_aucune_police_n_est_chargee_depuis_le_reseau():
    """Une app locale ouverte sans réseau perdait ses trois familles d'un
    coup et se rabattait sur les polices système, avec des largeurs de
    colonnes qui ne correspondaient plus."""
    base_html = (RACINE / "templates" / "base.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in base_html
    assert "fonts.gstatic.com" not in base_html


def test_les_fichiers_de_police_sont_bien_presents():
    """Retirer le CDN sans embarquer les fichiers aurait été pire que de le
    garder."""
    polices = sorted((RACINE / "static" / "fonts").glob("*.woff2"))
    assert len(polices) == 9
    css = (RACINE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for police in polices:
        assert police.name in css


def test_l_interface_est_en_clair_uniquement():
    """Un thème sombre a existé puis a été retiré. Maintenir deux palettes
    cohérentes coûtait un arbitrage à chaque couleur ajoutée, et les
    couleurs de charge — léger, chargé, tempête — devaient être réaccordées
    et pas seulement assombries pour rester distinguables. Ce test empêche
    qu'une demi-palette revienne par accident."""
    css = (RACINE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "prefers-color-scheme" not in css
    assert "data-theme" not in css
    assert "color-scheme: light" in css

    base_html = (RACINE / "templates" / "base.html").read_text(encoding="utf-8")
    assert "data-theme" not in base_html


def test_aucun_reglage_de_theme_ne_subsiste(base):
    """Un réglage sans effet est pire qu'un réglage absent : on le change,
    rien ne bouge, et on doute du reste de la page."""
    assert "theme" not in base.get_settings()
    html = _client().get("/reglages").data.decode()
    assert "Thème" not in html
    assert 'name="theme"' not in html


def test_la_palette_renvoie_un_index_utilisable(base):
    pid = base.create_project(project_data(name="Refonte tarifaire",
                                           client="Client A"))
    reponse = _client().get("/api/recherche")

    assert reponse.status_code == 200
    index = reponse.get_json()
    assert any(p["nom"] == "Refonte tarifaire" and p["id"] == pid
               for p in index["projets"])
    assert any(page["nom"] == "Facturation" for page in index["pages"])


def test_la_navigation_est_declaree_une_seule_fois():
    """Le rail, la barre mobile et la palette lisaient trois listes
    différentes : une page ajoutée n'apparaissait que là où on avait pensé à
    la déclarer."""
    pages = dict(flask_app.NAV_PAGES)
    for endpoint in pages.values():
        assert endpoint in flask_app.app.view_functions


def test_une_feuille_d_impression_existe():
    """Une revue de portefeuille se sort sur papier ou en PDF. Sans feuille
    dédiée, l'impression emportait le rail et les boutons, et coupait les
    grilles à la première colonne hors écran."""
    css = (RACINE / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "@media print" in css
    assert ".rail" in css.split("@media print")[1].split("}")[0] + css.split("@media print")[1][:600]
    # Le conteneur à défilement horizontal doit redevenir visible, sinon la
    # grille d'allocation s'imprime tronquée.
    assert "overflow: visible" in css.split("@media print")[1]


def test_la_barre_basse_et_la_palette_sont_dans_chaque_page(base):
    html = _client().get("/").data.decode()
    assert 'class="tabbar"' in html
    assert 'id="palette"' in html


def test_les_cellules_de_charge_restent_atteignables_au_clavier():
    """Le détail d'une journée n'existait qu'au survol et au clic : sans
    tabindex ni rôle, la carte de charge était hors d'atteinte au clavier et
    invisible pour un lecteur d'écran."""
    js = (RACINE / "static" / "js" / "app.js").read_text(encoding="utf-8")
    bloc = js.split("capacityDayDetail")[1]
    assert 'setAttribute("tabindex", "0")' in bloc
    assert 'setAttribute("role", "button")' in bloc
    assert 'addEventListener("focus"' in bloc


def test_la_palette_n_injecte_jamais_de_html():
    """Les noms de projets et de clients viennent de la saisie utilisateur :
    les injecter en HTML permettrait à un nom comme « <img onerror=…> »
    d'exécuter du script à la première recherche."""
    js = (RACINE / "static" / "js" / "app.js").read_text(encoding="utf-8")
    # Les commentaires du fichier mentionnent innerHTML pour expliquer
    # pourquoi il est banni : seul le code compte.
    code = "\n".join(ligne for ligne in js.splitlines()
                     if not ligne.strip().startswith("//"))
    assert "innerHTML" not in code
    assert "textContent" in code
