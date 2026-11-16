# StockPro Moto-Auto — Lubumbashi

Application web locale de gestion des pièces de rechange (motos, vélos, voitures et bus), construite avec Flask et SQLite.

## Fonctionnalités livrées dans cette première version
- Connexion avec mot de passe haché et session.
- Tableau de bord avec statistiques calculées depuis la base.
- Gestion des pièces : ajout, modification, suppression protégée lorsqu'un historique existe, recherche et export CSV de l'inventaire.
- Entrées et sorties de stock avec contrôle des quantités et historique d'inventaire (250 dernières opérations); les ajustements depuis une fiche article sont également tracés.
- Clients : création, consultation, modification et suppression protégée.
- Comptes utilisateurs : création par l'administrateur, rôles Administrateur/Employé et suppression protégée.
- Achats & réceptions : enregistrement des achats par fournisseur, coût d'achat, mise à jour du stock, historique et mouvements automatiques.
- Règlements fournisseurs : acomptes, paiements partiels ou complets, historique par achat, solde restant et blocage des règlements supérieurs au montant dû.
- Dépenses & charges : enregistrement par catégorie, description, montant, devise USD/CDF, mode de paiement et note; consultation des totaux séparés par devise et suppression d'une saisie erronée.
- Tableau de bord financier : ventes facturées, encaissements, créances clients, achats, règlements fournisseurs, dettes et dépenses, avec calculs séparés en USD et CDF. Le solde net présenté est un indicateur des flux saisis, pas un rapprochement bancaire.
- Ventes multi-articles : contrôle du stock, liaison aux clients enregistrés, facture, historique et décrémentation atomique.
- Paiements de factures : acomptes et paiements partiels, modes Espèces/Mobile Money/Banque/Carte, historique des règlements, solde restant et statut À crédit/Partiellement payée/Payée. Les paiements supérieurs au solde sont refusés.
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

**Important :** modifier le mot de passe administrateur avant usage réel. Pour définir le mot de passe initial avant le premier lancement, définir la variable `ADMIN_PASSWORD`. Définir également une longue valeur aléatoire pour `SECRET_KEY`. En déploiement HTTPS, définir `COOKIE_SECURE=1` pour activer les cookies de session sécurisés (laisser `0` en local HTTP). Les cookies sont configurés HttpOnly et SameSite=Lax, avec une session de 8 heures. La base SQLite est créée automatiquement à côté de app.py.

## Limites connues à configurer avant production
- La devise USD/CDF est enregistrée sur chaque article/vente; les prix et quantités négatifs ainsi que les devises non prises en charge sont refusés. Aucune conversion automatique n'est appliquée : saisir les prix dans la devise choisie et éviter de mélanger les devises dans un même panier.
- Le scanner QR utilise BarcodeDetector sur les navigateurs compatibles (Chrome/Edge récents) et nécessite l'autorisation caméra; sinon rechercher la référence manuellement.
- Les fournisseurs disposent d'une fiche simple; les rapports restent synthétiques. Les rôles Administrateur/Employé couvrent la gestion de base des comptes.
- Le compte initial est un compte de démarrage; l'application n'est pas encore configurée pour une exposition publique sur Internet.
- Sauvegarder régulièrement `stockpro.db`. Utiliser HTTPS, une clé secrète forte, un mot de passe unique et un serveur WSGI avant toute mise en production.

## Vérification
L'endpoint `/health` renvoie un statut JSON. L'application initialise automatiquement ses tables et un petit jeu de démonstration au premier lancement uniquement.
