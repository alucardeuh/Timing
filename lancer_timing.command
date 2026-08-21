#!/bin/bash
# Double-clique sur ce fichier pour lancer Timing.
# À placer dans le même dossier que app.py (racine du repo Timing).

cd "$(dirname "$0")"

PORT=5062   # PAS 5060 : les navigateurs bloquent ce port (voir README).

if [ ! -d "venv" ]; then
    echo "Création de l'environnement virtuel (première fois seulement)..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Vérification des dépendances..."
pip install -q -r requirements.txt

echo ""
echo "Démarrage de Timing sur http://127.0.0.1:$PORT"
echo "(laisse cette fenêtre ouverte tant que tu utilises l'app)"
echo ""

python3 app.py &
SERVER_PID=$!

# On attend que le serveur réponde vraiment avant d'ouvrir le navigateur.
# Un simple "sleep 2" ouvrait une page morte au premier lancement, quand
# la création du venv et l'installation des dépendances prennent 30 s.
PRET=0
for i in $(seq 1 60); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        PRET=1
        break
    fi
    sleep 0.5
done

if [ "$PRET" = "1" ]; then
    open "http://127.0.0.1:$PORT"
    echo "Timing est ouvert dans ton navigateur."
else
    echo "Le serveur met plus de temps que prévu — ouvre manuellement"
    echo "http://127.0.0.1:$PORT dans quelques secondes."
    echo "S'il y a une erreur, elle s'affiche ci-dessus."
fi

echo ""
echo "Pour tout arrêter : ferme cette fenêtre, ou Ctrl+C."
wait $SERVER_PID

echo ""
read -p "Le serveur s'est arrêté. Appuie sur Entrée pour fermer cette fenêtre..."
