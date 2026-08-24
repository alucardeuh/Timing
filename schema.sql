-- Timing — schéma de base de données
--
-- Convention : toute création de table est idempotente (IF NOT EXISTS), et
-- l'ajout de colonnes sur une base déjà existante est géré par la migration
-- légère de db.py (voir MIGRATIONS). Ce fichier décrit l'état CIBLE du
-- schéma ; db.py se charge d'y amener une base plus ancienne sans perte.

-- Fiches clients. Avant, « client » n'était qu'un texte libre recopié sur
-- chaque projet : une faute de frappe créait un doublon silencieux, et il
-- n'y avait nulle part où noter un contact ou un TJM habituel.
--
-- projects.client conserve le NOM en clair, tenu à jour avec cette table.
-- Cette dénormalisation assumée garde les exports, les regroupements du
-- planning et les agrégats lisibles sans jointure supplémentaire.
CREATE TABLE IF NOT EXISTS clients (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- COLLATE NOCASE : « Alpha SA » et « alpha sa » ne doivent former
    -- qu'une seule fiche. get_client_by_name() compare déjà en NOCASE ;
    -- sans cette collation ICI, la contrainte UNIQUE du SQL ne le savait
    -- pas et aurait laissé les deux coexister au premier appel qui
    -- contournait le contrôle applicatif. Sur une base plus ancienne,
    -- db._ensure_clients_name_nocase() amène la table à cet état.
    name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    contact_name        TEXT,
    email               TEXT,
    phone               TEXT,
    address             TEXT,

    -- Repris automatiquement à la création d'un projet pour ce client.
    default_day_rate    REAL,
    -- Délai de paiement contractuel, en jours. Le prévisionnel préfère le
    -- délai réellement constaté, et se rabat sur celui-ci sans historique.
    payment_terms_days  INTEGER,

    notes               TEXT,
    archived            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    client          TEXT,
    client_id       INTEGER REFERENCES clients(id) ON DELETE SET NULL,

    -- provisional : vendu mais pas signé. N'entre dans la charge que si on
    -- demande explicitement à l'inclure, jamais dans le CA réalisé.
    status          TEXT NOT NULL DEFAULT 'provisional'
                     CHECK (status IN ('provisional', 'confirmed', 'paused', 'completed')),

    days_per_week   REAL NOT NULL,
    duration_value  REAL NOT NULL,
    duration_unit   TEXT NOT NULL DEFAULT 'weeks'
                     CHECK (duration_unit IN ('weeks', 'months')),

    day_rate        REAL,
    price_total     REAL NOT NULL DEFAULT 0,

    -- NULL = utilise le réglage global. Ne sert plus qu'à convertir des
    -- jours en heures pour l'affichage : la consommation réelle est
    -- comptée en % de journée (voir entries.percent_of_day), donc changer
    -- cette valeur ne réécrit plus l'historique.
    hours_per_day   REAL,

    start_date      TEXT NOT NULL,

    -- Jours de la semaine effectivement occupés par ce projet, en index
    -- Python ("0,2" = lundi et mercredi). Vide = réparti sur tous les jours
    -- ouvrés. Sans ce champ, un projet vendu 2 j/semaine affichait 40 % sur
    -- les cinq jours ouvrés : une moyenne, pas un planning.
    weekdays        TEXT,

    color           TEXT NOT NULL DEFAULT '#3E8E82',
    notes           TEXT,

    -- Reste à faire DÉCLARÉ, en jours. NULL = non renseigné, auquel cas
    -- l'app retombe sur le prorata temporel. La cadence comparait le budget
    -- consommé au temps écoulé, ce qui suppose une consommation linéaire :
    -- un projet à 90 % de budget dont il reste trois jours de travail et un
    -- projet à 90 % dont il reste trois semaines s'affichaient pareil.
    remaining_days      REAL,
    remaining_updated_at TEXT,

    -- Corbeille : un projet supprimé est d'abord archivé (réversible).
    -- La suppression définitive n'est possible que depuis l'onglet Corbeille.
    archived        INTEGER NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    entry_date      TEXT NOT NULL,

    -- LA source de vérité de la consommation. 50 = une demi-journée.
    -- Indépendant de tout réglage : c'est ce qui garantit qu'un changement
    -- d'heures/jour ne déforme pas rétroactivement le passé.
    percent_of_day  REAL NOT NULL,

    -- Dérivé, conservé pour l'historique et les exports. Jamais utilisé
    -- pour calculer des jours consommés.
    hours           REAL NOT NULL DEFAULT 0,

    note            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

-- Congés, jours fériés, indisponibilités. Un jour couvert par une absence
-- a une capacité nulle : il ne compte plus comme jour travaillé dans la
-- carte de charge, et n'est jamais signalé comme "jour non saisi".
CREATE TABLE IF NOT EXISTS absences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'conges'
                     CHECK (kind IN ('conges', 'ferie', 'indispo')),
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- Jalons de facturation. La somme des jalons d'un projet devrait couvrir
-- son prix total ; l'écart est affiché sur la fiche plutôt que corrigé
-- d'office (un acompte non encore jalonné est un cas normal).
CREATE TABLE IF NOT EXISTS milestones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    due_date        TEXT,
    status          TEXT NOT NULL DEFAULT 'todo'
                     CHECK (status IN ('todo', 'invoiced', 'paid')),
    invoice_ref     TEXT,
    invoiced_at     TEXT,
    paid_at         TEXT,
    created_at      TEXT NOT NULL
);

