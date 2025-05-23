#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - Version simplifiée sans pandas pour éviter les problèmes de dépendances
Support CSV uniquement mais plus stable
"""

from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import requests
import csv
import io
import re
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'webhook-ovh-secret-key'

# Configuration Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '7822148813:AAEhWJWToLUY5heVP1G_yqM1Io-vmAMlbLg')
CHAT_ID = os.environ.get('CHAT_ID', '-1002652961145')

# Base de données clients en mémoire
clients_database = {}
upload_stats = {
    "total_clients": 0,
    "last_upload": None,
    "filename": None,
    "banks_detected": 0,
    "detection_rate": 0
}

class BankDetector:
    """Classe pour détecter le nom de la banque à partir de l'IBAN français"""
    
    def __init__(self):
        self.bank_codes = {
            '30002': 'Crédit Lyonnais (LCL)',
            '30003': 'Crédit Agricole',
            '30004': 'BNP Paribas',
            '30006': 'Société Générale',
            '20041': 'Banque Populaire',
            '42559': 'Crédit Mutuel',
            '10278': 'Crédit Mutuel Arkéa',
            '16958': 'La Banque Postale',
            '20817': 'HSBC France',
            '30056': 'Caisse d\'Épargne',
            '16967': 'Hello Bank (BNP Paribas)',
            '18206': 'Fortuneo',
            '19138': 'BforBank (Crédit Agricole)',
            '20395': 'ING Direct',
            '16586': 'Revolut',
            '14437': 'N26',
            '17515': 'CIC',
            '30027': 'Crédit du Nord',
            '13135': 'Crédit Coopératif',
            '27052': 'BRED',
            '30788': 'Natixis',
            '18327': 'Axa Banque'
        }
    
    def extract_bank_code(self, iban):
        """Extrait le code banque de l'IBAN français"""
        if not iban or not isinstance(iban, str):
            return None
            
        clean_iban = re.sub(r'\s+', '', iban.upper())
        
        if not re.match(r'^FR\d{2}\d{10,}', clean_iban):
            return None
            
        try:
            bank_code = clean_iban[4:9]
            return bank_code if bank_code.isdigit() else None
        except IndexError:
            return None
    
    def detect_bank(self, iban):
        """Détecte le nom de la banque à partir de l'IBAN"""
        bank_code = self.extract_bank_code(iban)
        
        if not bank_code:
            return None, None
            
        if bank_code in self.bank_codes:
            return self.bank_codes[bank_code], bank_code
            
        for code, name in self.bank_codes.items():
            if bank_code.startswith(code[:3]):
                return f"{name} (code approx.)", bank_code
                
        return f"Banque inconnue (code: {bank_code})", bank_code

# Instance globale du détecteur de banques
bank_detector = BankDetector()

def detect_and_add_bank_info(client_data):
    """Détecte automatiquement la banque à partir de l'IBAN"""
    iban = client_data.get('iban', '')
    
    if iban and iban != 'N/A':
        bank_name, bank_code = bank_detector.detect_bank(iban)
        
        if bank_name:
            client_data['banque_detectee'] = bank_name
            client_data['code_banque'] = bank_code or ''
            
            if not client_data.get('banque') or client_data.get('banque') == 'N/A':
                client_data['banque'] = bank_name
        else:
            client_data['banque_detectee'] = 'Non détectée'
            client_data['code_banque'] = ''
    else:
        client_data['banque_detectee'] = 'Pas d\'IBAN'
        client_data['code_banque'] = ''
    
    return client_data

