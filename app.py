from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import requests
import csv
import io
import base64
import time
import re
from datetime import datetime
from werkzeug.utils import secure_filename
from urllib.parse import urlencode, quote_plus, urlparse, parse_qs
from functools import wraps
import logging

# ===================================================================
# CONFIGURATION ET LOGGING
# ===================================================================

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Removed FileHandler for Railway compatibility
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'webhook-ovh-secret-key'

# Configuration centralisée
class Config:
    # Telegram
    TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '7822148813:AAEhWJWToLUY5heVP1G_yqM1Io-vmAMlbLg')
    CHAT_ID = os.environ.get('CHAT_ID', '-1002567065407')
    
    # Keyyo OAuth2
    KEYYO_CLIENT_ID = os.environ.get('KEYYO_CLIENT_ID', '6832980609dd1')
    KEYYO_CLIENT_SECRET = os.environ.get('KEYYO_CLIENT_SECRET', '3ce3ff3d62c261c079b66e9a')
    KEYYO_REDIRECT_URI = 'https://web-production-95ca.up.railway.app/oauth/keyyo/callback'
    
    # APIs IBAN
    ABSTRACT_API_KEY = os.environ.get('ABSTRACT_API_KEY', 'd931005e1f7146579ad649d934b65421')

app.config.from_object(Config)

# ===================================================================
# CACHE ET RATE LIMITING
# ===================================================================

