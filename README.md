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
défaut), ni l'indice **ni aucun taux réel** ne sont affichés — ils partagent
le même dénominateur, donc le même défaut. L'**indice projeté** extrapole le
rythme observé, mais reste muet tant que moins de 10 % du temps est écoulé
ou qu'il y a moins de trois saisies : divisé par un temps écoulé quasi nul,
il était encore plus volatil que celui qu'il remplace.

**Un quatrième principe** s'y est ajouté : *la charge suit le réel, pas le
contrat*. Un projet non terminé continue d'occuper des journées au-delà de sa
fin prévue, dans la limite réglable de 4 semaines, et le signale
distinctement. Une carte purement contractuelle affichait 0 % de charge
précisément quand un projet en dépassement mangeait les journées à venir.

## Les pages

- **Aujourd'hui** (`/`) — bandeau rouge des projets en difficulté (fin
  dépassée, budget consommé, cadence en retard), alertes à traiter, saisie rapide, carte de charge
  sur 8 semaines, chiffre d'affaires du mois et de l'année vs objectifs,
  carnet de commandes, classement de rentabilité, cartes projets.
- **Jour** (`/jour`) — une carte par projet, un grand champ de pourcentage,
  boutons 25/50/75/100 %, navigation veille / lendemain. Pensée pour le
  téléphone, là où la grille hebdo et ses neuf colonnes ne passent pas.
- **Semaine** (`/semaine`) — grille de saisie : projets en lignes,
  lundi→dimanche en colonnes, totaux par jour et par ligne. Les flèches haut
  et bas naviguent d'une ligne à l'autre. Une case vide ou à 0 supprime la
  saisie. La grille regroupe par projet et par tâche ; pour une saisie avec
  note détaillée, passe par la fiche projet.
- **Planning** (`/planning`) — deux vues. **Allocation par semaine** (par
  défaut) : une grille projets × 13 semaines où chaque case donne le nombre de
  jours occupés, regroupée par client, avec en bas la comparaison
  « engagé / disponible » et les jours libres restants. C'est elle qui dit si
  tu peux accepter un projet de plus. **Calendrier** : le Gantt jour par jour
  aligné avec la bande de charge.
- **Projets** (`/projects`) — onglets avec compteurs, recherche plein texte,
  filtre par client, et une **corbeille** : supprimer met de côté, la
  suppression définitive demande de retaper le nom du projet.
- **Fiche projet** (`/projects/N`) — cadence et courbe de rythme, argent
  (prix, coûts, marge, facturé, encaissé, reste à facturer), jalons de
  facturation, coûts directs, tâches renommables/archivables/supprimables,
  historique paginé avec **modification** de chaque saisie, et historique de
  périmètre.
- **Facturation** (`/facturation`) — jalons en trois colonnes (à facturer,
  facturé, encaissé), retards en tête, CA facturé par mois, et
  **prévisionnel d'encaissement** sur trois mois basé sur le délai de
  paiement réellement constaté par client. Les jalons sont modifiables et
  leur date de facturation antidatable.
- **Clients** (`/clients`) — CA par client, part de chacun dans ton activité
  (alerte au-delà de 50 %), et marge par jour réellement passé par client.
- **Comparatif** (`/comparatif`) — rentabilité de tous les projets, temps par
  tâche, et évolution mensuelle (jours travaillés + CA facturé).
- **Absences** (`/absences`) — congés, fériés, indisponibilités.
- **Réglages** (`/reglages`) — jours travaillés, seuils de charge, devise,
  seuil de fiabilité, alerte budget, objectifs de CA, exports et sauvegarde.

## Concepts

**Jours de la semaine.** Un projet peut déclarer les jours qu'il occupe
réellement (« lundi et mercredi »). La charge s'y concentre alors à 100 %.
Sans rien déclarer, elle est **lissée** sur tous les jours ouvrés : un projet
vendu 2 j/semaine apparaît à 40 % du lundi au vendredi — une moyenne honnête,
mais pas un planning. Cocher les jours rend le planning littéral.

