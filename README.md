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

**Un cinquième principe** est arrivé avec le reste à faire déclaré : *une
estimation vaut mieux qu'un prorata, tant qu'elle est fraîche*. Sans reste à
faire renseigné, la cadence compare le budget consommé au temps écoulé, ce
qui suppose que le travail avance au rythme du calendrier. Dès qu'un reste à
faire est déclaré, c'est lui qui pilote la cadence — pendant trois semaines,
au-delà desquelles l'app le signale comme périmé et repasse au prorata. Une
estimation de deux mois pesant autant qu'une estimation du jour serait pire
que pas d'estimation du tout.

**Un quatrième principe** s'y est ajouté : *la charge suit le réel, pas le
contrat*. Un projet non terminé continue d'occuper des journées au-delà de sa
fin prévue, dans la limite réglable de 4 semaines, et le signale
distinctement. Une carte purement contractuelle affichait 0 % de charge
précisément quand un projet en dépassement mangeait les journées à venir.

## Les pages

- **Aujourd'hui** (`/`) — bandeau rouge des projets en difficulté (fin
  dépassée, budget consommé, cadence en retard), alertes à traiter, saisie
  rapide, carte de charge sur 8 semaines, chiffre d'affaires du mois et de
  l'année vs objectifs, carnet de commandes, cartes projets. Uniquement ce
  qui appelle une décision aujourd'hui.
- **Jour** (`/jour`) — une carte par projet, un grand champ de pourcentage,
  boutons 25/50/75/100 %, navigation veille / lendemain. Pensée pour le
  téléphone, là où la grille hebdo et ses neuf colonnes ne passent pas.
- **Semaine** (`/semaine`) — grille de saisie : projets en lignes,
  lundi→dimanche en colonnes, totaux par jour et par ligne. Les flèches haut
  et bas naviguent d'une ligne à l'autre. Une case vide ou à 0 supprime la
  saisie. La grille regroupe par projet et par tâche ; pour une saisie avec
  note détaillée, passe par la fiche projet.
- **Planning** (`/planning`) — un simulateur et deux vues. Le **simulateur**
  ajoute un projet fictif — nom, date de début, jours par semaine, durée —
  et répond « ça tient » ou « ça ne tient pas », en comptant les jours
  libres avant et après et les semaines qui basculent en surcharge. Rien
  n'est écrit en base : répondre à « puis-je prendre ce projet ? » ne doit
  pas coûter une ligne dans le carnet de commandes et les exports. Puis les
  deux vues. **Allocation par semaine** (par
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
  facturé, encaissé), retards en tête, CA facturé par mois, **produit non
  facturé** (le travail déjà fait valorisé au prix vendu, moins ce qui est
  facturé — négatif quand c'est facturé d'avance), et
  **prévisionnel d'encaissement** sur trois mois basé sur le délai de
  paiement réellement constaté par client. Les jalons sont modifiables et
  leur date de facturation antidatable.
- **Clients** (`/clients`) — fiches en cartes (projets, CA, marge par jour,
  délai de paiement), création et édition, fiche détaillée par client avec ses
  projets et sa facturation. Plus la répartition du CA, la part de chacun
  (alerte au-delà de 50 %) et la marge par jour réellement passé.
- **Comparatif** (`/comparatif`) — rentabilité de tous les projets, temps par
  tâche, évolution mensuelle (jours travaillés + CA facturé), et le
  **classement** des projets les plus rentables avec le seuil de rentabilité
  annuel. Ces deux derniers blocs vivaient sur l'accueil : ils relèvent
  d'une revue mensuelle, pas de la question « que dois-je faire
  aujourd'hui », et repoussaient la saisie sous la ligne de flottaison.
- **Corbeille** (`/corbeille`) — saisies, jalons, coûts et absences
  supprimés, restaurables trente jours. Chaque suppression propose son
  annulation immédiate en bas d'écran.
- **Absences** (`/absences`) — congés, fériés, indisponibilités.
- **Réglages** (`/reglages`) — jours travaillés, seuils de charge, devise,
  seuil de fiabilité, alerte budget, objectifs de CA, exports et sauvegarde.

## Concepts

**Jours de la semaine.** Un projet peut déclarer les jours qu'il occupe
réellement (« lundi et mercredi »). La charge s'y concentre alors entièrement.
Sans rien déclarer, elle est **lissée** sur tous les jours ouvrés : un projet
vendu 2 j/semaine apparaît à 40 % du lundi au vendredi — une moyenne honnête,
mais pas un planning. Cocher les jours rend le planning littéral.

Littéral veut dire littéral : déclarer 5 jours vendus sur deux jours de la
semaine affiche 250 % sur ces deux jours, pas 100 %. Un engagement
impossible doit faire réagir la carte, pas être écrêté en silence — c'est le
même parti pris que pour deux projets qui se chevauchent.

**Clients.** Chaque client a une fiche : coordonnées, TJM habituel, délai de
paiement contractuel, notes. Deux façons d'en créer une — depuis la page
Clients (utile pour un prospect sans projet), ou en tapant simplement un nom
inconnu dans le champ « Client » d'un projet, auquel cas la fiche se crée
automatiquement. Le rapprochement ignore la casse, donc « Alpha SA » et
« alpha sa » ne font qu'un.

Le TJM habituel est repris quand tu laisses le champ vide à la création d'un
projet. Renommer une fiche met à jour tous ses projets. La supprimer conserve
les projets et leur nom de client : supprimer un contact ne doit pas effacer
l'historique de facturation qui s'y rattache.

**Tâches.** Optionnelles, elles se créent depuis la fiche d'un projet et
servent à savoir où part le temps *à l'intérieur* du projet.

