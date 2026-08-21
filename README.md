# Timing — carnet de charge et de rentabilité

App locale pour suivre des projets vendus en jours/semaine : rentabilité
réelle vs vendue, cadence, facturation, et une carte de charge qui anticipe
les pics d'activité — provisoires compris.

## Lancement

```bash
python3 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Puis ouvre **http://127.0.0.1:5062**. La base SQLite
(`instance/timing.sqlite3`) se crée au premier lancement.

Sans Terminal : double-clique `lancer_timing.command` (macOS) ou
`lancer_timing (Windows).bat`. Les deux créent l'environnement, installent
les dépendances, attendent que le serveur réponde vraiment, puis ouvrent le
navigateur.

> **Pourquoi 5062 et pas 5060 ?** 5060 est le port SIP. Depuis la faille
> « NAT Slipstream », Chrome, Firefox et Safari refusent toute connexion
> HTTP vers ce port : le serveur tourne mais le navigateur affiche une page
> blanche ou `ERR_UNSAFE_PORT`. Les autres ports à éviter : 1, 7, 22, 25,
> 53, 69, 137, 161, 554, 1719, 1720, 1723, 5061, 6000, 6566, 10080.

Le port se change avec `TIMING_PORT=5063 python3 app.py`.

## Les trois règles de calcul

Elles expliquent tous les chiffres affichés, et méritent d'être connues.

**1. La consommation se compte en % de journée, jamais en heures.**
Une saisie de 50 % vaut une demi-journée, définitivement. Changer le réglage
« heures par jour » ne modifie que l'affichage des heures : ton historique de
consommation ne bouge pas.

**2. La charge se divise par tes jours ouvrés.**
Un projet vendu 5 jours par semaine, sur une semaine de 5 jours travaillés,
remplit 100 % de chaque jour ouvré et 0 % du week-end. Les jours travaillés
se règlent dans Réglages ; les congés et jours fériés déclarés dans Absences
mettent la capacité à zéro sur la période.

**3. Un indice de rentabilité n'est affiché que s'il veut dire quelque chose.**
Le rapport prix / temps passé sur une demi-journée saisie donne un taux
spectaculaire et faux. En dessous du seuil de consommation réglé (20 % par
défaut), l'indice reste masqué. L'**indice projeté**, lui, extrapole le
rythme observé jusqu'à la fin du projet et reste lisible tôt.

## Les pages

- **Aujourd'hui** (`/`) — alertes à traiter, saisie rapide, carte de charge
  sur 8 semaines, chiffre d'affaires du mois et de l'année vs objectifs,
  carnet de commandes, classement de rentabilité, cartes projets.
- **Semaine** (`/semaine`) — grille de saisie : projets en lignes,
  lundi→dimanche en colonnes, totaux par jour et par ligne. Les flèches haut
  et bas naviguent d'une ligne à l'autre. Une case vide ou à 0 supprime la
  saisie. La grille regroupe par projet et par tâche ; pour une saisie avec
  note détaillée, passe par la fiche projet.
- **Planning** (`/planning`) — Gantt sur 13 semaines glissantes, aligné avec
  la bande de charge, absences à venir listées en dessous.
- **Projets** (`/projects`) — onglets avec compteurs, recherche plein texte,
  filtre par client, et une **corbeille** : supprimer met de côté, la
  suppression définitive demande de retaper le nom du projet.
- **Fiche projet** (`/projects/N`) — cadence et courbe de rythme, argent
  (prix, coûts, marge, facturé, encaissé, reste à facturer), jalons de
  facturation, coûts directs, tâches renommables/archivables/supprimables,
  historique paginé avec **modification** de chaque saisie, et historique de
  périmètre.
- **Facturation** (`/facturation`) — jalons en trois colonnes (à facturer,
  facturé, encaissé), retards en tête, CA facturé par mois.
- **Clients** (`/clients`) — CA par client, part de chacun dans ton activité
  (alerte au-delà de 50 %), et marge par jour réellement passé par client.
- **Comparatif** (`/comparatif`) — rentabilité de tous les projets, temps par
  tâche, et évolution mensuelle (jours travaillés + CA facturé).
- **Absences** (`/absences`) — congés, fériés, indisponibilités.
- **Réglages** (`/reglages`) — jours travaillés, seuils de charge, devise,
  seuil de fiabilité, alerte budget, objectifs de CA, exports et sauvegarde.

## Concepts

**Statuts.** *Provisoire* = vendu mais pas signé : compte dans la charge
seulement si tu l'y inclus, jamais dans le CA réalisé. Puis *confirmé*,
*en pause*, *terminé*.

**Jalons de facturation.** Un projet sans jalon n'apparaît pas dans la page
Facturation. Chaque jalon passe de « à facturer » à « facturé » (avec un
numéro de facture) puis « encaissé ». C'est ce qui alimente le CA mensuel,
les objectifs et le carnet de commandes.

**Coûts directs.** Sous-traitance, licences, déplacements. Ils sont déduits
du prix pour calculer la marge par jour : sans eux, l'indicateur mesure ta
productivité, pas ta rentabilité.

**Historique de périmètre.** Modifier les jours/semaine, la durée ou le prix
d'un projet enregistre l'ancienne valeur avec un motif. C'est ce qui permet
d'analyser ses dépassements après coup, au lieu d'écraser le contrat initial.

**Alertes.** Budget dépassé, cadence en retard, projet qui se termine sous
15 jours, jalon à facturer en retard, facture non encaissée depuis plus de
30 jours, jours en surcharge, jours ouvrés sans saisie.

## Sauvegarder

Réglages → Sauvegarde et export. Trois exports CSV (saisies, projets,
facturation) et une **sauvegarde complète** : une copie du fichier SQLite,
la seule qui restaure absolument tout.

## Tests

```bash
pip install pytest
python3 -m pytest tests/ -q
```

38 tests sur les calculs et la couche données : capacité, rentabilité,
indépendance des jours vis-à-vis du réglage horaire, détection des jours non
saisis, validation des statuts, corbeille, grille hebdo, et migration d'une base créée
par la V1 (données conservées, colonnes ajoutées, relances idempotentes).

## Structure

```
app.py              routes Flask — aucune requête SQL, aucun calcul métier
calculations.py     logique métier pure, sans Flask ni sqlite3
db.py               tout le SQL, migrations automatiques, agrégats
schema.sql          état cible du schéma
templates/          pages Jinja2
static/             CSS (thème blanc) + JS sans framework
tests/              suite pytest
```

Les migrations tournent à chaque démarrage : ajouter une colonne au schéma
ne demande jamais de supprimer la base existante.
