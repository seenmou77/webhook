from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import requests
import csv
import io
import base64
from datetime import datetime
from werkzeug.utils import secure_filename
from urllib.parse import urlencode, quote_plus, urlparse, parse_qs

app = Flask(__name__)
app.secret_key = 'webhook-ovh-secret-key'

# Configuration Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '7822148813:AAEhWJWToLUY5heVP1G_yqM1Io-vmAMlbLg')
CHAT_ID = os.environ.get('CHAT_ID', '-1002652961145')

# Configuration OAuth2 Keyyo
KEYYO_CLIENT_ID = os.environ.get('KEYYO_CLIENT_ID', '6832980609dd1')
KEYYO_CLIENT_SECRET = os.environ.get('KEYYO_CLIENT_SECRET', '3ce3ff3d62c261c079b66e9a')
KEYYO_REDIRECT_URI = 'https://web-production-95ca.up.railway.app/oauth/keyyo/callback'

# Variables globales pour Keyyo
keyyo_access_token = None
keyyo_csi_token = None

# Base de données clients en mémoire
clients_database = {}
upload_stats = {
    "total_clients": 0,
    "last_upload": None,
    "filename": None
}

def detect_bank_from_iban(iban):
    """Détecte automatiquement la banque à partir de l'IBAN via API"""
    if not iban or len(iban) < 14:
        return "N/A"
    
    # Nettoyer l'IBAN (supprimer espaces et tirets)
    iban_clean = iban.replace(' ', '').replace('-', '').upper()
    
    # Fallback local pour validation basique
    def fallback_detection(iban_clean):
        if not iban_clean.startswith('FR'):
            return "Banque étrangère"
        
        try:
            code_banque = iban_clean[4:9]
            basic_banks = {
                '10907': 'BNP Paribas', '30004': 'BNP Paribas',
                '30003': 'Société Générale', '30002': 'Crédit Agricole',
                '20041': 'La Banque Postale', '30056': 'BRED',
                '10278': 'Crédit Mutuel', '10906': 'CIC',
                '16798': 'ING Direct', '12548': 'Boursorama'
            }
            return basic_banks.get(code_banque, f"Banque française (code: {code_banque})")
        except:
            return "IBAN invalide"
    
    # Tentative 1: API ibanapi.com (gratuite)
    try:
        api_url = f"https://openiban.com/validate/{iban_clean}?getBIC=true"
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('valid'):
                bank_data = data.get('bankData', {})
                bank_name = bank_data.get('name', '')
                if bank_name:
                    print(f"🌐 API OpenIBAN: {bank_name}")
                    return f"🌐 {bank_name}"
    except Exception as e:
        print(f"⚠️ Erreur API OpenIBAN: {str(e)}")
    
    # Tentative 2: API iban-validator.com
    try:
        api_url = "https://api.iban-validator.com/iban"
        headers = {"Content-Type": "application/json"}
        payload = {"iban": iban_clean}
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('valid'):
                bank_name = data.get('bank', {}).get('name', '')
                if bank_name:
                    print(f"🌐 API IBAN-Validator: {bank_name}")
                    return f"🌐 {bank_name}"
    except Exception as e:
        print(f"⚠️ Erreur API IBAN-Validator: {str(e)}")
    
    # Tentative 3: API abstractapi.com (gratuite avec limite)
    try:
        # Clé API gratuite - remplacez par votre clé si vous en avez une
        api_key = os.environ.get('d931005e1f7146579ad649d934b65421', '')
        if api_key:
            api_url = f"https://iban.abstractapi.com/v1/?api_key={api_key}&iban={iban_clean}"
            response = requests.get(api_url, timeout=3)
            
            if response.status_code == 200:
                data = response.json()
                bank_name = data.get('bank', {}).get('name', '')
                if bank_name:
                    print(f"🌐 API AbstractAPI: {bank_name}")
                    return f"🌐 {bank_name}"
    except Exception as e:
        print(f"⚠️ Erreur API AbstractAPI: {str(e)}")
    
    # Tentative 4: API IBAN4U (gratuite avec limite)
    try:
        api_url = f"https://api.iban4u.com/v2/validate/{iban_clean}"
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('valid'):
                bank_name = data.get('bank_name', '')
                if bank_name:
                    print(f"🌐 API IBAN4U: {bank_name}")
                    return f"🌐 {bank_name}"
    except Exception as e:
        print(f"⚠️ Erreur API IBAN4U: {str(e)}")
    
    # Fallback: détection locale si toutes les APIs échouent
    print(f"🔄 Fallback: détection locale pour {iban_clean}")
    return f"📍 {fallback_detection(iban_clean)}"

def detect_bank_with_cache(iban):
    """Détection avec cache pour éviter les appels API répétés"""
    if not hasattr(detect_bank_with_cache, 'cache'):
        detect_bank_with_cache.cache = {}
    
    iban_clean = iban.replace(' ', '').replace('-', '').upper()
    
    # Vérifier le cache
    if iban_clean in detect_bank_with_cache.cache:
        print(f"💾 Cache hit pour {iban_clean}")
        return detect_bank_with_cache.cache[iban_clean]
    
    # Appel API
    result = detect_bank_from_iban(iban)
    
    # Stocker en cache
    detect_bank_with_cache.cache[iban_clean] = result
    return result