-- Coûts directs imputés à un projet (sous-traitance, licence, déplacement).
-- Sans eux, l'indice de rentabilité mesure la productivité, pas la marge.
CREATE TABLE IF NOT EXISTS costs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    cost_date       TEXT,
    -- Un frais refacturé au client s'AJOUTE au prix au lieu d'être déduit
    -- de la marge. Les traiter tous comme des coûts sous-estimait le
    -- revenu des projets avec déplacements ou achats refacturés.
    billable        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

-- Historique de périmètre : chaque modification du volume vendu ou du prix
-- laisse une ligne datée, au lieu d'écraser silencieusement l'ancienne
-- valeur. C'est ce qui permet d'analyser ses dépassements après coup.
CREATE TABLE IF NOT EXISTS scope_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field           TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    note            TEXT,
    changed_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

-- Corbeille universelle. La corbeille des projets protégeait les projets ;
-- supprimer une saisie, un jalon, un coût ou une absence restait définitif,
-- sans confirmation ni retour en arrière. Une ligne supprimée est désormais
-- recopiée ici en JSON avant d'être effacée, restaurable avec son id
-- d'origine, et purgée automatiquement au bout de TRASH_RETENTION_DAYS.
CREATE TABLE IF NOT EXISTS trash (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    label           TEXT NOT NULL,
    payload         TEXT NOT NULL,
    deleted_at      TEXT NOT NULL
);

-- @INDEXES
-- Tout ce qui suit est exécuté APRÈS les migrations.
-- Un index peut porter sur une colonne ajoutée par migration (archived) ;
-- le créer avant que la colonne existe échoue sur une base d'avant la V2.
CREATE INDEX IF NOT EXISTS idx_entries_project ON entries(project_id);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(entry_date);
-- Composite : sert la grille hebdo et les agrégats par projet sur une
-- période, les deux requêtes les plus fréquentes de l'app.
CREATE INDEX IF NOT EXISTS idx_entries_project_date ON entries(project_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
CREATE INDEX IF NOT EXISTS idx_costs_project ON costs(project_id);
CREATE INDEX IF NOT EXISTS idx_absences_dates ON absences(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status, archived);
CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id);
CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name);
CREATE INDEX IF NOT EXISTS idx_trash_deleted ON trash(deleted_at);