def load_clients_from_csv(file_content):
    """Charge les clients depuis un contenu CSV"""
    global clients_database, upload_stats
    
    clients_database = {}
    banks_detected = 0
    
    try:
        csv_reader = csv.DictReader(io.StringIO(file_content))
        
        for row in csv_reader:
            # Normalisation des clés
            normalized_row = {}
            for key, value in row.items():
                if key:
                    normalized_row[key.lower().strip()] = str(value).strip() if value else ""
            
            # Recherche colonne téléphone
            telephone = None
            tel_columns = ['telephone', 'tel', 'phone', 'numero', 'number', 'mobile', 'n° mobile']
            for tel_key in tel_columns:
                if tel_key in normalized_row and normalized_row[tel_key]:
                    telephone = normalized_row[tel_key]
                    break
            
            if not telephone:
                continue
                
            # Normalisation du numéro
            telephone = telephone.replace(' ', '').replace('.', '').replace('-', '')
            if telephone.startswith('+33'):
                telephone = '0' + telephone[3:]
            elif telephone.startswith('33') and len(telephone) > 10:
                telephone = '0' + telephone[2:]
            
            if len(telephone) >= 10 and telephone.startswith('0'):
                client_data = {
                    "nom": normalized_row.get('nom', ''),
                    "prenom": normalized_row.get('prenom', ''),
                    "email": normalized_row.get('email', ''),
                    "entreprise": normalized_row.get('entreprise', ''),
                    "telephone": telephone,
                    "adresse": normalized_row.get('adresse', ''),
                    "ville": normalized_row.get('ville', ''),
                    "code_postal": normalized_row.get('code_postal', ''),
                    "banque": normalized_row.get('banque', ''),
                    "swift": normalized_row.get('swift', ''),
                    "iban": normalized_row.get('iban', ''),
                    "sexe": normalized_row.get('sexe', ''),
                    "date_naissance": normalized_row.get('date_naissance', 'Non renseigné'),
                    "lieu_naissance": normalized_row.get('lieu_naissance', 'Non renseigné'),
                    "profession": normalized_row.get('profession', ''),
                    "nationalite": normalized_row.get('nationalite', ''),
                    "situation_familiale": normalized_row.get('situation_familiale', ''),
                    "statut": normalized_row.get('statut', 'Prospect'),
                    "date_upload": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "nb_appels": 0,
                    "dernier_appel": None,
                    "notes": ""
                }
                
                # Détection banque
                client_data = detect_and_add_bank_info(client_data)
                
                if client_data.get('banque_detectee') and client_data['banque_detectee'] not in ['Non détectée', 'Pas d\'IBAN']:
                    banks_detected += 1
                
                clients_database[telephone] = client_data
        
        total_clients = len(clients_database)
        detection_rate = (banks_detected / total_clients * 100) if total_clients > 0 else 0
        
        upload_stats["total_clients"] = total_clients
        upload_stats["banks_detected"] = banks_detected
        upload_stats["detection_rate"] = round(detection_rate, 1)
        upload_stats["last_upload"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        print(f"✅ {total_clients} clients chargés depuis CSV")
        print(f"🏦 {banks_detected} banques détectées ({detection_rate:.1f}%)")
        
        return total_clients
        
    except Exception as e:
        print(f"Erreur lecture CSV: {str(e)}")
        raise ValueError(f"Erreur lecture CSV: {str(e)}")

def get_client_info(phone_number):
    """Récupère les infos client depuis la base chargée"""
    # Normalisation du numéro entrant
    normalized_number = phone_number.replace(' ', '').replace('.', '').replace('-', '')
    if normalized_number.startswith('+33'):
        normalized_number = '0' + normalized_number[3:]
    elif normalized_number.startswith('33') and len(normalized_number) > 10:
        normalized_number = '0' + normalized_number[2:]
    
    # Recherche exacte
    if normalized_number in clients_database:
        client = clients_database[normalized_number].copy()
        clients_database[normalized_number]["nb_appels"] += 1
        clients_database[normalized_number]["dernier_appel"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        return client
    
    # Recherche partielle
    if len(normalized_number) >= 9:
        suffix = normalized_number[-9:]
        for tel, client in clients_database.items():
            if tel.endswith(suffix):
                client_copy = client.copy()
                clients_database[tel]["nb_appels"] += 1
                clients_database[tel]["dernier_appel"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                return client_copy
    
    # Client inconnu
    return {
        "nom": "INCONNU",
        "prenom": "CLIENT",
        "email": "N/A",
        "entreprise": "N/A", 
        "adresse": "N/A",
        "ville": "N/A",
        "code_postal": "N/A",
        "telephone": phone_number,
        "banque": "N/A",
        "banque_detectee": "N/A",
        "code_banque": "",
        "swift": "N/A",
        "iban": "N/A",
        "sexe": "N/A",
        "date_naissance": "Non renseigné",
        "lieu_naissance": "Non renseigné",
        "profession": "N/A",
        "nationalite": "N/A",
        "situation_familiale": "N/A",
        "statut": "Non référencé",
        "date_upload": "N/A",
        "nb_appels": 0,
        "dernier_appel": None,
        "notes": ""
    }

def send_telegram_message(message):
    """Envoie un message vers Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, data=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Erreur Telegram: {str(e)}")
        return None

def format_client_message(client_info, context="appel"):
    """Formate un message client pour Telegram"""
    banque_detectee = client_info.get('banque_detectee', 'N/A')
    bank_emoji = "🏦✅" if banque_detectee and banque_detectee not in ['N/A', 'Non détectée', 'Pas d\'IBAN'] else "🏦❓"
    
    if context == "appel":
        emoji_statut = "📞" if client_info['statut'] != "Non référencé" else "❓"
        
        return f"""
{emoji_statut} <b>APPEL ENTRANT</b>
📞 Numéro: <code>{client_info['telephone']}</code>
🕐 Heure: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}

👤 <b>IDENTITÉ</b>
▪️ Nom: <b>{client_info['nom']}</b>
▪️ Prénom: <b>{client_info['prenom']}</b>
👥 Sexe: {client_info.get('sexe', 'N/A')}
🎂 Date de naissance: {client_info.get('date_naissance', 'N/A')}

🏢 <b>PROFESSIONNEL</b>
▪️ Entreprise: {client_info['entreprise']}
▪️ Profession: {client_info.get('profession', 'N/A')}
▪️ Email: {client_info['email']}

🏠 <b>COORDONNÉES</b>
▪️ Adresse: {client_info['adresse']}
▪️ Ville: {client_info['ville']} {client_info['code_postal']}

{bank_emoji} <b>INFORMATIONS BANCAIRES</b>
▪️ Banque détectée: <b>{banque_detectee}</b>
▪️ Code banque: <code>{client_info.get('code_banque', 'N/A')}</code>
▪️ IBAN: <code>{client_info.get('iban', 'N/A')}</code>

📊 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Nb appels: {client_info['nb_appels']}
▪️ Dernier appel: {client_info['dernier_appel'] or 'Premier appel'}
        """
    else:
        return f"""
📋 <b>RÉSULTAT TROUVÉ :</b>

👤 <b>IDENTITÉ</b>
🙋 Nom : <b>{client_info['nom']}</b>
👤 Prénom : <b>{client_info['prenom']}</b>
📧 Email : {client_info['email']}
📞 Téléphone : <code>{client_info['telephone']}</code>

🏠 <b>ADRESSE</b>
▪️ Adresse : {client_info['adresse']}
🏘️ Ville : {client_info['ville']} {client_info['code_postal']}

{bank_emoji} <b>INFORMATIONS BANCAIRES</b>
🏛️ Banque détectée : <b>{banque_detectee}</b>
🔢 Code banque : <code>{client_info.get('code_banque', 'N/A')}</code>
🏦 IBAN : <code>{client_info.get('iban', 'N/A')}</code>

💼 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Nb appels: {client_info['nb_appels']}
        """

def process_telegram_command(message_text, chat_id):
    """Traite les commandes Telegram reçues"""
    try:
        if message_text.startswith('/numero '):
            phone_number = message_text.replace('/numero ', '').strip()
            client_info = get_client_info(phone_number)
            response_message = format_client_message(client_info, context="recherche")
            send_telegram_message(response_message)
            return {"status": "command_processed", "command": "numero", "phone": phone_number}
            
        elif message_text.startswith('/stats'):
            stats_message = f"""
📊 <b>STATISTIQUES CAMPAGNE</b>

👥 Clients total: {upload_stats['total_clients']}
📁 Dernier upload: {upload_stats['last_upload'] or 'Aucun'}
📋 Fichier: {upload_stats['filename'] or 'Aucun'}

🏦 <b>DÉTECTION BANQUES</b>
▪️ Banques détectées: {upload_stats.get('banks_detected', 0)} ({upload_stats.get('detection_rate', 0)}%)

📞 <b>APPELS DU JOUR</b>
▪️ Clients appelants: {len([c for c in clients_database.values() if c['dernier_appel'] and c['dernier_appel'].startswith(datetime.now().strftime('%d/%m/%Y'))])}
▪️ Nouveaux contacts: {len([c for c in clients_database.values() if c['nb_appels'] == 0])}
            """
            send_telegram_message(stats_message)
            return {"status": "stats_sent"}
            
        elif message_text.startswith('/help'):
            help_message = """
🤖 <b>COMMANDES DISPONIBLES</b>

📞 <code>/numero 0123456789</code>
   → Affiche la fiche client complète avec banque détectée

📊 <code>/stats</code>
   → Statistiques de la campagne et détection banques

🆘 <code>/help</code>
   → Affiche cette aide

✅ <b>Le bot reçoit automatiquement:</b>
▪️ Les appels entrants OVH
▪️ Les notifications en temps réel
▪️ La détection automatique des banques par IBAN
            """
            send_telegram_message(help_message)
            return {"status": "help_sent"}
            
        else:
            return {"status": "unknown_command"}
            
    except Exception as e:
        print(f"❌ Erreur commande Telegram: {str(e)}")
        return {"error": str(e)}

@app.route('/webhook/ovh', methods=['POST', 'GET'])
def ovh_webhook():
    """Webhook pour recevoir les appels OVH"""
    try:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        if request.method == 'GET':
            caller_number = request.args.get('caller', 'Inconnu')
            called_number = request.args.get('callee', 'Inconnu') 
            event_type = request.args.get('type', 'unknown')
            call_status = f"CGI-{event_type}"
            
            print(f"🔔 [{timestamp}] Appel CGI OVH:")
            print(f"📞 Appelant: {caller_number}")
        else:
            data = request.get_json() or {}
            caller_number = data.get('callerIdNumber', request.args.get('caller', 'Inconnu'))
            call_status = data.get('status', 'incoming')
            
            print(f"🔔 [{timestamp}] Appel JSON:")
        
        # Récupération fiche client
        client_info = get_client_info(caller_number)
        
        # Message Telegram
        telegram_message = format_client_message(client_info, context="appel")
        telegram_message += f"\n📊 Statut appel: {call_status}"
        
        # Envoi vers Telegram
        telegram_result = send_telegram_message(telegram_message)
        
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "caller": caller_number,
            "method": request.method,
            "telegram_sent": telegram_result is not None,
            "client": f"{client_info['prenom']} {client_info['nom']}",
            "bank_detected": client_info.get('banque_detectee', 'N/A')
        })
        
    except Exception as e:
        print(f"❌ Erreur webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    """Webhook pour recevoir les commandes Telegram"""
    try:
        data = request.get_json()
        
        if 'message' in data and 'text' in data['message']:
            message_text = data['message']['text']
            chat_id = data['message']['chat']['id']
            
            result = process_telegram_command(message_text, chat_id)
            
            return jsonify({
                "status": "success",
                "command_result": result,
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })
        
        return jsonify({"status": "no_text_message"})
        
    except Exception as e:
        print(f"❌ Erreur webhook Telegram: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Webhook OVH-Telegram avec Détection Banques</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: #e3f2fd; padding: 20px; border-radius: 8px; text-align: center; }
        .stat-card.bank { background: #e8f5e8; }
        .upload-section { background: #f0f4f8; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; margin: 5px; }
        .btn:hover { background: #1976D2; }
        .btn-success { background: #4CAF50; }
        .success { color: #4CAF50; font-weight: bold; }
        .info-box { background: #e8f5e8; padding: 15px; border-radius: 8px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram 🏦</h1>
            <p class="success">✅ Support CSV - Détection automatique des banques</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>👥 Clients chargés</h3>
                <h2>{{ total_clients }}</h2>
            </div>
            <div class="stat-card bank">
                <h3>🏦 Banques détectées</h3>
                <h2>{{ banks_detected }}</h2>
                <p>{{ detection_rate }}% détection</p>
            </div>
            <div class="stat-card">
                <h3>📁 Dernier upload</h3>
                <p>{{ last_upload or 'Aucun' }}</p>
            </div>
            <div class="stat-card">
                <h3>📋 Fichier actuel</h3>
                <p>{{ filename or 'Aucun' }}</p>
            </div>
        </div>

        <div class="upload-section">
            <h2>📂 Upload fichier clients (CSV)</h2>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <div class="info-box">
                    <p><strong>📋 Format supporté:</strong> CSV (.csv)</p>
                    <p><strong>🔥 Colonne obligatoire:</strong> téléphone (telephone, N° Mobile, etc.)</p>
                    <p><strong>🏦 Détection banque:</strong> IBAN analysé automatiquement</p>
                    <p><strong>✨ Colonnes supportées:</strong> nom, prenom, email, adresse, ville, iban, etc.</p>
                </div>
                <input type="file" name="file" accept=".csv" required style="margin: 10px 0;">
                <br>
                <button type="submit" class="btn btn-success">📁 Charger fichier CSV</button>
            </form>
        </div>

        <h2>📱 Commandes Telegram</h2>
        <ul>
            <li><code>/numero 0123456789</code> - Fiche client avec banque détectée</li>
            <li><code>/stats</code> - Statistiques campagne</li>
            <li><code>/help</code> - Aide</li>
        </ul>

        <div class="info-box">
            <h3>🎯 Fonctionnement :</h3>
            <ol>
                <li>📂 Uploadez votre fichier CSV</li>
                <li>🏦 Détection automatique des banques via IBAN</li>
                <li>📞 Chaque appel affiche la fiche client dans Telegram</li>
            </ol>
        </div>
    </div>
</body>
</html>
    """, 
    total_clients=upload_stats["total_clients"],
    banks_detected=upload_stats.get("banks_detected", 0),
    detection_rate=upload_stats.get("detection_rate", 0),
    last_upload=upload_stats["last_upload"],
    filename=upload_stats["filename"]
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload et traitement des fichiers CSV"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        filename = secure_filename(file.filename)
        upload_stats["filename"] = filename
        
        if filename.lower().endswith('.csv'):
            content = file.read().decode('utf-8-sig')
            nb_clients = load_clients_from_csv(content)
        else:
            return jsonify({"error": "Seuls les fichiers CSV sont supportés"}), 400
        
        return jsonify({
            "status": "success",
            "message": f"{nb_clients} clients chargés avec succès depuis CSV",
            "filename": filename,
            "total_clients": nb_clients,
            "banks_detected": upload_stats.get("banks_detected", 0),
            "detection_rate": f"{upload_stats.get('detection_rate', 0)}%"
        })
        
    except Exception as e:
        return jsonify({"error": f"Erreur upload: {str(e)}"}), 500

@app.route('/clients')
def view_clients():
    """Visualisation des clients"""
    search = request.args.get('search', '')
    
    if search:
        search_lower = search.lower()
        filtered_clients = {k: v for k, v in clients_database.items() 
                          if search_lower in f"{v['nom']} {v['prenom']} {v['telephone']} {v['email']} {v.get('banque_detectee', '')}".lower()}
    else:
        filtered_clients = dict(list(clients_database.items())[:100])
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>👥 Gestion Clients</title>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1600px; margin: 0 auto; }
        .search input { padding: 10px; width: 400px; border: 1px solid #ddd; border-radius: 5px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; cursor: pointer; border-radius: 5px; margin: 5px; text-decoration: none; display: inline-block; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f2f2f2; }
        .bank-detected { background: #e8f5e8; font-weight: bold; }
        .bank-unknown { background: #ffebee; }
    </style>
</head>
<body>
    <div class="container">
        <h1>👥 Base Clients ({{ total_clients }} total)</h1>
        
        <div class="search">
            <form method="GET">
                <input type="text" name="search" placeholder="Rechercher..." value="{{ search }}">
                <button type="submit" class="btn">🔍 Rechercher</button>
                <a href="/clients" class="btn">🔄 Tout afficher</a>
                <a href="/" class="btn">🏠 Accueil</a>
            </form>
        </div>
        
        <table>
            <tr>
                <th>📞 Téléphone</th>
                <th>👤 Nom</th>
                <th>👤 Prénom</th>
                <th>📧 Email</th>
                <th>🏘️ Ville</th>
                <th>🏦 Banque Détectée</th>
                <th>💳 IBAN</th>
                <th>📊 Statut</th>
                <th>📈 Appels</th>
            </tr>
            {% for tel, client in clients %}
            <tr>
                <td><strong>{{ tel }}</strong></td>
                <td>{{ client.nom }}</td>
                <td>{{ client.prenom }}</td>
                <td>{{ client.email }}</td>
                <td>{{ client.ville }}</td>
                <td class="{% if client.get('banque_detectee') and client.banque_detectee not in ['Non détectée', 'Pas d\'IBAN'] %}bank-detected{% else %}bank-unknown{% endif %}">
                    {{ client.get('banque_detectee', 'N/A') }}
                </td>
                <td style="font-size: 10px;">{{ client.get('iban', '')[:20] }}...</td>
                <td>{{ client.statut }}</td>
                <td style="text-align: center;">{{ client.nb_appels }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
    """,
    clients=filtered_clients.items(),
    total_clients=upload_stats["total_clients"],
    search=search
    )

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "webhook-ovh-telegram-csv",
        "clients_loaded": upload_stats["total_clients"],
        "banks_detected": upload_stats.get("banks_detected", 0),
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