def load_clients_from_csv(file_content):
    """Charge les clients depuis un contenu CSV"""
    global clients_database, upload_stats
    
    clients_database = {}
    
    # Lecture CSV avec gestion des erreurs
    try:
        # Utilisation du module csv de Python
        csv_reader = csv.DictReader(io.StringIO(file_content))
        
        for row in csv_reader:
            # Normalisation des clés (lowercase et strip)
            normalized_row = {}
            for key, value in row.items():
                if key:  # Éviter les clés None
                    normalized_row[key.lower().strip()] = str(value).strip() if value else ""
            
            # Recherche colonne téléphone
            telephone = None
            tel_columns = ['telephone', 'tel', 'phone', 'numero', 'number', 'mobile']
            for tel_key in tel_columns:
                if tel_key in normalized_row and normalized_row[tel_key]:
                    telephone = normalized_row[tel_key]
                    break
            
            if not telephone:
                continue
                
            # Normalisation du numéro
            telephone = telephone.replace(' ', '').replace('.', '').replace('-', '').replace('(', '').replace(')', '')
            if telephone.startswith('+33'):
                telephone = '0' + telephone[3:]
            elif telephone.startswith('33') and len(telephone) > 10:
                telephone = '0' + telephone[2:]
            
            if len(telephone) >= 10 and telephone.startswith('0'):
                # Récupération IBAN pour détection automatique banque
                iban = normalized_row.get('iban', '')
                
                # Détection automatique de la banque si pas renseignée
                banque = normalized_row.get('banque', '')
                if not banque and iban:
                    banque = detect_bank_with_cache(iban)
                    print(f"🏦 Banque détectée automatiquement pour {telephone}: {banque}")
                elif not banque:
                    banque = 'N/A'
                
                clients_database[telephone] = {
                    # Informations de base
                    "nom": normalized_row.get('nom', ''),
                    "prenom": normalized_row.get('prenom', ''),
                    "email": normalized_row.get('email', ''),
                    "entreprise": normalized_row.get('entreprise', ''),
                    "telephone": telephone,
                    
                    # Adresse
                    "adresse": normalized_row.get('adresse', ''),
                    "ville": normalized_row.get('ville', ''),
                    "code_postal": normalized_row.get('code_postal', ''),
                    
                    # Informations bancaires (avec détection automatique)
                    "banque": banque,
                    "swift": normalized_row.get('swift', ''),
                    "iban": iban,
                    
                    # Informations personnelles
                    "sexe": normalized_row.get('sexe', ''),
                    "date_naissance": normalized_row.get('date_naissance', 'Non renseigné'),
                    "lieu_naissance": normalized_row.get('lieu_naissance', 'Non renseigné'),
                    "profession": normalized_row.get('profession', ''),
                    "nationalite": normalized_row.get('nationalite', ''),
                    "situation_familiale": normalized_row.get('situation_familiale', ''),
                    
                    # Gestion campagne
                    "statut": normalized_row.get('statut', 'Prospect'),
                    "date_upload": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "nb_appels": 0,
                    "dernier_appel": None,
                    "notes": ""
                }
        
        upload_stats["total_clients"] = len(clients_database)
        upload_stats["last_upload"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        # Affichage des statistiques de détection
        auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
        print(f"🏦 Détection automatique: {auto_detected} banques détectées sur {len(clients_database)} clients")
        
        return len(clients_database)
        
    except Exception as e:
        print(f"Erreur lecture CSV: {str(e)}")
        raise ValueError(f"Erreur lecture CSV: {str(e)}")

def get_client_info(phone_number):
    """Récupère les infos client depuis la base chargée"""
    # Normalisation du numéro entrant
    normalized_number = phone_number.replace(' ', '').replace('.', '').replace('-', '').replace('(', '').replace(')', '')
    if normalized_number.startswith('+33'):
        normalized_number = '0' + normalized_number[3:]
    elif normalized_number.startswith('33') and len(normalized_number) > 10:
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
        
        # Emoji spécial pour banque détectée automatiquement
        banque_display = client_info.get('banque', 'N/A')
        if banque_display not in ['N/A', ''] and client_info.get('iban'):
            if banque_display.startswith('🌐'):
                banque_display = f"{banque_display} (API)"
            elif banque_display.startswith('📍'):
                banque_display = f"{banque_display} (local)"
            else:
                banque_display = f"🤖 {banque_display} (auto-détectée)"
        
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
▪️ Banque: {banque_display}
▪️ SWIFT: <code>{client_info.get('swift', 'N/A')}</code>
▪️ IBAN: <code>{client_info.get('iban', 'N/A')}</code>

📊 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Nb appels: {client_info['nb_appels']}
▪️ Dernier appel: {client_info['dernier_appel'] or 'Premier appel'}
        """
    else:  # Recherche manuelle
        # Emoji spécial pour banque détectée automatiquement
        banque_display = client_info.get('banque', 'N/A')
        if banque_display not in ['N/A', ''] and client_info.get('iban'):
            if banque_display.startswith('🌐'):
                banque_display = f"{banque_display} (API)"
            elif banque_display.startswith('📍'):
                banque_display = f"{banque_display} (local)"
            else:
                banque_display = f"🤖 {banque_display} (auto-détectée)"
            
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
🏛️ Banque : {banque_display}
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
            
        elif message_text.startswith('/iban '):
            iban = message_text.replace('/iban ', '').strip()
            detected_bank = detect_bank_with_cache(iban)
            response_message = f"""
🏦 <b>ANALYSE IBAN VIA API</b>

💳 IBAN: <code>{iban}</code>
🏛️ Banque détectée: <b>{detected_bank}</b>

🌐 <i>Détection via APIs externes avec fallback local</i>
            """
            send_telegram_message(response_message)
            return {"status": "iban_analyzed", "iban": iban, "bank": detected_bank}
            
        elif message_text.startswith('/stats'):
            auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
            stats_message = f"""
📊 <b>STATISTIQUES CAMPAGNE</b>

👥 Clients total: {upload_stats['total_clients']}
📁 Dernier upload: {upload_stats['last_upload'] or 'Aucun'}
📋 Fichier: {upload_stats['filename'] or 'Aucun'}
🏦 Banques auto-détectées: {auto_detected}
🚀 CTI Keyyo: {'✅ Configuré' if keyyo_csi_token else '❌ Non configuré'}

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

🏦 <code>/iban FR76XXXXXXXXX</code>
   → Détecte la banque depuis l'IBAN

📊 <code>/stats</code>
   → Statistiques de la campagne

🆘 <code>/help</code>
   → Affiche cette aide

✅ <b>Le bot reçoit automatiquement:</b>
▪️ Les appels entrants OVH
▪️ Les appels entrants Keyyo CTI (temps réel)
▪️ Les notifications en temps réel
▪️ 🌐 Détection automatique des banques via APIs IBAN
            """
            send_telegram_message(help_message)
            return {"status": "help_sent"}
            
        else:
            return {"status": "unknown_command"}
            
    except Exception as e:
        print(f"❌ Erreur commande Telegram: {str(e)}")
        return {"error": str(e)}

# =================== FONCTIONS OAUTH2 KEYYO CORRIGÉES ===================

def get_keyyo_auth_url():
    """Génère l'URL d'autorisation OAuth2 Keyyo"""
    auth_params = {
        'response_type': 'code',
        'client_id': KEYYO_CLIENT_ID,
        'redirect_uri': KEYYO_REDIRECT_URI,
        'scope': 'cti_admin full_access_read_only',
        'state': 'webhook_telegram_cti'
    }
    
    return f"https://ssl.keyyo.com/oauth2/authorize.php?{urlencode(auth_params)}"

def exchange_code_for_token(auth_code):
    """Échange le code d'autorisation contre un access token - VERSION CORRIGÉE RFC 6749"""
    global keyyo_access_token
    
    # Encoder correctement les credentials selon RFC 6749 Section 2.3.1
    # Les credentials doivent être URL-encodés avant l'encoding Base64
    client_id_encoded = quote_plus(KEYYO_CLIENT_ID)
    client_secret_encoded = quote_plus(KEYYO_CLIENT_SECRET)
    credentials_string = f"{client_id_encoded}:{client_secret_encoded}"
    credentials = base64.b64encode(credentials_string.encode()).decode()
    
    # Headers selon RFC 6749 Section 3.2
    headers = {
        'Authorization': f'Basic {credentials}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    
    # Data selon RFC 6749 Section 4.1.3
    data = {
        'grant_type': 'authorization_code',
        'code': auth_code,
        'redirect_uri': KEYYO_REDIRECT_URI
    }
    
    print(f"🔍 Debug OAuth2 CORRIGÉ:")
    print(f"📋 URL: https://api.keyyo.com/oauth2/token.php")
    print(f"📋 Method: POST")
    print(f"📋 Headers: {headers}")
    print(f"📋 Data: {data}")
    print(f"📋 Client ID: {KEYYO_CLIENT_ID}")
    print(f"📋 Redirect URI: {KEYYO_REDIRECT_URI}")
    print(f"📋 Auth code: {auth_code[:20]}...")
    
    try:
        # REQUÊTE POST selon RFC 6749
        response = requests.post(
            'https://api.keyyo.com/oauth2/token.php', 
            headers=headers, 
            data=data,
            timeout=30
        )
        
        print(f"🔍 Debug Response:")
        print(f"📋 Status: {response.status_code}")
        print(f"📋 Headers: {dict(response.headers)}")
        print(f"📋 Content: {response.text}")
        
        if response.status_code == 200:
            token_data = response.json()
            keyyo_access_token = token_data['access_token']
            print(f"✅ Access token Keyyo récupéré: {keyyo_access_token[:20]}...")
            
            # Afficher toutes les infos du token
            print(f"🔍 Debug Token complet: {json.dumps(token_data, indent=2)}")
            
            return True
        else:
            print(f"❌ Erreur récupération token: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Erreur OAuth2: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def get_csi_token():
    """Récupère le CSI token nécessaire pour CTI avec debug amélioré"""
    global keyyo_csi_token
    
    if not keyyo_access_token:
        print("❌ Pas d'access token disponible")
        return None
    
    headers = {
        'Authorization': f'Bearer {keyyo_access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        print("🔍 Debug: Tentative récupération des services...")
        
        # D'abord, récupérer la liste des services
        response = requests.get('https://api.keyyo.com/1.0/services', headers=headers)
        
        print(f"🔍 Debug: Status code services: {response.status_code}")
        print(f"🔍 Debug: Response headers: {response.headers}")
        print(f"🔍 Debug: Response text: {response.text}")
        
        if response.status_code == 200:
            services = response.json()
            print(f"📋 Services trouvés: {json.dumps(services, indent=2)}")
            
            # Analyser la structure de la réponse
            if isinstance(services, dict):
                if 'services' in services:
                    # Structure: {"services": {"csi1": {...}, "csi2": {...}}}
                    services_dict = services['services']
                elif services:
                    # Structure: {"csi1": {...}, "csi2": {...}}
                    services_dict = services
                else:
                    print("❌ Structure services vide ou inconnue")
                    return None
            elif isinstance(services, list):
                # Structure: [{"csi": "...", ...}, ...]
                print("📋 Services en liste, recherche CSI...")
                services_dict = {}
                for service in services:
                    if 'csi' in service:
                        services_dict[service['csi']] = service
                    elif 'id' in service:
                        services_dict[service['id']] = service
            else:
                print(f"❌ Type de réponse inattendu: {type(services)}")
                return None
            
            print(f"🔍 Debug: Services dict: {services_dict}")
            
            # Prendre le premier service (CSI)
            if services_dict and len(services_dict) > 0:
                csi = list(services_dict.keys())[0]  # Premier CSI disponible
                print(f"🎯 CSI sélectionné: {csi}")
                
                # Essayer différentes URLs pour générer le CSI token
                possible_urls = [
                    f'https://api.keyyo.com/1.0/services/{csi}/csi_token',
                    f'https://api.keyyo.com/services/{csi}/csi_token',
                    f'https://api.keyyo.com/1.0/services/{csi}/token',
                ]
                
                for url in possible_urls:
                    print(f"🔍 Debug: Tentative URL: {url}")
                    
                    csi_response = requests.post(url, headers=headers)
                    
                    print(f"🔍 Debug: CSI Status: {csi_response.status_code}")
                    print(f"🔍 Debug: CSI Response: {csi_response.text}")
                    
                    if csi_response.status_code == 200:
                        try:
                            csi_data = csi_response.json()
                            print(f"🔍 Debug: CSI Data: {json.dumps(csi_data, indent=2)}")
                            
                            # Chercher le token dans différents champs possibles
                            token_fields = ['csi_token', 'token', 'access_token', 'cti_token']
                            
                            for field in token_fields:
                                if field in csi_data:
                                    keyyo_csi_token = csi_data[field]
                                    print(f"✅ CSI Token trouvé dans '{field}': {keyyo_csi_token[:20]}...")
                                    return keyyo_csi_token
                            
                            print(f"❌ Aucun champ token trouvé dans: {list(csi_data.keys())}")
                            
                        except json.JSONDecodeError:
                            print(f"❌ Réponse non-JSON: {csi_response.text}")
                    else:
                        print(f"❌ Erreur génération CSI token: {csi_response.status_code} - {csi_response.text}")
                
                print("❌ Aucune URL n'a fonctionné pour générer le CSI token")
                return None
                
            else:
                print("❌ Aucun service trouvé dans la réponse")
                return None
                
        elif response.status_code == 401:
            print("❌ Token d'accès invalide ou expiré")
            return None
        elif response.status_code == 403:
            print("❌ Permissions insuffisantes - vérifiez les scopes OAuth2")
            return None
        else:
            print(f"❌ Erreur récupération services: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Erreur récupération CSI: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return None

def test_keyyo_api():
    """Test de l'API Keyyo avec le token actuel"""
    if not keyyo_access_token:
        return {"error": "Pas d'access token"}
    
    headers = {
        'Authorization': f'Bearer {keyyo_access_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get('https://api.keyyo.com/1.0/services', headers=headers)
        return {
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else response.text
        }
    except Exception as e:
        return {"error": str(e)}

# =================== ROUTES WEBHOOK ===================

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
        telegram_message += f"\n🔗 Source: {'OVH' if 'CGI' in call_status else 'Keyyo CTI'}"
        
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
            "client_status": client_info['statut'],
            "bank_detected": client_info.get('banque', 'N/A') not in ['N/A', ''],
            "source": "OVH-CGI" if request.method == 'GET' else "Keyyo-CTI"
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

# =================== ROUTES OAUTH2 KEYYO ===================

@app.route('/keyyo-auth')
def keyyo_auth():
    """Démarre le processus d'authentification OAuth2"""
    auth_url = get_keyyo_auth_url()
    return redirect(auth_url)

@app.route('/oauth/keyyo/callback')
def keyyo_callback():
    """Callback OAuth2 Keyyo"""
    auth_code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f"❌ Erreur OAuth2: {error}", 400
    
    if auth_code:
        success = exchange_code_for_token(auth_code)
        
        if success:
            csi_token = get_csi_token()
            
            if csi_token:
                return f"""
                <h2>✅ Authentification Keyyo réussie !</h2>
                <p><strong>CSI Token:</strong> <code>{csi_token}</code></p>
                <p>Copiez ce token dans votre interface CTI</p>
                <a href="/keyyo-cti" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🚀 Ouvrir Interface CTI</a>
                <a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">🏠 Retour accueil</a>
                """
            else:
                return "❌ Erreur génération CSI token", 500
        else:
            return "❌ Erreur échange token", 500
    else:
        return "❌ Code d'autorisation manquant", 400

@app.route('/oauth/keyyo/manual', methods=['GET', 'POST'])
def keyyo_manual_callback():
    """Callback manuel pour debug OAuth2"""
    if request.method == 'GET':
        return """
        <html>
        <head>
            <title>🔧 Debug OAuth2 Keyyo</title>
            <style>
                body { font-family: Arial; margin: 20px; }
                .container { max-width: 600px; margin: 0 auto; }
                input, textarea { width: 100%; padding: 10px; margin: 5px 0; }
                .btn { background: #4CAF50; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }
                .debug { background: #f0f0f0; padding: 15px; margin: 10px 0; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔧 Debug OAuth2 Keyyo Manual</h1>
                
                <div class="debug">
                    <h3>📋 Instructions:</h3>
                    <ol>
                        <li>Allez sur: <a href="/keyyo-auth" target="_blank">Démarrer OAuth2</a></li>
                        <li>Autorisez l'application</li>
                        <li>Copiez le <strong>code</strong> depuis l'URL de retour</li>
                        <li>Collez-le ci-dessous</li>
                    </ol>
                </div>
                
                <form method="POST">
                    <label><strong>🔑 Code d'autorisation:</strong></label>
                    <textarea name="auth_code" rows="3" placeholder="Collez le code d'autorisation ici..."></textarea>
                    
                    <label><strong>📧 URL de callback complète (optionnel):</strong></label>
                    <input type="text" name="callback_url" placeholder="https://web-production-95ca.up.railway.app/oauth/keyyo/callback?code=...">
                    
                    <br><br>
                    <button type="submit" class="btn">🚀 Échanger contre Access Token</button>
                </form>
                
                <div class="debug">
                    <p><strong>Configuration actuelle:</strong></p>
                    <p>Client ID: <code>{client_id}</code></p>
                    <p>Redirect URI: <code>{redirect_uri}</code></p>
                </div>
            </div>
        </body>
        </html>
        """.format(
            client_id=KEYYO_CLIENT_ID,
            redirect_uri=KEYYO_REDIRECT_URI
        )
    
    elif request.method == 'POST':
        auth_code = request.form.get('auth_code', '').strip()
        callback_url = request.form.get('callback_url', '').strip()
        
        # Extraire le code depuis l'URL si fournie
        if callback_url and 'code=' in callback_url:
            parsed = urlparse(callback_url)
            params = parse_qs(parsed.query)
            if 'code' in params:
                auth_code = params['code'][0]
        
        if auth_code:
            print(f"🔧 Test manuel OAuth2 avec code: {auth_code[:20]}...")
            success = exchange_code_for_token(auth_code)
            
            if success:
                csi_token = get_csi_token()
                
                if csi_token:
                    return f"""
                    <h2>✅ Succès OAuth2 Manuel !</h2>
                    <p><strong>Access Token:</strong> <code>{keyyo_access_token[:20]}...</code></p>
                    <p><strong>CSI Token:</strong> <code>{csi_token}</code></p>
                    <a href="/keyyo-cti" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🚀 Interface CTI</a>
                    <a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">🏠 Accueil</a>
                    """
                else:
                    return f"""
                    <h2>⚠️ Access Token OK, mais erreur CSI Token</h2>
                    <p><strong>Access Token:</strong> <code>{keyyo_access_token[:20]}...</code></p>
                    <p>Vérifiez les logs pour voir l'erreur CSI Token</p>
                    <a href="/debug-keyyo">🔍 Debug API</a>
                    """
            else:
                return """
                <h2>❌ Erreur échange OAuth2</h2>
                <p>Vérifiez les logs serveur pour plus d'infos</p>
                <a href="/oauth/keyyo/manual">🔄 Réessayer</a>
                """
        else:
            return """
            <h2>❌ Code d'autorisation manquant</h2>
            <a href="/oauth/keyyo/manual">🔄 Retour</a>
            """

@app.route('/test-oauth-direct')
def test_oauth_direct():
    """Test OAuth2 avec paramètres hardcodés pour debug"""
    
    # Pour tester avec un code que vous récupérez manuellement
    test_code = request.args.get('code', '')
    
    if test_code:
        print(f"🧪 Test OAuth2 direct avec code: {test_code[:20]}...")
        success = exchange_code_for_token(test_code)
        
        return jsonify({
            "test": "oauth_direct",
            "code_received": test_code[:20] + "...",
            "exchange_success": success,
            "access_token_available": keyyo_access_token is not None
        })
    else:
        return jsonify({
            "error": "Ajoutez ?code=VOTRE_CODE à l'URL pour tester"
        })

@app.route('/keyyo-cti')
def keyyo_cti_interface():
    """Interface CTI Keyyo"""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🚀 Interface CTI Keyyo</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 20px; background: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }
            .success { background: #e8f5e8; padding: 15px; margin: 20px 0; border-radius: 5px; border-left: 4px solid #4CAF50; }
            .btn { background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 5px; display: inline-block; }
            .btn.success { background: #4CAF50; }
            code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Interface CTI Keyyo</h1>
            <p>Votre CSI Token: <strong><code>{{ csi_token }}</code></strong></p>
            
            <div class="success">
                <h3>✅ Prochaines étapes:</h3>
                <ol>
                    <li>Copiez le CSI token ci-dessus</li>
                    <li>Ouvrez l'interface CTI dans un nouvel onglet</li>
                    <li>Collez le token et connectez-vous</li>
                    <li>Les appels seront automatiquement envoyés à Telegram!</li>
                </ol>
            </div>
            
            <a href="https://keyyo-cti-interface.up.railway.app" target="_blank" class="btn success">🚀 Ouvrir Interface CTI</a>
            <a href="/keyyo-status" class="btn">📊 Status Keyyo</a>
            <a href="/" class="btn">🏠 Retour accueil</a>
        </div>
    </body>
    </html>
    """, csi_token=keyyo_csi_token or 'Non disponible')

@app.route('/keyyo-status')
def keyyo_status():
    """Status de l'intégration Keyyo"""
    return jsonify({
        "access_token_available": keyyo_access_token is not None,
        "csi_token_available": keyyo_csi_token is not None,
        "csi_token_preview": keyyo_csi_token[:20] + "..." if keyyo_csi_token else None,
        "auth_url": get_keyyo_auth_url(),
        "client_id": KEYYO_CLIENT_ID,
        "redirect_uri": KEYYO_REDIRECT_URI
    })

@app.route('/debug-keyyo')
def debug_keyyo():
    """Debug manuel de l'API Keyyo"""
    if not keyyo_access_token:
        return jsonify({"error": "Pas d'access token. Faites d'abord /keyyo-auth"})
    
    headers = {
        'Authorization': f'Bearer {keyyo_access_token}',
        'Content-Type': 'application/json'
    }
    
    debug_info = {
        "access_token_preview": keyyo_access_token[:20] + "..." if keyyo_access_token else None,
        "tests": []
    }
    
    # Test 1: Services
    try:
        response = requests.get('https://api.keyyo.com/1.0/services', headers=headers)
        debug_info["tests"].append({
            "endpoint": "/1.0/services",
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else response.text,
            "headers": dict(response.headers)
        })
    except Exception as e:
        debug_info["tests"].append({
            "endpoint": "/1.0/services",
            "error": str(e)
        })
    
    # Test 2: Alternative services endpoint
    try:
        response = requests.get('https://api.keyyo.com/services', headers=headers)
        debug_info["tests"].append({
            "endpoint": "/services",
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else response.text,
            "headers": dict(response.headers)
        })
    except Exception as e:
        debug_info["tests"].append({
            "endpoint": "/services",
            "error": str(e)
        })
    
    # Test 3: User info
    try:
        response = requests.get('https://api.keyyo.com/1.0/user', headers=headers)
        debug_info["tests"].append({
            "endpoint": "/1.0/user",
            "status_code": response.status_code,
            "response": response.json() if response.status_code == 200 else response.text
        })
    except Exception as e:
        debug_info["tests"].append({
            "endpoint": "/1.0/user",
            "error": str(e)
        })
    
    return jsonify(debug_info)

@app.route('/manual-csi', methods=['GET', 'POST'])
def manual_csi():
    """Interface pour saisie manuelle du CSI token"""
    global keyyo_csi_token
    
    if request.method == 'POST':
        manual_token = request.form.get('csi_token', '').strip()
        
        if manual_token:
            keyyo_csi_token = manual_token
            print(f"✅ CSI Token saisi manuellement: {keyyo_csi_token[:20]}...")
            
            return f"""
            <h2>✅ CSI Token configuré manuellement !</h2>
            <p><strong>Token:</strong> <code>{keyyo_csi_token[:20]}...</code></p>
            <a href="/keyyo-cti" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🚀 Ouvrir Interface CTI</a>
            <a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">🏠 Retour accueil</a>
            """
        else:
            return "❌ Token vide", 400
    
    return """
    <html>
    <head>
        <title>🔑 Saisie manuelle CSI Token</title>
        <style>
            body { font-family: Arial; margin: 20px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }
            input { padding: 10px; width: 100%; border: 1px solid #ddd; border-radius: 5px; margin: 10px 0; }
            .btn { background: #4CAF50; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; }
            .info { background: #e1f5fe; padding: 15px; border-radius: 5px; margin: 10px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔑 Saisie manuelle CSI Token</h1>
            
            <div class="info">
                <h3>📋 Comment récupérer votre CSI Token :</h3>
                <ol>
                    <li>Connectez-vous à votre <strong>espace client Bouygues Pro</strong></li>
                    <li>Cherchez la section <strong>"API"</strong> ou <strong>"Développeurs"</strong></li>
                    <li>Ou appelez le <strong>1067</strong> et demandez votre <strong>"CSI Token pour l'API CTI"</strong></li>
                    <li>Le token ressemble à : <code>eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...</code></li>
                </ol>
            </div>
            
            <form method="POST">
                <label><strong>🔑 CSI Token :</strong></label>
                <input type="text" name="csi_token" placeholder="Collez votre CSI token ici..." required>
                <br>
                <button type="submit" class="btn">✅ Configurer Token</button>
            </form>
            
            <p><a href="/">🏠 Retour accueil</a></p>
        </div>
    </body>
    </html>
    """

# =================== ROUTES PRINCIPALES ===================

@app.route('/')
def home():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Webhook OVH-Telegram - Gestion Clients</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: #e3f2fd; padding: 20px; border-radius: 8px; text-align: center; }
        .upload-section { background: #f0f4f8; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; margin: 5px; }
        .btn:hover { background: #1976D2; }
        .btn-danger { background: #f44336; }
        .btn-success { background: #4CAF50; }
        .btn-keyyo { background: #ff9800; }
        .btn-manual { background: #9c27b0; }
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
        .success { color: #4CAF50; font-weight: bold; }
        .error { color: #f44336; font-weight: bold; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
        .info-box { background: #e8f5e8; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .new-feature { background: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; margin: 10px 0; }
        .keyyo-section { background: #e1f5fe; border-left: 4px solid #4CAF50; padding: 15px; margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram + 🚀 CTI Keyyo</h1>
            <p class="success">✅ Serveur Railway actif 24/7 - Bot configuré</p>
            <div class="new-feature">
                <strong>🆕 NOUVELLE FONCTIONNALITÉ :</strong> 🚀 Intégration CTI Keyyo temps réel + 🌐 Détection automatique banques IBAN !
            </div>
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
            <div class="stat-card">
                <h3>🚀 CTI Keyyo</h3>
                <p>{{ 'Configuré' if csi_available else 'À configurer' }}</p>
            </div>
        </div>

        <div class="keyyo-section">
            <h2>🚀 Configuration Keyyo CTI</h2>
            <div class="info-box">
                <h3>🎯 Intégration CTI temps réel :</h3>
                <ol>
                    <li>🔐 <strong>Authentifiez-vous</strong> : <a href="/keyyo-auth" style="color: #4CAF50; font-weight: bold;">Démarrer OAuth2 Keyyo</a></li>
                    <li>🔧 <strong>Debug manuel</strong> : <a href="/oauth/keyyo/manual" style="color: #9c27b0; font-weight: bold;">Test OAuth2 Manuel</a></li>
                    <li>🔑 <strong>Saisie manuelle</strong> : <a href="/manual-csi" style="color: #ff9800; font-weight: bold;">CSI Token Manuel</a></li>
                    <li>📊 <strong>Vérifiez le statut</strong> : <a href="/keyyo-status" style="color: #2196F3;">Status intégration</a></li>
                    <li>🚀 <strong>Interface CTI</strong> : <a href="/keyyo-cti" style="color: #ff9800;">Ouvrir supervision</a></li>
                    <li>✅ <strong>Test complet</strong> : Appelez votre numéro et vérifiez Telegram</li>
                </ol>
            </div>
            
            <div class="links">
                <a href="/keyyo-auth" class="btn btn-success">🔐 Auth Keyyo OAuth2</a>
                <a href="/oauth/keyyo/manual" class="btn btn-manual">🔧 Debug Manuel</a>
                <a href="/manual-csi" class="btn btn-keyyo">🔑 CSI Token Manuel</a>
                <a href="/keyyo-status" class="btn">📊 Status Keyyo</a>
                <a href="/keyyo-cti" class="btn btn-keyyo">🚀 Interface CTI</a>
                <a href="/debug-keyyo" class="btn">🔍 Debug API</a>
            </div>
        </div>

        <div class="upload-section">
            <h2>📂 Upload fichier clients (CSV uniquement)</h2>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <div class="info-box">
                    <p><strong>📋 Format supporté:</strong> CSV (.csv)</p>
                    <p><strong>🔥 Colonne obligatoire:</strong> <code>telephone</code> (ou tel, phone, numero)</p>
                    <p><strong>✨ Colonnes optionnelles:</strong></p>
                    <ul style="text-align: left; max-width: 800px; margin: 0 auto;">
                        <li><strong>Identité:</strong> nom, prenom, sexe, date_naissance, lieu_naissance, nationalite</li>
                        <li><strong>Contact:</strong> email, adresse, ville, code_postal</li>
                        <li><strong>Professionnel:</strong> entreprise, profession</li>
                        <li><strong>Bancaire:</strong> banque, swift, iban</li>
                        <li><strong>Divers:</strong> statut, situation_familiale</li>
                    </ul>
                    <div class="new-feature" style="margin-top: 10px;">
                        <strong>🌐 AUTO-DÉTECTION BANQUE VIA API :</strong> Si la colonne <code>banque</code> est vide mais qu'un <code>iban</code> est présent, la banque sera automatiquement détectée via APIs externes !
                    </div>
                </div>
                <input type="file" name="file" accept=".csv" required style="margin: 10px 0;">
                <br>
                <button type="submit" class="btn btn-success">📁 Charger fichier CSV</button>
            </form>
        </div>

        <h2>🔧 Tests & Configuration</h2>
        <div class="links">
            <a href="/clients" class="btn">👥 Voir clients</a>
            <a href="/setup-telegram-webhook" class="btn">⚙️ Config Telegram</a>
            <a href="/test-telegram" class="btn">📧 Test Telegram</a>
            <a href="/test-command" class="btn">🎯 Test /numero</a>
            <a href="/test-iban" class="btn">🏦 Test détection IBAN</a>
            <a href="/test-ovh-cgi" class="btn">📞 Test appel OVH</a>
            <a href="/clear-clients" class="btn btn-danger" onclick="return confirm('Effacer tous les clients ?')">🗑️ Vider base</a>
        </div>

        <h2>🔗 Configuration OVH CTI</h2>
        <div class="info-box">
            <p><strong>URL CGI à configurer dans l'interface OVH :</strong></p>
            <code>https://web-production-95ca.up.railway.app/webhook/ovh?caller=*CALLING*&callee=*CALLED*&type=*EVENT*</code>
        </div>

        <h2>📱 Commandes Telegram disponibles</h2>
        <ul>
            <li><code>/numero 0123456789</code> - Affiche fiche client complète</li>
            <li><code>/iban FR76XXXXXXXXX</code> - <span class="new-feature" style="display: inline; background: #fff3e0; padding: 2px 6px;">🆕 Détecte la banque depuis l'IBAN</span></li>
            <li><code>/stats</code> - Statistiques de la campagne + status CTI Keyyo</li>
            <li><code>/help</code> - Aide et liste des commandes</li>
        </ul>

        <div class="info-box">
            <h3>🎯 Comment ça marche :</h3>
            <ol>
                <li>📂 Uploadez votre fichier CSV avec les clients</li>
                <li>🌐 Les banques sont automatiquement détectées via APIs IBAN externes</li>
                <li>🚀 Configurez l'authentification Keyyo CTI pour les appels temps réel</li>
                <li>📞 Configurez l'URL OVH CTI en backup</li>
                <li>✅ Chaque appel entrant affiche automatiquement la fiche client dans Telegram</li>
                <li>🔍 Utilisez <code>/numero XXXXXXXXXX</code> pour rechercher un client</li>
                <li>🆕 Utilisez <code>/iban FR76XXXXX</code> pour tester la détection de banque</li>
            </ol>
        </div>
    </div>
</body>
</html>
    """, 
    total_clients=upload_stats["total_clients"],
    last_upload=upload_stats["last_upload"],
    filename=upload_stats["filename"],
    csi_available=keyyo_csi_token is not None
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload et traitement du fichier CSV"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Aucun fichier sélectionné"}), 400
        
        filename = secure_filename(file.filename)
        upload_stats["filename"] = filename
        
        # Lecture CSV uniquement
        if filename.endswith('.csv'):
            content = file.read().decode('utf-8-sig')  # utf-8-sig pour gérer le BOM Excel
            nb_clients = load_clients_from_csv(content)
            
            # Statistiques de détection automatique
            auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
            
        else:
            return jsonify({"error": "Seuls les fichiers CSV sont supportés dans cette version"}), 400
        
        return jsonify({
            "status": "success",
            "message": f"{nb_clients} clients chargés avec succès",
            "filename": filename,
            "total_clients": nb_clients,
            "auto_detected_banks": auto_detected
        })
        
    except Exception as e:
        return jsonify({"error": f"Erreur upload: {str(e)}"}), 500

@app.route('/clients')
def view_clients():
    """Interface de visualisation des clients"""
    search = request.args.get('search', '')
    
    if search:
        search_lower = search.lower()
        filtered_clients = {k: v for k, v in clients_database.items() 
                          if search_lower in f"{v['nom']} {v['prenom']} {v['telephone']} {v['entreprise']} {v['email']} {v['ville']} {v['banque']}".lower()}
    else:
        # Limite à 100 pour la performance
        filtered_clients = dict(list(clients_database.items())[:100])
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>👥 Gestion Clients</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1600px; margin: 0 auto; }
        .search { margin-bottom: 20px; }
        .search input { padding: 10px; width: 300px; border: 1px solid #ddd; border-radius: 5px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; cursor: pointer; border-radius: 5px; margin: 5px; text-decoration: none; display: inline-block; }
        .btn:hover { background: #1976D2; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th, td { border: 1px solid #ddd; padding: 6px; text-align: left; }
        th { background: #f2f2f2; position: sticky; top: 0; }
        .status-prospect { background: #fff3e0; }
        .status-client { background: #e8f5e8; }
        .stats { background: #f0f4f8; padding: 15px; margin-bottom: 20px; border-radius: 5px; }
        .table-container { max-height: 600px; overflow-y: auto; }
        .highlight { background: yellow; }
        .auto-detected { background: #e3f2fd; font-weight: bold; }
    </style>
    <script>
        function highlightSearch() {
            const search = '{{ search }}';
            if (search) {
                const cells = document.querySelectorAll('td');
                cells.forEach(cell => {
                    if (cell.textContent.toLowerCase().includes(search.toLowerCase())) {
                        cell.innerHTML = cell.innerHTML.replace(new RegExp(search, 'gi'), '<span class="highlight">$&</span>');
                    }
                });
            }
        }
        window.onload = highlightSearch;
    </script>
</head>
<body>
    <div class="container">
        <h1>👥 Base Clients ({{ total_clients }} total)</h1>
        
        <div class="stats">
            <strong>📊 Statistiques:</strong> 
            Total: {{ total_clients }} | 
            Affichés: {{ displayed_count }} |
            Avec appels: {{ with_calls }} |
            Aujourd'hui: {{ today_calls }} |
            🏦 Banques auto-détectées: {{ auto_detected }}
        </div>
        
        <div class="search">
            <form method="GET">
                <input type="text" name="search" placeholder="Rechercher (nom, téléphone, entreprise, email, ville, banque...)" value="{{ search }}">
                <button type="submit" class="btn">🔍 Rechercher</button>
                <a href="/clients" class="btn">🔄 Tout afficher</a>
                <a href="/" class="btn">🏠 Accueil</a>
            </form>
        </div>
        
        <div class="table-container">
            <table>
                <tr>
                    <th>📞 Téléphone</th>
                    <th>👤 Nom</th>
                    <th>👤 Prénom</th>
                    <th>🏢 Entreprise</th>
                    <th>📧 Email</th>
                    <th>🏘️ Ville</th>
                    <th>🏦 Banque</th>
                    <th>💳 IBAN</th>
                    <th>📊 Statut</th>
                    <th>📈 Appels</th>
                    <th>🕐 Dernier</th>
                    <th>📋 Upload</th>
                </tr>
                {% for tel, client in clients %}
                <tr class="status-{{ client.statut.lower().replace(' ', '') }}">
                    <td><strong>{{ tel }}</strong></td>
                    <td>{{ client.nom }}</td>
                    <td>{{ client.prenom }}</td>
                    <td>{{ client.entreprise }}</td>
                    <td>{{ client.email }}</td>
                    <td>{{ client.ville }}</td>
                    <td class="{% if client.banque not in ['N/A', ''] and client.iban %}auto-detected{% endif %}">
                        {{ client.banque }}
                        {% if client.banque not in ['N/A', ''] and client.iban %}🤖{% endif %}
                    </td>
                    <td>{{ client.iban[:10] }}...{% if client.iban|length > 10 %}{% endif %}</td>
                    <td><strong>{{ client.statut }}</strong></td>
                    <td style="text-align: center;">{{ client.nb_appels }}</td>
                    <td>{{ client.dernier_appel or '-' }}</td>
                    <td>{{ client.date_upload }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        {% if displayed_count >= 100 and total_clients > 100 %}
        <p style="color: orange;"><strong>⚠️ Affichage limité aux 100 premiers clients. Utilisez la recherche pour filtrer.</strong></p>
        {% endif %}
        
        <p><strong>🤖 Légende:</strong> Les banques avec icône robot ont été auto-détectées depuis l'IBAN</p>
    </div>
</body>
</html>
    """,
    clients=filtered_clients.items(),
    total_clients=upload_stats["total_clients"],
    displayed_count=len(filtered_clients),
    with_calls=len([c for c in clients_database.values() if c['nb_appels'] > 0]),
    today_calls=len([c for c in clients_database.values() if c['dernier_appel'] and c['dernier_appel'].startswith(datetime.now().strftime('%d/%m/%Y'))]),
    auto_detected=len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']]),
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
    message = f"🧪 Test de connexion - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    result = send_telegram_message(message)
    
    if result:
        return jsonify({"status": "success", "message": "Test Telegram envoyé avec succès"})
    else:
        return jsonify({"status": "error", "message": "Échec du test Telegram"})

@app.route('/test-command')
def test_command():
    """Test de la commande /numero"""
    # Test avec un client existant s'il y en a
    if clients_database:
        test_number = list(clients_database.keys())[0]
    else:
        test_number = "0767328146"  # Numéro par défaut
    
    result = process_telegram_command(f"/numero {test_number}", CHAT_ID)
    return jsonify({"test_result": result, "test_number": test_number})

@app.route('/test-iban')
def test_iban():
    """Test de la détection d'IBAN via API"""
    test_ibans = [
        "FR1420041010050500013M02606",  # La Banque Postale
        "FR7630003000540000000001234",  # Société Générale
        "FR1411315000100000000000000",  # Crédit Agricole
        "FR7610907000000000000000000",  # BNP Paribas
        "FR7617206000000000000000000",  # BRED
        "DE89370400440532013000",       # Deutsche Bank (test étranger)
    ]
    
    results = []
    for iban in test_ibans:
        bank = detect_bank_with_cache(iban)
        results.append({"iban": iban, "bank_detected": bank})
    
    return jsonify({
        "test_results": results,
        "function_status": "API-enabled with fallback",
        "total_tests": len(test_ibans),
        "cache_size": len(getattr(detect_bank_with_cache, 'cache', {}))
    })

@app.route('/test-ovh-cgi')
def test_ovh_cgi():
    """Test du webhook OVH format CGI"""
    # Test avec un client existant s'il y en a
    if clients_database:
        test_caller = list(clients_database.keys())[0]
    else:
        test_caller = "0767328146"
    
    params = {
        'caller': test_caller,
        'callee': '0033185093001', 
        'type': 'start_ringing'
    }
    
    return f"""
    <h2>🧪 Test OVH CGI</h2>
    <p>Simulation d'un appel OVH avec paramètres CGI</p>
    <p><a href="/webhook/ovh?{urlencode(params)}" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🎯 Déclencher test appel</a></p>
    <p><strong>Paramètres de test:</strong> {params}</p>
    <p><a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🏠 Retour accueil</a></p>
    """

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "service": "webhook-ovh-telegram-keyyo",
        "telegram_configured": bool(TELEGRAM_TOKEN and CHAT_ID),
        "clients_loaded": upload_stats["total_clients"],
        "iban_detection": "API-enabled with fallback",
        "keyyo_oauth_configured": bool(KEYYO_CLIENT_ID and KEYYO_CLIENT_SECRET),
        "keyyo_authenticated": keyyo_access_token is not None,
        "keyyo_cti_ready": keyyo_csi_token is not None,
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
