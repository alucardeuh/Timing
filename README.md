# Timing — carnet de charge et de rentabilité

App locale pour suivre tes projets vendus en jours/semaine : rentabilité réelle
vs vendue, cadence, et une carte de charge qui anticipe tes pics d'activité —
provisoires compris.

## Installation

```bash
cd timing
python3 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```bash
python3 app.py
```

Ouvre **http://127.0.0.1:5060**. La base SQLite (`instance/timing.sqlite3`)
se crée automatiquement au premier lancement.

## Comment ça marche

**Un projet** a un statut : *provisoire* (pas encore signé), *confirmé*, *en
pause* ou *terminé*. Un projet provisoire garde les mêmes infos qu'un projet
confirmé (jours/semaine, dates, prix estimé) mais n'affecte tes chiffres de
rentabilité tant qu'il n'est pas confirmé, et s'affiche hachuré sur le
Planning.

**Chaque jour**, tu ajoutes une entrée en **% de ta journée** (ex. 40%) depuis
la page d'accueil ou la fiche projet — plus besoin de calculer des heures.

**La carte de charge** (page d'accueil et Planning) additionne le % de charge
de tous tes projets actifs, jour par jour, et colore chaque jour : libre,
léger, chargé, ou tempête (seuils réglables dans Réglages). Un bouton permet
d'inclure ou non les projets provisoires dans ce calcul, pour tester "et si je
prends ce projet en plus ?"

**Les tâches** sont optionnelles : ajoute-en depuis une fiche projet pour
répartir tes saisies (ex. "Maquettes", "Réunions client") et voir où part ton
temps, projet par projet ou tous projets confondus (page Comparatif, onglet
"Par tâche").

**Le Planning** est un Gantt sur 13 semaines glissantes, aligné avec la carte
de charge en dessous. Navigue avec les boutons ← 13 sem. / Aujourd'hui / 13
sem. →.

## Structure du projet

```
timing/
├── app.py              routes Flask
├── calculations.py     rentabilité, cadence, charge quotidienne
├── db.py                accès SQLite
├── schema.sql            schéma de la base
├── templates/            pages Jinja2
└── static/                CSS (thème blanc) + JS
```

## Sauvegarder tes données

Tout vit dans `instance/timing.sqlite3`. Copie ce fichier pour sauvegarder ;
supprime-le pour repartir de zéro.
