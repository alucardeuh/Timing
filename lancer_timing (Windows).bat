@echo off
REM Double-clique sur ce fichier pour lancer Timing sous Windows.
cd /d "%~dp0"
set PORT=5062

if not exist venv (
    echo Creation de l'environnement virtuel (premiere fois seulement)...
    python -m venv venv
)

call venv\Scripts\activate.bat
echo Verification des dependances...
pip install -q -r requirements.txt

echo.
echo Demarrage de Timing sur http://127.0.0.1:%PORT%
echo (laisse cette fenetre ouverte tant que tu utilises l'app)
echo.

start "" http://127.0.0.1:%PORT%
python app.py
pause
