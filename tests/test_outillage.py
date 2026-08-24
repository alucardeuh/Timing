"""
Outillage : configuration du linter et intégration continue.

Une suite de tests ne sert à rien si elle ne tourne que quand on y pense, et
une configuration de linter absente du dépôt donne un verdict différent sur
chaque machine.
"""
from pathlib import Path

RACINE = Path(__file__).parent.parent


def test_la_configuration_du_linter_est_versionnee():
    """Sans pyproject.toml dans le dépôt, `ruff check .` applique ses
    valeurs par défaut : le verdict dépend alors de la version de ruff
    installée sur la machine, pas du projet."""
    config = (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.ruff]" in config
    assert "line-length" in config


def test_pytest_trouve_les_modules_depuis_la_racine():
    config = (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.pytest.ini_options]" in config
    assert "pythonpath" in config


def test_le_workflow_lance_le_linter_et_les_tests():
    workflow = RACINE / ".github" / "workflows" / "tests.yml"
    assert workflow.exists()
    contenu = workflow.read_text(encoding="utf-8")
    assert "ruff check ." in contenu
    assert "pytest" in contenu


def test_le_workflow_verifie_aussi_le_demarrage():
    """Les tests passent par le client de test de Flask, qui ne touche
    jamais au bloc `if __name__ == "__main__"`. Une étourderie dans les
    migrations ou la sauvegarde du démarrage y passerait inaperçue jusqu'au
    premier lancement réel."""
    contenu = (RACINE / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "python app.py" in contenu
    assert "curl" in contenu


def test_le_workflow_se_declenche_sur_toutes_les_branches():
    """Un workflow limité à `main` ne dit rien tant que la branche n'est pas
    fusionnée, c'est-à-dire au moment où l'information ne sert plus."""
    contenu = (RACINE / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert 'branches: ["**"]' in contenu
    assert "pull_request" in contenu