class SimpleCache:
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
    
    def get(self, key, ttl=3600):
        if key in self.cache:
            if time.time() - self.timestamps.get(key, 0) < ttl:
                return self.cache[key]
            else:
                # Expired
                del self.cache[key]
                if key in self.timestamps:
                    del self.timestamps[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = value
        self.timestamps[key] = time.time()
    
    def clear(self):
        self.cache.clear()
        self.timestamps.clear()

# Cache global
cache = SimpleCache()

def rate_limit(calls_per_minute=30):
    """Rate limiting decorator"""
    def decorator(func):
        calls = []
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()
            # Nettoyer les appels anciens
            calls[:] = [call_time for call_time in calls if now - call_time < 60]
            
            if len(calls) >= calls_per_minute:
                logger.warning("Rate limit exceeded")
                raise Exception("Rate limit exceeded")
            
            calls.append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ===================================================================
# SERVICE DÉTECTION IBAN AMÉLIORÉ
# ===================================================================

class IBANDetector:
    def __init__(self):
        self.local_banks = {
            '10907': 'BNP Paribas', '30004': 'BNP Paribas',
            '30003': 'Société Générale', '30002': 'Crédit Agricole',
            '20041': 'La Banque Postale', '30056': 'BRED',
            '10278': 'Crédit Mutuel', '10906': 'CIC',
            '16798': 'ING Direct', '12548': 'Boursorama',
            '30027': 'Crédit Coopératif', '10011': 'BNP Paribas Fortis',
            '17515': 'Monabanq', '18206': 'N26'
        }
    
    def clean_iban(self, iban):
        """Nettoie l'IBAN"""
        if not iban:
            return ""
        return iban.replace(' ', '').replace('-', '').upper()
    
    def detect_local(self, iban_clean):
        """Détection locale basique"""
        if not iban_clean.startswith('FR'):
            return "Banque étrangère"
        
        if len(iban_clean) < 14:
            return "IBAN invalide"
        
        try:
            code_banque = iban_clean[4:9]
            return self.local_banks.get(code_banque, f"Banque française (code: {code_banque})")
        except:
            return "IBAN invalide"
    
    def detect_with_api(self, iban_clean):
        """Détection via APIs externes avec timeout court"""
        # Vérifier cache d'abord
        cache_key = f"iban:{iban_clean}"
        cached_result = cache.get(cache_key, ttl=86400)  # Cache 24h
        if cached_result:
            logger.info(f"💾 Cache hit pour IBAN: {iban_clean}")
            return cached_result
        
        # API OpenIBAN
        try:
            response = requests.get(
                f"https://openiban.com/validate/{iban_clean}?getBIC=true",
                timeout=3
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('valid'):
                    bank_name = data.get('bankData', {}).get('name', '')
                    if bank_name:
                        result = f"🌐 {bank_name}"
                        cache.set(cache_key, result)
                        logger.info(f"✅ API OpenIBAN: {bank_name}")
                        return result
        except Exception as e:
            logger.debug(f"⚠️ Erreur API OpenIBAN: {str(e)}")
        
        # API IBAN4U
        try:
            response = requests.get(
                f"https://api.iban4u.com/v2/validate/{iban_clean}",
                timeout=3
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('valid'):
                    bank_name = data.get('bank_name', '')
                    if bank_name:
                        result = f"🌐 {bank_name}"
                        cache.set(cache_key, result)
                        logger.info(f"✅ API IBAN4U: {bank_name}")
                        return result
        except Exception as e:
            logger.debug(f"⚠️ Erreur API IBAN4U: {str(e)}")
        
        # API AbstractAPI (si clé disponible)
        if Config.ABSTRACT_API_KEY:
            try:
                response = requests.get(
                    f"https://iban.abstractapi.com/v1/?api_key={Config.ABSTRACT_API_KEY}&iban={iban_clean}",
                    timeout=3
                )
                if response.status_code == 200:
                    data = response.json()
                    bank_name = data.get('bank', {}).get('name', '')
                    if bank_name:
                        result = f"🌐 {bank_name}"
                        cache.set(cache_key, result)
                        logger.info(f"✅ API AbstractAPI: {bank_name}")
                        return result
            except Exception as e:
                logger.debug(f"⚠️ Erreur API AbstractAPI: {str(e)}")
        
        return None
    
    def detect_bank(self, iban):
        """Détection principale avec fallback"""
        if not iban:
            return "N/A"
        
        iban_clean = self.clean_iban(iban)
        if not iban_clean:
            return "N/A"
        
        # Tentative API
        api_result = self.detect_with_api(iban_clean)
        if api_result:
            return api_result
        
        # Fallback local
        local_result = f"📍 {self.detect_local(iban_clean)}"
        logger.info(f"🔄 Fallback local pour {iban_clean}: {local_result}")
        return local_result

# Instance globale
iban_detector = IBANDetector()

# ===================================================================
# CLIENT KEYYO AMÉLIORÉ
# ===================================================================

class KeyyoClient:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.csi_token = None
    
    def get_auth_url(self):
        """Génère l'URL d'autorisation OAuth2"""
        auth_params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'cti_admin full_access_read_only',
            'state': 'webhook_telegram_cti'
        }
        return f"https://ssl.keyyo.com/oauth2/authorize.php?{urlencode(auth_params)}"
    
    def exchange_code_for_token(self, auth_code):
        """Échange le code contre un access token - VERSION CORRIGÉE RFC 6749"""
        # Encoder correctement les credentials selon RFC 6749 Section 2.3.1
        client_id_encoded = quote_plus(self.client_id)
        client_secret_encoded = quote_plus(self.client_secret)
        credentials_string = f"{client_id_encoded}:{client_secret_encoded}"
        credentials = base64.b64encode(credentials_string.encode()).decode()
        
        headers = {
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        data = {
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.redirect_uri
        }
        
        logger.info(f"🔍 OAuth2 Exchange - URL: https://api.keyyo.com/oauth2/token.php")
        logger.info(f"🔍 Client ID: {self.client_id}")
        logger.info(f"🔍 Redirect URI: {self.redirect_uri}")
        
        try:
            response = requests.post(
                'https://api.keyyo.com/oauth2/token.php',
                headers=headers,
                data=data,
                timeout=30
            )
            
            logger.info(f"🔍 OAuth2 Response - Status: {response.status_code}")
            logger.info(f"🔍 OAuth2 Response - Content: {response.text}")
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                logger.info(f"✅ Access token récupéré: {self.access_token[:20]}...")
                return True
            else:
                logger.error(f"❌ Erreur OAuth2: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erreur OAuth2: {str(e)}")
            return False
    
    def get_services(self):
        """Récupère la liste des services disponibles"""
        if not self.access_token:
            logger.error("❌ Pas d'access token disponible")
            return None
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        try:
            logger.info("🔍 Récupération de la liste des services...")
            response = requests.get(
                'https://api.keyyo.com/1.0/services',
                headers=headers,
                timeout=10
            )
            
            logger.info(f"📊 Services Status: {response.status_code}")
            logger.info(f"📊 Services Response: {response.text}")
            
            if response.status_code == 200:
                services_data = response.json()
                logger.info(f"✅ Services récupérés: {json.dumps(services_data, indent=2)}")
                return services_data
            else:
                logger.error(f"❌ Erreur services: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Erreur récupération services: {str(e)}")
            return None
    
    def generate_csi_token(self):
        """
        Génère le CSI token - VERSION CORRIGÉE basée sur la documentation
        URL documentée: POST https://api.keyyo.com/1.0/services/:csi/csi_token
        """
        if not self.access_token:
            logger.error("❌ Pas d'access token disponible")
            return None
        
        # 1. Récupérer les services d'abord
        services_data = self.get_services()
        if not services_data:
            return None
        
        # 2. Extraire le premier CSI disponible
        if 'services' in services_data:
            services_dict = services_data['services']
        elif isinstance(services_data, dict):
            services_dict = services_data
        else:
            logger.error("❌ Format de services non reconnu")
            return None
        
        if not services_dict:
            logger.error("❌ Aucun service trouvé")
            return None
        
        # Prendre le premier CSI
        csi_id = list(services_dict.keys())[0]
        logger.info(f"🎯 CSI sélectionné: {csi_id}")
        
        # 3. Générer le CSI token via l'endpoint documenté
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # URL selon la documentation fournie
        csi_token_url = f'https://api.keyyo.com/1.0/services/{csi_id}/csi_token'
        logger.info(f"🚀 Génération CSI token via: {csi_token_url}")
        
        try:
            # POST request selon la documentation
            response = requests.post(
                csi_token_url,
                headers=headers,
                timeout=10
            )
            
            logger.info(f"📊 CSI Status: {response.status_code}")
            logger.info(f"📊 CSI Response: {response.text}")
            logger.info(f"📊 CSI Headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                try:
                    # Essayer de parser en JSON
                    csi_data = response.json()
                    logger.info(f"📋 CSI Data (JSON): {json.dumps(csi_data, indent=2)}")
                    
                    # Chercher le token dans différents champs possibles
                    token_fields = ['csi_token', 'token', 'access_token', 'cti_token']
                    
                    for field in token_fields:
                        if field in csi_data and csi_data[field]:
                            self.csi_token = csi_data[field]
                            logger.info(f"✅ CSI Token trouvé dans '{field}': {self.csi_token[:20]}...")
                            return self.csi_token
                    
                    logger.error(f"❌ Token non trouvé dans les champs: {list(csi_data.keys())}")
                    return None
                    
                except json.JSONDecodeError:
                    # La réponse pourrait être directement le token en texte brut
                    token_text = response.text.strip()
                    if token_text and len(token_text) > 10:  # Token minimum viable
                        self.csi_token = token_text
                        logger.info(f"✅ CSI Token (texte brut): {self.csi_token[:20]}...")
                        return self.csi_token
                    else:
                        logger.error(f"❌ Réponse texte non valide: '{token_text}'")
                        return None
            
            elif response.status_code == 401:
                logger.error("❌ Token d'accès invalide ou expiré")
                return None
            elif response.status_code == 403:
                logger.error("❌ Permissions insuffisantes - vérifiez les scopes OAuth2")
                return None
            elif response.status_code == 404:
                logger.error(f"❌ Service CSI '{csi_id}' non trouvé - vérifiez l'ID")
                return None
            else:
                logger.error(f"❌ Erreur HTTP {response.status_code}: {response.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("❌ Timeout lors de la requête CSI token")
            return None
        except requests.exceptions.ConnectionError:
            logger.error("❌ Erreur de connexion à l'API Keyyo")
            return None
        except Exception as e:
            logger.error(f"❌ Erreur inattendue: {str(e)}")
            return None
    
    def test_api(self):
        """Test de l'API Keyyo avec le token actuel"""
        if not self.access_token:
            return {"error": "Pas d'access token"}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get('https://api.keyyo.com/1.0/services', headers=headers, timeout=5)
            return {
                "status_code": response.status_code,
                "response": response.json() if response.status_code == 200 else response.text
            }
        except Exception as e:
            return {"error": str(e)}

# Instance globale Keyyo
keyyo_client = KeyyoClient(
    Config.KEYYO_CLIENT_ID,
    Config.KEYYO_CLIENT_SECRET,
    Config.KEYYO_REDIRECT_URI
)

# ===================================================================
# SERVICE TELEGRAM AMÉLIORÉ
# ===================================================================

class TelegramService:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
    
    @rate_limit(calls_per_minute=30)
    def send_message(self, message):
        """Envoie un message vers Telegram avec rate limiting"""
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("✅ Message Telegram envoyé")
                return response.json()
            else:
                logger.error(f"❌ Erreur Telegram: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Erreur Telegram: {str(e)}")
            return None
    
    def format_client_message(self, client_info, context="appel"):
        """Formate un message client pour Telegram"""
        emoji_statut = "📞" if client_info['statut'] != "Non référencé" else "❓"
        
        # Emoji spécial pour banque détectée automatiquement
        banque_display = client_info.get('banque', 'N/A')
        if banque_display not in ['N/A', ''] and client_info.get('iban'):
            if banque_display.startswith('🌐'):
                banque_display = f"{banque_display} (API)"
            elif banque_display.startswith('📍'):
                banque_display = f"{banque_display} (local)"
        
        return f"""
{emoji_statut} <b>{'APPEL ENTRANT' if context == 'appel' else 'RECHERCHE'}</b>
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
📧 Email: {client_info['email']}

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

# Instance globale Telegram
telegram_service = TelegramService(Config.TELEGRAM_TOKEN, Config.CHAT_ID)

# ===================================================================
# GESTION CLIENTS ET DONNÉES
# ===================================================================

# Base de données clients en mémoire
clients_database = {}
upload_stats = {
    "total_clients": 0,
    "last_upload": None,
    "filename": None
}

def normalize_phone(phone):
    """Normalisation avancée des numéros de téléphone"""
    if not phone:
        return None
    
    # Supprimer tous les caractères non numériques sauf +
    cleaned = re.sub(r'[^\d+]', '', str(phone))
    
    # Patterns courants
    patterns = [
        (r'^\+33(\d{9})$', lambda m: '0' + m.group(1)),      # +33123456789 -> 0123456789
        (r'^33(\d{9})$', lambda m: '0' + m.group(1)),        # 33123456789 -> 0123456789
        (r'^0(\d{9})$', lambda m: '0' + m.group(1)),         # 0123456789 -> 0123456789
        (r'^(\d{10})$', lambda m: m.group(1)),               # 1234567890 -> 1234567890
    ]
    
    for pattern, transform in patterns:
        match = re.match(pattern, cleaned)
        if match:
            result = transform(match)
            # Validation finale
            if len(result) == 10 and result.startswith('0'):
                return result
    
    return None

def load_clients_from_csv(file_content):
    """Charge les clients depuis un contenu CSV avec détection automatique banque"""
    global clients_database, upload_stats
    
    clients_database = {}
    
    try:
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
                    telephone = normalize_phone(normalized_row[tel_key])
                    break
            
            if not telephone:
                continue
            
            # Récupération IBAN pour détection automatique banque
            iban = normalized_row.get('iban', '')
            
            # Détection automatique de la banque si pas renseignée
            banque = normalized_row.get('banque', '')
            if not banque and iban:
                banque = iban_detector.detect_bank(iban)
                logger.info(f"🏦 Banque détectée automatiquement pour {telephone}: {banque}")
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
        logger.info(f"🏦 Détection automatique: {auto_detected} banques détectées sur {len(clients_database)} clients")
        
        return len(clients_database)
        
    except Exception as e:
        logger.error(f"Erreur lecture CSV: {str(e)}")
        raise ValueError(f"Erreur lecture CSV: {str(e)}")

def get_client_info(phone_number):
    """Récupère les infos client depuis la base chargée"""
    # Normalisation du numéro entrant
    normalized_number = normalize_phone(phone_number)
    
    if not normalized_number:
        return create_unknown_client(phone_number)
    
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
    return create_unknown_client(phone_number)

def create_unknown_client(phone_number):
    """Crée une fiche client pour un numéro inconnu"""
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

def process_telegram_command(message_text, chat_id):
    """Traite les commandes Telegram reçues"""
    try:
        if message_text.startswith('/numero '):
            phone_number = message_text.replace('/numero ', '').strip()
            client_info = get_client_info(phone_number)
            response_message = telegram_service.format_client_message(client_info, context="recherche")
            telegram_service.send_message(response_message)
            return {"status": "command_processed", "command": "numero", "phone": phone_number}
            
        elif message_text.startswith('/iban '):
            iban = message_text.replace('/iban ', '').strip()
            detected_bank = iban_detector.detect_bank(iban)
            response_message = f"""
🏦 <b>ANALYSE IBAN VIA API</b>

💳 IBAN: <code>{iban}</code>
🏛️ Banque détectée: <b>{detected_bank}</b>

🌐 <i>Détection via APIs externes avec fallback local</i>
            """
            telegram_service.send_message(response_message)
            return {"status": "iban_analyzed", "iban": iban, "bank": detected_bank}
            
        elif message_text.startswith('/stats'):
            auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
            stats_message = f"""
📊 <b>STATISTIQUES CAMPAGNE</b>

👥 Clients total: {upload_stats['total_clients']}
📁 Dernier upload: {upload_stats['last_upload'] or 'Aucun'}
📋 Fichier: {upload_stats['filename'] or 'Aucun'}
🏦 Banques auto-détectées: {auto_detected}
🚀 CTI Keyyo: {'✅ Configuré' if keyyo_client.csi_token else '❌ Non configuré'}

📞 <b>APPELS DU JOUR</b>
▪️ Clients appelants: {len([c for c in clients_database.values() if c['dernier_appel'] and c['dernier_appel'].startswith(datetime.now().strftime('%d/%m/%Y'))])}
▪️ Nouveaux contacts: {len([c for c in clients_database.values() if c['nb_appels'] == 0])}
            """
            telegram_service.send_message(stats_message)
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
            telegram_service.send_message(help_message)
            return {"status": "help_sent"}
            
        else:
            return {"status": "unknown_command"}
            
    except Exception as e:
        logger.error(f"❌ Erreur commande Telegram: {str(e)}")
        return {"error": str(e)}

# ===================================================================
# ROUTES WEBHOOK
# ===================================================================

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
            
            logger.info(f"🔔 [{timestamp}] Appel CGI OVH: {caller_number} -> {called_number} ({event_type})")
        else:
            data = request.get_json() or {}
            caller_number = data.get('callerIdNumber', request.args.get('caller', 'Inconnu'))
            call_status = data.get('status', 'incoming')
            
            logger.info(f"🔔 [{timestamp}] Appel JSON: {json.dumps(data, indent=2)}")
        
        # Récupération fiche client
        client_info = get_client_info(caller_number)
        
        # Message Telegram formaté
        telegram_message = telegram_service.format_client_message(client_info, context="appel")
        telegram_message += f"\n📊 Statut appel: {call_status}"
        telegram_message += f"\n🔗 Source: {'OVH' if 'CGI' in call_status else 'Keyyo CTI'}"
        
        # Envoi vers Telegram
        telegram_result = telegram_service.send_message(telegram_message)
        
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
        logger.error(f"❌ Erreur webhook: {str(e)}")
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
            
            logger.info(f"📱 Commande reçue de {user_name}: {message_text}")
            
            result = process_telegram_command(message_text, chat_id)
            
            return jsonify({
                "status": "success",
                "command_result": result,
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })
        
        return jsonify({"status": "no_text_message"})
        
    except Exception as e:
        logger.error(f"❌ Erreur webhook Telegram: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ===================================================================
# ROUTES OAUTH2 KEYYO
# ===================================================================

@app.route('/keyyo-auth')
def keyyo_auth():
    """Démarre le processus d'authentification OAuth2"""
    auth_url = keyyo_client.get_auth_url()
    return redirect(auth_url)

@app.route('/oauth/keyyo/callback')
def keyyo_callback():
    """Callback OAuth2 Keyyo"""
    auth_code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f"❌ Erreur OAuth2: {error}", 400
    
    if auth_code:
        success = keyyo_client.exchange_code_for_token(auth_code)
        
        if success:
            csi_token = keyyo_client.generate_csi_token()
            
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
            client_id=Config.KEYYO_CLIENT_ID,
            redirect_uri=Config.KEYYO_REDIRECT_URI
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
            logger.info(f"🔧 Test manuel OAuth2 avec code: {auth_code[:20]}...")
            success = keyyo_client.exchange_code_for_token(auth_code)
            
            if success:
                csi_token = keyyo_client.generate_csi_token()
                
                if csi_token:
                    return f"""
                    <h2>✅ Succès OAuth2 Manuel !</h2>
                    <p><strong>Access Token:</strong> <code>{keyyo_client.access_token[:20]}...</code></p>
                    <p><strong>CSI Token:</strong> <code>{csi_token}</code></p>
                    <a href="/keyyo-cti" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🚀 Interface CTI</a>
                    <a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">🏠 Accueil</a>
                    """
                else:
                    return f"""
                    <h2>⚠️ Access Token OK, mais erreur CSI Token</h2>
                    <p><strong>Access Token:</strong> <code>{keyyo_client.access_token[:20]}...</code></p>
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

@app.route('/manual-csi', methods=['GET', 'POST'])
def manual_csi():
    """Interface pour saisie manuelle du CSI token"""
    if request.method == 'POST':
        manual_token = request.form.get('csi_token', '').strip()
        
        if manual_token:
            keyyo_client.csi_token = manual_token
            logger.info(f"✅ CSI Token saisi manuellement: {keyyo_client.csi_token[:20]}...")
            
            return f"""
            <h2>✅ CSI Token configuré manuellement !</h2>
            <p><strong>Token:</strong> <code>{keyyo_client.csi_token[:20]}...</code></p>
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
    """, csi_token=keyyo_client.csi_token or 'Non disponible')

@app.route('/keyyo-status')
def keyyo_status():
    """Status de l'intégration Keyyo"""
    return jsonify({
        "access_token_available": keyyo_client.access_token is not None,
        "csi_token_available": keyyo_client.csi_token is not None,
        "csi_token_preview": keyyo_client.csi_token[:20] + "..." if keyyo_client.csi_token else None,
        "auth_url": keyyo_client.get_auth_url(),
        "client_id": Config.KEYYO_CLIENT_ID,
        "redirect_uri": Config.KEYYO_REDIRECT_URI
    })

@app.route('/debug-keyyo')
def debug_keyyo():
    """Debug manuel de l'API Keyyo"""
    if not keyyo_client.access_token:
        return jsonify({"error": "Pas d'access token. Faites d'abord /keyyo-auth"})
    
    test_result = keyyo_client.test_api()
    
    debug_info = {
        "access_token_preview": keyyo_client.access_token[:20] + "..." if keyyo_client.access_token else None,
        "csi_token_available": keyyo_client.csi_token is not None,
        "api_test": test_result
    }
    
    return jsonify(debug_info)

# ===================================================================
# ROUTES PRINCIPALES
# ===================================================================

@app.route('/')
def home():
    """Page d'accueil avec dashboard"""
    auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Webhook OVH-Telegram - Version Corrigée</title>
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
        .fixed-version { background: #e8f5e8; border-left: 4px solid #4CAF50; padding: 15px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram + 🚀 CTI Keyyo</h1>
            <div class="fixed-version">
                <strong>✅ VERSION CORRIGÉE :</strong> Architecture modulaire, fonction get_csi_token() corrigée, optimisations de performance !
            </div>
            <p class="success">✅ Serveur Railway actif 24/7 - Bot configuré</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <h3>👥 Clients chargés</h3>
                <h2>{{ total_clients }}</h2>
            </div>
            <div class="stat-card">
                <h3>🏦 Banques détectées</h3>
                <h2>{{ auto_detected }}</h2>
            </div>
            <div class="stat-card">
                <h3>📁 Dernier upload</h3>
                <p>{{ last_upload or 'Aucun' }}</p>
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
            <li><code>/iban FR76XXXXXXXXX</code> - Détecte la banque depuis l'IBAN</li>
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
        
        <div class="fixed-version">
            <h3>🔧 Améliorations de cette version :</h3>
            <ul>
                <li>✅ Fonction <code>get_csi_token()</code> corrigée selon la documentation Keyyo</li>
                <li>✅ Architecture modulaire avec services séparés</li>
                <li>✅ Cache amélioré pour les détections IBAN</li>
                <li>✅ Rate limiting pour éviter le spam</li>
                <li>✅ Logging structuré et gestion d'erreurs améliorée</li>
                <li>✅ Normalisation avancée des numéros de téléphone</li>
                <li>✅ Optimisations de performance</li>
            </ul>
        </div>
    </div>
</body>
</html>
    """, 
    total_clients=upload_stats["total_clients"],
    auto_detected=auto_detected,
    last_upload=upload_stats["last_upload"],
    csi_available=keyyo_client.csi_token is not None
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
            return jsonify({"error": "Seuls les fichiers CSV sont supportés"}), 400
        
        return jsonify({
            "status": "success",
            "message": f"{nb_clients} clients chargés avec succès",
            "filename": filename,
            "total_clients": nb_clients,
            "auto_detected_banks": auto_detected
        })
        
    except Exception as e:
        logger.error(f"Erreur upload: {str(e)}")
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
    
    auto_detected = len([c for c in clients_database.values() if c['banque'] not in ['N/A', ''] and c['iban']])
    
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
    auto_detected=auto_detected,
    search=search
    )

@app.route('/clear-clients')
def clear_clients():
    """Vide la base de données clients"""
    global clients_database, upload_stats
    clients_database = {}
    upload_stats = {"total_clients": 0, "last_upload": None, "filename": None}
    cache.clear()  # Vider aussi le cache
    return redirect('/')

@app.route('/setup-telegram-webhook')
def setup_telegram_webhook():
    """Configure le webhook Telegram pour recevoir les commandes"""
    try:
        webhook_url = f"https://web-production-95ca.up.railway.app/webhook/telegram"
        telegram_api_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/setWebhook"
        
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
    message = f"🧪 Test de connexion - Version corrigée - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    result = telegram_service.send_message(message)
    
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
    
    result = process_telegram_command(f"/numero {test_number}", Config.CHAT_ID)
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
        bank = iban_detector.detect_bank(iban)
        results.append({"iban": iban, "bank_detected": bank})
    
    return jsonify({
        "test_results": results,
        "function_status": "API-enabled with cache and fallback",
        "total_tests": len(test_ibans),
        "cache_size": len(cache.cache)
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
    """Status de l'application"""
    return jsonify({
        "status": "healthy", 
        "version": "corrected",
        "service": "webhook-ovh-telegram-keyyo",
        "telegram_configured": bool(Config.TELEGRAM_TOKEN and Config.CHAT_ID),
        "clients_loaded": upload_stats["total_clients"],
        "iban_detection": "API-enabled with cache and fallback",
        "keyyo_oauth_configured": bool(Config.KEYYO_CLIENT_ID and Config.KEYYO_CLIENT_SECRET),
        "keyyo_authenticated": keyyo_client.access_token is not None,
        "keyyo_cti_ready": keyyo_client.csi_token is not None,
        "cache_size": len(cache.cache),
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })
# Ajoutez ces routes à la fin de votre app.py pour tester immédiatement

@app.route('/test-oauth-direct')
def test_oauth_direct():
    """Test OAuth2 avec le code que vous avez reçu"""
    # Utilisez le code de votre URL
    test_code = "e457e407714dad048a8d54ef11319d377a18bf4c"
    
    logger.info(f"🧪 Test OAuth2 avec code: {test_code}")
    
    # Réinitialiser les tokens
    keyyo_client.access_token = None
    keyyo_client.csi_token = None
    
    # Tester l'échange
    success = keyyo_client.exchange_code_for_token(test_code)
    
    result = {
        "code_used": test_code,
        "exchange_success": success,
        "access_token_available": keyyo_client.access_token is not None
    }
    
    if success:
        # Tester la génération CSI avec la nouvelle fonction
        logger.info("🚀 Test génération CSI avec fonction corrigée...")
        csi_token = keyyo_client.generate_csi_token()
        result.update({
            "csi_generation_success": csi_token is not None,
            "csi_token_preview": csi_token[:20] + "..." if csi_token else None,
            "full_csi_token": csi_token  # Pour debug
        })
    
    return jsonify(result)

@app.route('/test-csi-multiple')
def test_csi_multiple():
    """Test génération CSI avec multiples approches"""
    if not keyyo_client.access_token:
        return jsonify({"error": "Pas d'access token. Allez d'abord sur /test-oauth-direct"})
    
    logger.info("🧪 Test CSI avec multiples configurations...")
    
    # Récupérer les services
    services = keyyo_client.get_services()
    if not services:
        return jsonify({"error": "Impossible de récupérer les services"})
    
    services_dict = services.get('services', services) if isinstance(services, dict) else {}
    if not services_dict:
        return jsonify({"error": "Aucun service trouvé", "services_response": services})
    
    csi_id = list(services_dict.keys())[0]
    
    headers = {
        'Authorization': f'Bearer {keyyo_client.access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    # Tester différentes configurations
    test_results = []
    
    configs = [
        {
            'name': 'URL corrigée avec domain_masks',
            'url': f'https://api.keyyo.com/manager/1.0/services/{csi_id}/csi_token',
            'payload': {'domain_masks': ['*.up.railway.app', 'web-production-95ca.up.railway.app']}
        },
        {
            'name': 'URL corrigée sans payload',
            'url': f'https://api.keyyo.com/manager/1.0/services/{csi_id}/csi_token',
            'payload': None
        },
        {
            'name': 'Ancienne URL avec domain_masks',
            'url': f'https://api.keyyo.com/1.0/services/{csi_id}/csi_token',
            'payload': {'domain_masks': ['*.up.railway.app']}
        },
        {
            'name': 'Ancienne URL sans payload',
            'url': f'https://api.keyyo.com/1.0/services/{csi_id}/csi_token',
            'payload': None
        }
    ]
    
    for config in configs:
        try:
            if config['payload']:
                response = requests.post(
                    config['url'],
                    headers=headers,
                    json=config['payload'],
                    timeout=5
                )
            else:
                response = requests.post(
                    config['url'],
                    headers=headers,
                    timeout=5
                )
            
            result = {
                'name': config['name'],
                'url': config['url'],
                'payload': config['payload'],
                'status_code': response.status_code,
                'response': response.text,
                'success': response.status_code == 200
            }
            
            if response.status_code == 200:
                try:
                    json_data = response.json()
                    result['json_response'] = json_data
                    # Chercher le token
                    for field in ['csi_token', 'token', 'access_token']:
                        if field in json_data and json_data[field]:
                            result['token_found'] = json_data[field]
                            break
                except:
                    result['raw_response'] = response.text
            
            test_results.append(result)
            
        except Exception as e:
            test_results.append({
                'name': config['name'],
                'url': config['url'],
                'error': str(e)
            })
    
    return jsonify({
        "csi_id": csi_id,
        "services_found": list(services_dict.keys()),
        "test_results": test_results
    })

@app.route('/manual-test-csi', methods=['GET', 'POST'])
def manual_test_csi():
    """Interface manuelle pour tester CSI"""
    if request.method == 'GET':
        return """
        <html>
        <head><title>🧪 Test CSI Manuel</title></head>
        <body style="font-family: Arial; margin: 20px;">
            <h1>🧪 Test CSI Token Manuel</h1>
            
            <h3>🔍 Étapes de diagnostic :</h3>
            <ol>
                <li><a href="/test-oauth-direct" target="_blank">1. Tester OAuth2 avec votre code</a></li>
                <li><a href="/test-csi-multiple" target="_blank">2. Tester génération CSI (multiple configs)</a></li>
                <li><a href="/debug-keyyo" target="_blank">3. Debug API Keyyo</a></li>
            </ol>
            
            <h3>📋 URLs à tester manuellement :</h3>
            <form method="POST">
                <label><strong>URL CSI :</strong></label><br>
                <input type="text" name="csi_url" style="width: 500px; padding: 5px;" 
                       value="https://api.keyyo.com/manager/1.0/services/CSI_ID/csi_token"><br><br>
                
                <label><strong>Payload JSON (optionnel) :</strong></label><br>
                <textarea name="payload" rows="3" style="width: 500px;">{"domain_masks": ["*.up.railway.app"]}</textarea><br><br>
                
                <button type="submit" style="background: #4CAF50; color: white; padding: 10px 20px; border: none;">🚀 Tester</button>
            </form>
            
            <p><a href="/">🏠 Retour accueil</a></p>
        </body>
        </html>
        """
    
    elif request.method == 'POST':
        if not keyyo_client.access_token:
            return jsonify({"error": "Pas d'access token"})
        
        csi_url = request.form.get('csi_url', '')
        payload_str = request.form.get('payload', '')
        
        headers = {
            'Authorization': f'Bearer {keyyo_client.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            if payload_str.strip():
                payload = json.loads(payload_str)
                response = requests.post(csi_url, headers=headers, json=payload, timeout=5)
            else:
                response = requests.post(csi_url, headers=headers, timeout=5)
            
            return jsonify({
                "url": csi_url,
                "payload": payload_str,
                "status_code": response.status_code,
                "response": response.text,
                "headers": dict(response.headers)
            })
            
        except Exception as e:
            return jsonify({"error": str(e)})

# Correction rapide de la méthode dans KeyyoClient
def quick_fix_csi_method():
    """Applique le fix rapide à la méthode generate_csi_token"""
    
    def generate_csi_token_fixed(self):
        """Version corrigée avec URL /manager/ et domain_masks"""
        if not self.access_token:
            logger.error("❌ Pas d'access token disponible")
            return None
        
        services_data = self.get_services()
        if not services_data:
            return None
        
        services_dict = services_data.get('services', services_data) if isinstance(services_data, dict) else {}
        if not services_dict:
            return None
        
        csi_id = list(services_dict.keys())[0]
        logger.info(f"🎯 CSI sélectionné: {csi_id}")
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # URL CORRIGÉE avec /manager/
        csi_token_url = f'https://api.keyyo.com/manager/1.0/services/{csi_id}/csi_token'
        logger.info(f"🚀 URL CORRIGÉE: {csi_token_url}")
        
        # Tenter avec domain_masks d'abord
        payload = {'domain_masks': ['*.up.railway.app', 'web-production-95ca.up.railway.app']}
        
        try:
            response = requests.post(csi_token_url, headers=headers, json=payload, timeout=10)
            logger.info(f"📊 Status avec payload: {response.status_code}")
            logger.info(f"📊 Response avec payload: {response.text}")
            
            if response.status_code == 200:
                try:
                    csi_data = response.json()
                    for field in ['csi_token', 'token', 'access_token']:
                        if field in csi_data and csi_data[field]:
                            self.csi_token = csi_data[field]
                            logger.info(f"✅ CSI Token trouvé: {self.csi_token[:20]}...")
                            return self.csi_token
                except:
                    if len(response.text.strip()) > 10:
                        self.csi_token = response.text.strip()
                        return self.csi_token
            
            # Si échec avec payload, tenter sans
            logger.info("🔄 Tentative sans payload...")
            response2 = requests.post(csi_token_url, headers=headers, timeout=10)
            logger.info(f"📊 Status sans payload: {response2.status_code}")
            logger.info(f"📊 Response sans payload: {response2.text}")
            
            if response2.status_code == 200:
                try:
                    csi_data = response2.json()
                    for field in ['csi_token', 'token', 'access_token']:
                        if field in csi_data and csi_data[field]:
                            self.csi_token = csi_data[field]
                            return self.csi_token
                except:
                    if len(response2.text.strip()) > 10:
                        self.csi_token = response2.text.strip()
                        return self.csi_token
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Erreur: {str(e)}")
            return None
    
    # Remplacer la méthode
    KeyyoClient.generate_csi_token = generate_csi_token_fixed
    logger.info("✅ Méthode generate_csi_token corrigée automatiquement")

# Appliquer le fix au démarrage
quick_fix_csi_method()

# 🔧 PATCH RAPIDE - Ajoutez ceci à la fin de votre app.py existant

def get_csi_token_fixed():
    """Version corrigée de get_csi_token avec URL /manager/ et domain_masks"""
    global keyyo_csi_token
    
    if not keyyo_access_token:
        print("❌ Pas d'access token disponible")
        return None
    
    headers = {
        'Authorization': f'Bearer {keyyo_access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    try:
        # 1. Récupérer les services
        print("🔍 Récupération des services...")
        services_response = requests.get('https://api.keyyo.com/1.0/services', headers=headers, timeout=10)
        
        if services_response.status_code != 200:
            print(f"❌ Erreur services: {services_response.status_code} - {services_response.text}")
            return None
        
        services_data = services_response.json()
        print(f"✅ Services récupérés: {json.dumps(services_data, indent=2)}")
        
        # 2. Extraire le CSI
        if 'services' in services_data:
            services_dict = services_data['services']
        elif isinstance(services_data, dict):
            services_dict = services_data
        else:
            print("❌ Format services non reconnu")
            return None
        
        if not services_dict:
            print("❌ Aucun service trouvé")
            return None
        
        csi_id = list(services_dict.keys())[0]
        print(f"🎯 CSI sélectionné: {csi_id}")
        
        # 3. Générer CSI token avec URL CORRIGÉE
        csi_token_url = f'https://api.keyyo.com/manager/1.0/services/{csi_id}/csi_token'
        print(f"🚀 URL CORRIGÉE: {csi_token_url}")
        
        # Tentative 1: Avec domain_masks
        payload = {'domain_masks': ['*.up.railway.app', 'web-production-95ca.up.railway.app']}
        print(f"🔄 Tentative avec payload: {payload}")
        
        response = requests.post(csi_token_url, headers=headers, json=payload, timeout=10)
        print(f"📊 Status avec payload: {response.status_code}")
        print(f"📊 Response avec payload: {response.text}")
        
        if response.status_code == 200:
            try:
                csi_data = response.json()
                for field in ['csi_token', 'token', 'access_token', 'cti_token']:
                    if field in csi_data and csi_data[field]:
                        keyyo_csi_token = csi_data[field]
                        print(f"✅ CSI Token trouvé dans '{field}': {keyyo_csi_token[:20]}...")
                        return keyyo_csi_token
            except:
                if len(response.text.strip()) > 10:
                    keyyo_csi_token = response.text.strip()
                    print(f"✅ CSI Token (texte): {keyyo_csi_token[:20]}...")
                    return keyyo_csi_token
        
        # Tentative 2: Sans payload
        print("🔄 Tentative sans payload...")
        response2 = requests.post(csi_token_url, headers=headers, timeout=10)
        print(f"📊 Status sans payload: {response2.status_code}")
        print(f"📊 Response sans payload: {response2.text}")
        
        if response2.status_code == 200:
            try:
                csi_data = response2.json()
                for field in ['csi_token', 'token', 'access_token']:
                    if field in csi_data and csi_data[field]:
                        keyyo_csi_token = csi_data[field]
                        print(f"✅ CSI Token trouvé: {keyyo_csi_token[:20]}...")
                        return keyyo_csi_token
            except:
                if len(response2.text.strip()) > 10:
                    keyyo_csi_token = response2.text.strip()
                    return keyyo_csi_token
        
        print(f"❌ Échec des deux tentatives")
        return None
        
    except Exception as e:
        print(f"❌ Erreur: {str(e)}")
        return None

# Remplacer l'ancienne fonction
get_csi_token = get_csi_token_fixed

@app.route('/test-oauth-quick')
def test_oauth_quick():
    """Test rapide avec votre code OAuth2"""
    global keyyo_access_token, keyyo_csi_token
    
    # Réinitialiser
    keyyo_access_token = None
    keyyo_csi_token = None
    
    # Code reçu dans votre URL
    test_code = "e457e407714dad048a8d54ef11319d377a18bf4c"
    
    print(f"🧪 Test avec code: {test_code}")
    
    # Échanger le code
    success = exchange_code_for_token(test_code)
    
    if success:
        print("✅ Access token récupéré, test génération CSI...")
        csi_token = get_csi_token_fixed()
        
        return jsonify({
            "oauth_success": True,
            "access_token": keyyo_access_token[:20] + "..." if keyyo_access_token else None,
            "csi_success": csi_token is not None,
            "csi_token": csi_token[:20] + "..." if csi_token else None,
            "full_csi_token": csi_token,  # Pour copier-coller
            "status": "SUCCESS" if csi_token else "CSI_FAILED"
        })
    else:
        return jsonify({
            "oauth_success": False,
            "error": "Échec échange OAuth2"
        })

@app.route('/test-new-oauth')
def test_new_oauth():
    """Générer un nouveau code OAuth2"""
    return redirect(get_keyyo_auth_url())

print("🔧 Patch appliqué ! Testez sur /test-oauth-quick")
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Démarrage de l'application sur le port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)



