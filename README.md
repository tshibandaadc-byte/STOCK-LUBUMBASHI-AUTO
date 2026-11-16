# StockPro Moto-Auto — Lubumbashi

Application web locale de gestion des pièces de rechange (motos, vélos, voitures et bus), construite avec Flask et SQLite.

## Fonctionnalités livrées dans cette première version
- Connexion avec mot de passe haché et session.
- Tableau de bord avec statistiques calculées depuis la base.
- Gestion des pièces : ajout, modification, suppression protégée et recherche.
- Entrées et sorties de stock avec contrôle des quantités.
- Clients : création et consultation.
- Ventes multi-articles : contrôle du stock, facture, historique et décrémentation atomique.
- Alertes lorsque quantité <= seuil minimum.
- Interface responsive reprenant la maquette StockPro (bleu marine, jaune, cartes colorées et menu latéral).

## Installation Windows / VS Code
1. Installer Python 3.10 ou plus récent depuis https://www.python.org/downloads/ et cocher **Add Python to PATH**.
2. Ouvrir ce dossier dans VS Code, puis le terminal PowerShell.
3. Exécuter :
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   python app.py
   ```
4. Ouvrir http://127.0.0.1:5000

Si PowerShell bloque l'activation : `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, puis réactiver l'environnement.

## Première connexion
- Utilisateur : `admin`
- Mot de passe initial : `Admin@12345`

**Important :** modifier le mot de passe administrateur avant usage réel. Pour définir le mot de passe initial avant le premier lancement, définir la variable `ADMIN_PASSWORD`. Définir également une longue valeur aléatoire pour `SECRET_KEY`. La base SQLite est créée automatiquement à côté de app.py.

## Limites connues à configurer avant production
- La devise USD/CDF est enregistrée sur chaque article/vente, mais aucune conversion automatique n'est appliquée : saisir les prix dans la devise choisie et éviter de mélanger les devises dans un même panier.
- Le scanner QR utilise BarcodeDetector sur les navigateurs compatibles (Chrome/Edge récents) et nécessite l'autorisation caméra; sinon rechercher la référence manuellement.\n- Les fournisseurs disposent d'une fiche simple; les rapports restent synthétiques et l'application ne propose pas encore de gestion multi-utilisateurs avancée.
- Le compte initial est un compte de démarrage; l'application n'est pas encore configurée pour une exposition publique sur Internet.
- Sauvegarder régulièrement `stockpro.db`. Utiliser HTTPS, une clé secrète forte, un mot de passe unique et un serveur WSGI avant toute mise en production.

## Vérification
L'endpoint `/health` renvoie un statut JSON. L'application initialise automatiquement ses tables et un petit jeu de démonstration au premier lancement uniquement.
