#!/bin/bash
# Double-clique sur ce fichier pour lancer Timing.
# À placer dans le même dossier que app.py (racine du repo Timing).

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Création de l'environnement virtuel (première fois seulement)..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Installation des dépendances..."
pip install -q -r requirements.txt

echo ""
echo "Démarrage de Timing sur http://127.0.0.1:5060"
echo "(laisse cette fenêtre ouverte tant que tu utilises l'app — Ctrl+C ou ferme la fenêtre pour arrêter)"
echo ""

# Ouvre le navigateur automatiquement une fois le serveur prêt
( sleep 2 && open http://127.0.0.1:5060 ) &

python3 app.py

echo ""
read -p "Le serveur s'est arrêté. Appuie sur Entrée pour fermer cette fenêtre..."
