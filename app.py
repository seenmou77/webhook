from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import requests
import pandas as pd
import io
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
    "filename": None
}

def load_clients_from_dataframe(df):
    """Charge les clients depuis un DataFrame pandas"""
    global clients_database, upload_stats
    
    clients_database = {}
    
    # Normalisation des colonnes
    df.columns = df.columns.str.lower().str.strip()
    
    # Mapping des colonnes possibles - COMPLET
    column_mapping = {
        'telephone': ['telephone', 'tel', 'phone', 'numero', 'number', 'mobile'],
        'nom': ['nom', 'lastname', 'name', 'surname'],
        'prenom': ['prenom', 'firstname', 'first_name', 'fname'],
        'email': ['email', 'mail', 'e-mail'],
        'entreprise': ['entreprise', 'company', 'societe', 'société'],
        'adresse': ['adresse', 'address', 'rue'],
        'ville': ['ville', 'city'],
        'code_postal': ['code_postal', 'cp', 'zip', 'postal'],
        'statut': ['statut', 'status', 'etat'],
        'banque': ['banque', 'bank', 'etablissement'],
        'swift': ['swift', 'bic'],
        'iban': ['iban'],
        'sexe': ['sexe', 'gender', 'genre'],
        'date_naissance': ['date_naissance', 'naissance', 'birth_date', 'birthday'],
        'lieu_naissance': ['lieu_naissance', 'birth_place', 'lieu_naiss'],
        'profession': ['profession', 'job', 'metier'],
        'nationalite': ['nationalite', 'nationality'],
        'situation_familiale': ['situation_familiale', 'marital_status', 'famille']
    }
    
    def find_column(possible_names):
        for col in df.columns:
            if col in possible_names:
                return col
        return None
    
    # Recherche des colonnes
    tel_col = find_column(column_mapping['telephone'])
    nom_col = find_column(column_mapping['nom'])
    prenom_col = find_column(column_mapping['prenom'])
    email_col = find_column(column_mapping['email'])
    entreprise_col = find_column(column_mapping['entreprise'])
    adresse_col = find_column(column_mapping['adresse'])
    ville_col = find_column(column_mapping['ville'])
    cp_col = find_column(column_mapping['code_postal'])
    statut_col = find_column(column_mapping['statut'])
    banque_col = find_column(column_mapping['banque'])
    swift_col = find_column(column_mapping['swift'])
    iban_col = find_column(column_mapping['iban'])
    sexe_col = find_column(column_mapping['sexe'])
    date_naiss_col = find_column(column_mapping['date_naissance'])
    lieu_naiss_col = find_column(column_mapping['lieu_naissance'])
    profession_col = find_column(column_mapping['profession'])
    nationalite_col = find_column(column_mapping['nationalite'])
    situation_col = find_column(column_mapping['situation_familiale'])
    
    if not tel_col:
        raise ValueError("Colonne téléphone non trouvée. Colonnes disponibles: " + ", ".join(df.columns))
    
    # Chargement des clients
    for index, row in df.iterrows():
        telephone = str(row[tel_col]).strip()
        
        # Normalisation du numéro
        telephone = telephone.replace(' ', '').replace('.', '').replace('-', '')
        if telephone.startswith('+33'):
            telephone = '0' + telephone[3:]
        elif telephone.startswith('33'):
            telephone = '0' + telephone[2:]
        
        if len(telephone) >= 10:
            clients_database[telephone] = {
                # Informations de base
                "nom": str(row[nom_col] if nom_col else "").strip(),
                "prenom": str(row[prenom_col] if prenom_col else "").strip(),
                "email": str(row[email_col] if email_col else "").strip(),
                "entreprise": str(row[entreprise_col] if entreprise_col else "").strip(),
                "telephone": telephone,
                
                # Adresse
                "adresse": str(row[adresse_col] if adresse_col else "").strip(),
                "ville": str(row[ville_col] if ville_col else "").strip(),
                "code_postal": str(row[cp_col] if cp_col else "").strip(),
                
                # Informations bancaires
                "banque": str(row[banque_col] if banque_col else "").strip(),
                "swift": str(row[swift_col] if swift_col else "").strip(),
                "iban": str(row[iban_col] if iban_col else "").strip(),
                
                # Informations personnelles
                "sexe": str(row[sexe_col] if sexe_col else "").strip(),
                "date_naissance": str(row[date_naiss_col] if date_naiss_col else "Non renseigné").strip(),
                "lieu_naissance": str(row[lieu_naiss_col] if lieu_naiss_col else "Non renseigné").strip(),
                "profession": str(row[profession_col] if profession_col else "").strip(),
                "nationalite": str(row[nationalite_col] if nationalite_col else "").strip(),
                "situation_familiale": str(row[situation_col] if situation_col else "").strip(),
                
                # Gestion campagne
                "statut": str(row[statut_col] if statut_col else "Prospect").strip(),
                "date_upload": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "nb_appels": 0,
                "dernier_appel": None,
                "notes": ""
            }
    
    upload_stats["total_clients"] = len(clients_database)
    upload_stats["last_upload"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    return len(clients_database)

def get_client_info(phone_number):
    """Récupère les infos client depuis la base chargée"""
    # Normalisation du numéro entrant
    normalized_number = phone_number.replace(' ', '').replace('.', '').replace('-', '')
    if normalized_number.startswith('+33'):
        normalized_number = '0' + normalized_number[3:]
    elif normalized_number.startswith('33'):
        normalized_number = '0' + normalized_number[2:]
    
    # Recherche exacte
    if normalized_number in clients_database:
        client = clients_database[normalized_number].copy()
        # Mise à jour statistiques
        clients_database[normalized_number]["nb_appels"] += 1
        clients_database[normalized_number]["dernier_appel"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        return client
    
    # Recherche partielle (derniers 9 chiffres)
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
📍 Lieu de naissance: {client_info.get('lieu_naissance', 'N/A')}

🏢 <b>PROFESSIONNEL</b>
▪️ Entreprise: {client_info['entreprise']}
▪️ Profession: {client_info.get('profession', 'N/A')}
▪️ Email: {client_info['email']}

🏠 <b>COORDONNÉES</b>
▪️ Adresse: {client_info['adresse']}
▪️ Ville: {client_info['ville']} {client_info['code_postal']}

🏦 <b>INFORMATIONS BANCAIRES</b>
▪️ Banque: {client_info.get('banque', 'N/A')}
▪️ SWIFT: <code>{client_info.get('swift', 'N/A')}</code>
▪️ IBAN: <code>{client_info.get('iban', 'N/A')}</code>

📊 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Nb appels: {client_info['nb_appels']}
▪️ Dernier appel: {client_info['dernier_appel'] or 'Premier appel'}
        """
    else:  # Recherche manuelle
        return f"""
📋 <b>RÉSULTAT TROUVÉ :</b>

👤 <b>IDENTITÉ</b>
🙋 Nom : <b>{client_info['nom']}</b>
👤 Prénom : <b>{client_info['prenom']}</b>
👥 Sexe : {client_info.get('sexe', 'N/A')}
🎂 Date de naissance : {client_info.get('date_naissance', 'N/A')}
📍 Lieu de naissance : {client_info.get('lieu_naissance', 'N/A')}
🌍 Nationalité : {client_info.get('nationalite', 'N/A')}

🏢 <b>PROFESSIONNEL</b>
▪️ Entreprise : {client_info['entreprise']}
▪️ Profession : {client_info.get('profession', 'N/A')}
📧 Email : {client_info['email']}
📞 Téléphone : <code>{client_info['telephone']}</code>

🏠 <b>ADRESSE</b>
▪️ Adresse : {client_info['adresse']}
📮 Code postal : {client_info['code_postal']}
🏘️ Ville : {client_info['ville']}

🏦 <b>INFORMATIONS BANCAIRES</b>
🏛️ Banque : {client_info.get('banque', 'N/A')}
💳 SWIFT : <code>{client_info.get('swift', 'N/A')}</code>
🏦 IBAN : <code>{client_info.get('iban', 'N/A')}</code>

👨‍👩‍👧‍👦 <b>SITUATION</b>
▪️ Situation familiale : {client_info.get('situation_familiale', 'N/A')}

💼 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Ajouté le: {client_info.get('date_upload', 'N/A')}
▪️ Nb appels: {client_info['nb_appels']}
▪️ Dernier appel: {client_info['dernier_appel'] or 'Jamais appelé'}
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
   → Affiche la fiche client complète

📊 <code>/stats</code>
   → Statistiques de la campagne

🆘 <code>/help</code>
   → Affiche cette aide

✅ <b>Le bot reçoit automatiquement:</b>
▪️ Les appels entrants OVH
▪️ Les notifications en temps réel
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
            print(f"📞 Appelé: {called_number}")
            print(f"📋 Type: {event_type}")
        else:
            data = request.get_json() or {}
            caller_number = data.get('callerIdNumber', request.args.get('caller', 'Inconnu'))
            call_status = data.get('status', 'incoming')
            
            print(f"🔔 [{timestamp}] Appel JSON:")
            print(f"📋 Données: {json.dumps(data, indent=2)}")
        
        # Récupération fiche client
        client_info = get_client_info(caller_number)
        
        # Message Telegram formaté
        telegram_message = format_client_message(client_info, context="appel")
        telegram_message += f"\n📊 Statut appel: {call_status}"
        
        # Envoi vers Telegram
        telegram_result = send_telegram_message(telegram_message)
        
        if telegram_result:
            print("✅ Message Telegram envoyé")
        else:
            print("❌ Échec envoi Telegram")
        
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "caller": caller_number,
            "method": request.method,
            "telegram_sent": telegram_result is not None,
            "client": f"{client_info['prenom']} {client_info['nom']}",
            "client_status": client_info['statut']
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
            user_name = data['message']['from'].get('first_name', 'Utilisateur')
            
            print(f"📱 Commande reçue de {user_name}: {message_text}")
            
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
    <title>🤖 Webhook OVH-Telegram - Gestion Clients</title>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }
        .header { text-align: center; margin-bottom: 30px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: #e3f2fd; padding: 20px; border-radius: 8px; text-align: center; }
        .upload-section { background: #f0f4f8; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn:hover { background: #1976D2; }
        .btn-danger { background: #f44336; }
        .btn-success { background: #4CAF50; }
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram</h1>
            <p>✅ Serveur Railway actif 24/7 - Bot configuré</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>👥 Clients chargés</h3>
                <h2>{{ total_clients }}</h2>
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
            <h2>📂 Upload fichier clients</h2>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <p><strong>Formats supportés:</strong> CSV, Excel (.xlsx, .xls)</p>
                <p><strong>Colonne obligatoire:</strong> telephone (ou tel, phone, numero)</p>
                <p><strong>Colonnes optionnelles:</strong></p>
                <ul style="text-align: left; max-width: 800px; margin: 0 auto;">
                    <li><strong>Identité:</strong> nom, prenom, sexe, date_naissance, lieu_naissance, nationalite</li>
                    <li><strong>Contact:</strong> email, adresse, ville, code_postal</li>
                    <li><strong>Professionnel:</strong> entreprise, profession</li>
                    <li><strong>Bancaire:</strong> banque, swift, iban</li>
                    <li><strong>Divers:</strong> statut, situation_familiale</li>
                </ul>
                <br>
                <input type="file" name="file" accept=".csv,.xlsx,.xls" required>
                <button type="submit" class="btn btn-success">📁 Charger fichier</button>
            </form>
        </div>

        <h2>🔧 Tests & Configuration</h2>
        <div class="links">
            <a href="/clients" class="btn">👥 Voir clients</a>
            <a href="/setup-telegram-webhook" class="btn">⚙️ Config Telegram</a>
            <a href="/test-telegram" class="btn">📧 Test Telegram</a>
            <a href="/test-command" class="btn">🎯 Test /numero</a>
            <a href="/test-ovh-cgi" class="btn">📞 Test appel OVH</a>
            <a href="/clear-clients" class="btn btn-danger" onclick="return confirm('Effacer tous les clients ?')">🗑️ Vider base</a>
        </div>

        <h2>🔗 Configuration OVH</h2>
        <p><strong>URL CGI à configurer :</strong></p>
        <code>https://web-production-95ca.up.railway.app/webhook/ovh?caller=*CALLING*&callee=*CALLED*&type=*EVENT*</code>

        <h2>📱 Commandes Telegram</h2>
        <ul>
            <li><code>/numero 0123456789</code> - Affiche fiche client</li>
            <li><code>/stats</code> - Statistiques campagne</li>
            <li><code>/help</code> - Aide</li>
        </ul>
    </div>
</body>
</html>
    """, 
    total_clients=upload_stats["total_clients"],
    last_upload=upload_stats["last_upload"],
    filename=upload_stats["filename"]
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload et traitement du fichier clients"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        filename = secure_filename(file.filename)
        upload_stats["filename"] = filename
        
        # Lecture du fichier
        if filename.endswith('.csv'):
            df = pd.read_csv(io.StringIO(file.read().decode('utf-8')))
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(file.read()))
        else:
            return jsonify({"error": "Format non supporté. Utilisez CSV ou Excel"}), 400
        
        # Chargement des clients
        nb_clients = load_clients_from_dataframe(df)
        
        return jsonify({
            "status": "success",
            "message": f"{nb_clients} clients chargés avec succès",
            "filename": filename,
            "total_clients": nb_clients
        })
        
    except Exception as e:
        return jsonify({"error": f"Erreur upload: {str(e)}"}), 500

@app.route('/clients')
def view_clients():
    """Interface de visualisation des clients"""
    search = request.args.get('search', '')
    
    if search:
        filtered_clients = {k: v for k, v in clients_database.items() 
                          if search.lower() in f"{v['nom']} {v['prenom']} {v['telephone']} {v['entreprise']}".lower()}
    else:
        filtered_clients = dict(list(clients_database.items())[:50])  # Limite à 50 pour la performance
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>👥 Gestion Clients</title>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .search { margin-bottom: 20px; }
        .search input { padding: 10px; width: 300px; border: 1px solid #ddd; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f2f2f2; }
        .status-prospect { background: #fff3e0; }
        .status-client { background: #e8f5e8; }
        .stats { background: #f0f4f8; padding: 15px; margin-bottom: 20px; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>👥 Base Clients ({{ total_clients }} total)</h1>
        
        <div class="stats">
            <strong>📊 Statistiques:</strong> 
            Total: {{ total_clients }} | 
            Affichés: {{ displayed_count }} |
            Avec appels: {{ with_calls }}
        </div>
        
        <div class="search">
            <form method="GET">
                <input type="text" name="search" placeholder="Rechercher (nom, téléphone, entreprise...)" value="{{ search }}">
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
                <th>🏢 Entreprise</th>
                <th>📧 Email</th>
                <th>🏘️ Ville</th>
                <th>🏦 Banque</th>
                <th>📊 Statut</th>
                <th>📈 Appels</th>
                <th>🕐 Dernier</th>
            </tr>
            {% for tel, client in clients %}
            <tr class="status-{{ client.statut.lower() }}">
                <td><strong>{{ tel }}</strong></td>
                <td>{{ client.nom }}</td>
                <td>{{ client.prenom }}</td>
                <td>{{ client.entreprise }}</td>
                <td>{{ client.email }}</td>
                <td>{{ client.ville }}</td>
                <td>{{ client.get('banque', 'N/A') }}</td>
                <td><strong>{{ client.statut }}</strong></td>
                <td>{{ client.nb_appels }}</td>
                <td>{{ client.dernier_appel or '-' }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
    """,
    clients=filtered_clients.items(),
    total_clients=upload_stats["total_clients"],
    displayed_count=len(filtered_clients),
    with_calls=len([c for c in clients_database.values() if c['nb_appels'] > 0]),
    search=search
    )

@app.route('/clear-clients')
def clear_clients():
    """Vide la base de données clients"""
    global clients_database, upload_stats
    clients_database = {}
    upload_stats = {"total_clients": 0, "last_upload": None, "filename": None}
    return redirect('/')

@app.route('/setup-telegram-webhook')
def setup_telegram_webhook():
    """Configure le webhook Telegram pour recevoir les commandes"""
    try:
        webhook_url = f"https://web-production-95ca.up.railway.app/webhook/telegram"
        telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
        
        data = {"url": webhook_url}
        response = requests.post(telegram_api_url, data=data)
        
        return jsonify({
            "status": "webhook_configured",
            "telegram_response": response.json(),
            "webhook_url": webhook_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/test-telegram')
def test_telegram():
    """Test d'envoi Telegram"""
    message = f"🧪 Test de connexion - {datetime.now().strftime('%H:%M:%S')}"
    result = send_telegram_message(message)
    
    if result:
        return jsonify({"status": "success", "message": "Test Telegram envoyé"})
    else:
        return jsonify({"status": "error", "message": "Échec test Telegram"})

@app.route('/test-command')
def test_command():
    """Test de la commande /numero"""
    # Test avec un client existant s'il y en a
    if clients_database:
        test_number = list(clients_database.keys())[0]
    else:
        test_number = "0123456789"
    
    result = process_telegram_command(f"/numero {test_number}", CHAT_ID)
    return jsonify({"test_result": result, "test_number": test_number})

@app.route('/test-ovh-cgi')
def test_ovh_cgi():
    """Test du webhook OVH format CGI"""
    from urllib.parse import urlencode
    
    # Test avec un client existant s'il y en a
    if clients_database:
        test_caller = list(clients_database.keys())[0]
    else:
        test_caller = "0123456789"
    
    params = {
        'caller': test_caller,
        'callee': '0033185093001', 
        'type': 'start_ringing'
    }
    
    return f"""
    <h2>🧪 Test OVH CGI</h2>
    <p>Simulation d'un appel OVH avec paramètres CGI</p>
    <p><a href="/webhook/ovh?{urlencode(params)}">🎯 Déclencher test appel</a></p>
    <p>Paramètres: {params}</p>
    <p><a href="/">🏠 Retour accueil</a></p>
    """

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "service": "webhook-ovh",
        "telegram_configured": bool(TELEGRAM_TOKEN and CHAT_ID),
        "clients_loaded": upload_stats["total_clients"]
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