**Statuts.** *Provisoire* = vendu mais pas signé : compte dans la charge
seulement si tu l'y inclus, jamais dans le CA réalisé. Puis *confirmé*,
*en pause*, *terminé*.

**Jalons de facturation.** Un projet sans jalon n'apparaît pas dans la page
Facturation. Chaque jalon passe de « à facturer » à « facturé » (avec un
numéro de facture) puis « encaissé ». C'est ce qui alimente le CA mensuel,
les objectifs et le carnet de commandes.

**Reste à faire.** Un champ sur la fiche projet, en jours, réajustable à
volonté. Il donne la **terminaison prévue** (consommé + reste) et l'écart au
budget vendu. C'est la seule alerte qui puisse sonner *avant* les faits :
déclarer huit jours de reste sur un projet à qui il en reste trois est un
dépassement acquis, même quand le compteur de budget affiche encore de la
marge. Vider le champ efface l'estimation — « zéro jour restant » et « je ne
sais pas » sont deux affirmations différentes.

**Produit non facturé.** Les jours consommés valorisés au prix vendu par
jour, moins ce qui est déjà facturé. Pour qui facture par jalons, c'est ce
qui dit combien d'argent dort dans du travail livré. Une valeur négative
veut dire facturé d'avance et n'est pas ramenée à zéro.

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

Une **sauvegarde automatique** est prise au premier lancement de chaque
journée, dans `instance/backups/`, avec rotation sur sept jours. Une seule
par jour : relancer l'app dix fois n'écrase pas dix fois l'état du matin,
qui est justement celui qu'on veut pouvoir retrouver. Un disque plein ou un
dossier en lecture seule prive de la sauvegarde du jour, jamais du
démarrage.

Manuellement : Réglages → Sauvegarde et export. Quatre exports CSV (saisies, projets,
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

154 tests sur les calculs, la couche données et les routes : capacité en
jours ouvrés, dépassement, rentabilité et garde-fous de fiabilité, coût de
revient, indépendance des jours vis-à-vis du réglage horaire, détection des
jours non saisis, validation des statuts, corbeille, grille hebdo (dates,
atomicité, lignes d'affichage), jeton CSRF et redirections, prévisionnel
d'encaissement, coûts refacturables, migration d'une base créée par la V1,
non-résurrection d'une fiche client supprimée, borne de fin incluse, prise
en compte des absences dans les projections, une connexion SQLite par
requête, page d'erreur résiliente à une base indisponible, cycle de
couleurs des projets immunisé contre une suppression définitive, collation
insensible à la casse des noms de clients, routes de suppression qui ne
mentent pas sur un id déjà supprimé, déduplication des alertes du
tableau de bord avec le bandeau de projets en difficulté, corbeille
universelle (restauration à l'id d'origine, refus si le projet parent a
disparu, purge à la borne inclusive), reste à faire déclaré (terminaison
prévue, bascule et repli de la cadence, valeur illisible ignorée),
simulation de charge (jamais persistée, comptée même provisoires masqués,
respectueuse des absences, paramètre d'URL absurde sans page 500), produit
non facturé, sauvegarde automatique (une par jour, rotation, cohérence en
WAL), et les garanties d'interface qui ne se voyaient qu'à l'écran : aucune
police chargée depuis le réseau, thème refusant une valeur inconnue,
navigation déclarée une seule fois, feuille d'impression, cellules de charge
atteignables au clavier, palette sans innerHTML.

Chaque test documente en commentaire le bug qu'il empêche de revenir.

## Structure

```
app.py              routes Flask — aucune requête SQL, aucun calcul métier
calculations.py     logique métier pure, sans Flask ni sqlite3
db.py               tout le SQL, migrations automatiques, agrégats
schema.sql          état cible du schéma
templates/          pages Jinja2
static/             CSS (thèmes clair et sombre), polices woff2
                    auto-hébergées, JS sans framework, manifeste PWA
tests/              suite pytest
```

Les migrations tournent à chaque démarrage : ajouter une colonne au schéma
ne demande jamais de supprimer la base existante.

Une connexion SQLite est ouverte par requête, pas par fonction appelée :
`db.get_db()` la partage via `flask.g` pour toute la durée de la requête,
fermée au teardown. Hors d'une requête (tests, scripts), chaque appel garde
une connexion jetable — le module ne dépend de Flask que pour ça, et
fonctionne toujours sans.

## Clavier

Tout se fait au clavier, hors des champs de saisie.

| Touche | Effet |
|---|---|
| `/` | palette : chercher un projet, un client ou une page |
| `g` puis `a` `j` `s` `p` `f` `c` `r` | aujourd'hui, jour, semaine, planning, facturation, clients, projets |
| `n` | nouveau projet |
| `?` | liste des raccourcis |
| `↑` `↓` | dans la grille hebdo, changer de ligne à colonne constante |
| `←` `→` | dans la carte de charge, passer d'un jour à l'autre |
| `Échap` | fermer la palette |

## Thème et impression

Le thème suit le système par défaut ; Réglages permet de forcer clair ou
sombre. Le réglage vit en base et non dans le navigateur : il suit les
données, pas la machine.

Les couleurs de charge sont réaccordées pour le fond sombre, pas seulement
assombries. Les fonds pastel d'origine y viraient au gris, et les trois
niveaux — léger, chargé, tempête — cessaient de se distinguer, ce qui est
précisément la seule chose que la carte doit faire.

Chaque page s'imprime en A4 paysage sans le rail, les boutons ni les liens
de filtre, et les grilles à défilement horizontal redeviennent entières :
c'est ce qui transforme le planning ou le comparatif en support de revue.