**Clients.** Il n'y a pas de fiche client à créer : un client existe dès qu'un
projet porte son nom, dans le champ « Client » du formulaire projet. La page
Clients regroupe ensuite tous les projets portant le même nom.

**Tâches.** Optionnelles, elles se créent depuis la fiche d'un projet et
servent à savoir où part le temps *à l'intérieur* du projet.

**Statuts.** *Provisoire* = vendu mais pas signé : compte dans la charge
seulement si tu l'y inclus, jamais dans le CA réalisé. Puis *confirmé*,
*en pause*, *terminé*.

**Jalons de facturation.** Un projet sans jalon n'apparaît pas dans la page
Facturation. Chaque jalon passe de « à facturer » à « facturé » (avec un
numéro de facture) puis « encaissé ». C'est ce qui alimente le CA mensuel,
les objectifs et le carnet de commandes.

**Coûts directs.** Sous-traitance, licences, déplacements. Déduits du prix
par défaut ; cochés « refacturé au client », ils s'y **ajoutent** à la place.

**Coût de revient.** Charges fixes annuelles ÷ jours facturables visés = ce
que coûte une journée de ton temps. Tant que ce réglage est vide, la fiche
projet affiche « net de coûts directs » et non « marge » : le premier ignore
le coût de ton propre temps et ne dit donc pas si tu gagnes de l'argent,
seulement si tu tiens ton budget de jours. Renseigné, il débloque la marge
réelle par projet et par client, ainsi que le seuil de rentabilité annuel sur
l'accueil.

**Historique de périmètre.** Modifier les jours/semaine, la durée ou le prix
d'un projet enregistre l'ancienne valeur avec un motif. C'est ce qui permet
d'analyser ses dépassements après coup, au lieu d'écraser le contrat initial.

**Alertes.** Budget dépassé, cadence en retard, projet prolongé au-delà de sa
fin prévue, projet qui se termine sous 15 jours, jalon à facturer en retard,
facture non encaissée depuis plus de 30 jours, jours en surcharge, jours
ouvrés sans saisie — cette dernière se clôt en un clic par « rien fait ce
jour-là », qui déclare une indisponibilité.

**Cadence.** Quatre niveaux sur une échelle symétrique : en avance
(delta < −10), dans les clous (−10 à 10), tendu (10 à 25), en retard (> 25),
où delta est l'écart entre budget consommé et temps écoulé. Plus un état
« pas commencé » distinct.

## Sauvegarder

Réglages → Sauvegarde et export. Quatre exports CSV (saisies, projets,
facturation, à facturer) et une **sauvegarde complète**, produite par
`sqlite3.backup()` : en mode WAL, copier le fichier principal seul aurait
donné une sauvegarde en retard sur la base réelle.

L'export « à facturer » est volontairement plat et complet. C'est le format
qui survivra à la facturation électronique obligatoire (réception au
1er septembre 2026, émission au format structuré pour les TPE au
1er septembre 2027) — un générateur de PDF maison serait hors-jeu d'ici là.

## Tests

```bash
pip install pytest
python3 -m pytest tests/ -q
```

73 tests sur les calculs et la couche données : capacité en jours ouvrés,
dépassement, rentabilité et garde-fous de fiabilité, coût de revient,
indépendance des jours vis-à-vis du réglage horaire, détection des jours non
saisis, validation des statuts, corbeille, grille hebdo (dates, atomicité,
lignes d'affichage), jeton CSRF et redirections, prévisionnel d'encaissement,
coûts refacturables, et migration d'une base créée par la V1.

Chaque test documente en commentaire le bug qu'il empêche de revenir.

## Structure

```
app.py              routes Flask — aucune requête SQL, aucun calcul métier
calculations.py     logique métier pure, sans Flask ni sqlite3
db.py               tout le SQL, migrations automatiques, agrégats
schema.sql          état cible du schéma
templates/          pages Jinja2
static/             CSS (thème blanc), JS sans framework, manifeste PWA
tests/              suite pytest
```

Les migrations tournent à chaque démarrage : ajouter une colonne au schéma
ne demande jamais de supprimer la base existante.
