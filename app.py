from flask import Flask, request, jsonify, render_template_string, redirect
import os
import json
import requests
import csv
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

def detect_bank_from_iban(iban):
    """Détecte automatiquement la banque à partir de l'IBAN français"""
    if not iban or len(iban) < 14:
        return "N/A"
    
    # Nettoyer l'IBAN (supprimer espaces et tirets)
    iban_clean = iban.replace(' ', '').replace('-', '').upper()
    
    # Vérifier que c'est un IBAN français
    if not iban_clean.startswith('FR'):
        return "Banque étrangère"
    
    try:
        # Extraire le code banque (positions 4 à 9 dans l'IBAN français)
        code_banque = iban_clean[4:9]
        
        # Dictionnaire des codes banque français principaux
        bank_codes = {
            # Grandes banques nationales
            '30002': 'Crédit Agricole',
            '30003': 'Crédit Agricole',
            '30004': 'Crédit Agricole',
            '30056': 'BRED Banque Populaire',
            '10907': 'BNP Paribas',
            '30004': 'BNP Paribas',
            '14707': 'Banque Populaire',
            '10278': 'Crédit Mutuel',
            '10278': 'CIC',
            '20041': 'Banque Postale',
            '30003': 'Société Générale',
            '30003': 'Crédit du Nord',
            
            # BNP Paribas et filiales
            '30004': 'BNP Paribas',
            '10907': 'BNP Paribas',
            '18206': 'BNP Paribas',
            
            # Société Générale et filiales
            '30003': 'Société Générale',
            '30003': 'Crédit du Nord',
            
            # Crédit Agricole
            '11315': 'Crédit Agricole Centre-Est',
            '13315': 'Crédit Agricole Sud Rhône Alpes',
            '12115': 'Crédit Agricole Alpes Provence',
            '10915': 'Crédit Agricole Aquitaine',
            '18315': 'Crédit Agricole Atlantique Vendée',
            '11815': 'Crédit Agricole Centre France',
            '12515': 'Crédit Agricole Centre Loire',
            '16915': 'Crédit Agricole Centre Ouest',
            '11715': 'Crédit Agricole Charente-Maritime Deux-Sèvres',
            '15215': 'Crédit Agricole Charente-Périgord',
            '19715': 'Crédit Agricole Corse',
            '13815': 'Crédit Agricole des Savoie',
            '14015': 'Crédit Agricole du Finistère',
            '15515': 'Crédit Agricole du Languedoc',
            '10115': 'Crédit Agricole du Morbihan',
            '14715': 'Crédit Agricole Ille-et-Vilaine',
            '17115': 'Crédit Agricole Loire Haute-Loire',
            '16515': 'Crédit Agricole Lorraine',
            '17515': 'Crédit Agricole Midi Pyrénées',
            '13015': 'Crédit Agricole Nord de France',
            '18715': 'Crédit Agricole Nord Est',
            '10715': 'Crédit Agricole Normandie',
            '17915': 'Crédit Agricole Normandie-Seine',
            '15815': 'Crédit Agricole Pyrénées Gascogne',
            '12815': 'Crédit Agricole Sud Méditerranée',
            '14415': 'Crédit Agricole Touraine Poitou',
            
            # Banques Populaires
            '14707': 'Banque Populaire Alsace Lorraine Champagne',
            '17807': 'Banque Populaire Aquitaine Centre Atlantique',
            '12807': 'Banque Populaire Auvergne Rhône Alpes',
            '13807': 'Banque Populaire Bourgogne Franche-Comté',
            '16307': 'Banque Populaire Grand Ouest',
            '15207': 'Banque Populaire Méditerranée',
            '16607': 'Banque Populaire Nord',
            '18307': 'Banque Populaire Occitane',
            '18407': 'Banque Populaire Provençale et Corse',
            '10207': 'Banque Populaire Rives de Paris',
            '14507': 'Banque Populaire Val de France',
            
            # Crédit Mutuel et CIC
            '10278': 'Crédit Mutuel',
            '10906': 'CIC',
            '30006': 'CIC',
            '10096': 'CIC Est',
            '20096': 'CIC Iberbanco',
            '10846': 'CIC Lyonnaise de Banque',
            '11906': 'CIC Nord Ouest',
            '30066': 'CIC Ouest',
            
            # Banques en ligne et néo-banques
            '16798': 'ING Direct',
            '12548': 'Boursorama',
            '14469': 'Monabanq',
            '17515': 'Hello Bank',
            '10907': 'Hello Bank (BNP)',
            
            # Banques spécialisées
            '30056': 'BRED',
            '20041': 'La Banque Postale',
            '15589': 'LCL',
            '30002': 'LCL',
            '13369': 'Caisse d\'Épargne',
            '17906': 'Caisse d\'Épargne',
            
            # Banques régionales
            '20815': 'Banque de Savoie',
            '20845': 'Banque Rhône-Alpes',
            '14559': 'Banque Tarneaud',
            '13489': 'Crédit Coopératif',
            
            # Banques professionnelles
            '30027': 'Banque Palatine',
            '18829': 'Banque Kolb',
            '16229': 'Banque Nuger',
            '17729': 'Banque de l\'Union Européenne',
        }
        
        # Recherche directe du code
        if code_banque in bank_codes:
            return bank_codes[code_banque]
        
        # Recherche par patterns pour les groupes
        if code_banque.startswith('100') or code_banque.startswith('102'):
            return 'Crédit Mutuel / CIC'
        elif code_banque.startswith('109'):
            return 'BNP Paribas'
        elif code_banque.startswith('300'):
            if code_banque.startswith('30003'):
                return 'Société Générale'
            elif code_banque.startswith('30004'):
                return 'BNP Paribas'
            elif code_banque.startswith('30002'):
                return 'Crédit Agricole'
            else:
                return 'Société Générale (groupe)'
        elif code_banque.endswith('15'):
            return 'Crédit Agricole (région)'
        elif code_banque.endswith('07'):
            return 'Banque Populaire (région)'
        elif code_banque.startswith('200'):
            return 'La Banque Postale'
        elif code_banque.startswith('139') or code_banque.startswith('179'):
            return 'Caisse d\'Épargne'
        else:
            return f"Banque française (code: {code_banque})"
            
    except (ValueError, IndexError):
        return "IBAN invalide"

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
                    banque = detect_bank_from_iban(iban)
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
            detected_bank = detect_bank_from_iban(iban)
            response_message = f"""
🏦 <b>ANALYSE IBAN</b>

💳 IBAN: <code>{iban}</code>
🏛️ Banque détectée: <b>{detected_bank}</b>

🤖 <i>Détection automatique basée sur le code banque français</i>
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
▪️ Les notifications en temps réel
▪️ 🤖 Détection automatique des banques depuis IBAN
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
            "client_status": client_info['statut'],
            "bank_detected": client_info.get('banque', 'N/A') not in ['N/A', '']
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
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
        .success { color: #4CAF50; font-weight: bold; }
        .error { color: #f44336; font-weight: bold; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
        .info-box { background: #e8f5e8; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .new-feature { background: #fff3e0; border-left: 4px solid #ff9800; padding: 15px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram</h1>
            <p class="success">✅ Serveur Railway actif 24/7 - Bot configuré</p>
            <div class="new-feature">
                <strong>🆕 NOUVELLE FONCTIONNALITÉ :</strong> 🏦 Détection automatique de la banque à partir de l'IBAN français !
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
                        <strong>🤖 AUTO-DÉTECTION BANQUE :</strong> Si la colonne <code>banque</code> est vide mais qu'un <code>iban</code> français est présent, la banque sera automatiquement détectée !
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
            <li><code>/stats</code> - Statistiques de la campagne</li>
            <li><code>/help</code> - Aide et liste des commandes</li>
        </ul>

        <div class="info-box">
            <h3>🎯 Comment ça marche :</h3>
            <ol>
                <li>📂 Uploadez votre fichier CSV avec les clients</li>
                <li>🏦 Les banques sont automatiquement détectées depuis les IBAN français</li>
                <li>📞 Configurez l'URL OVH CTI</li>
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
    filename=upload_stats["filename"]
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
    """Test de la détection d'IBAN"""
    test_ibans = [
        "FR1420041010050500013M02606",  # La Banque Postale
        "FR7630003000540000000001234",  # Société Générale
        "FR1411315000100000000000000",  # Crédit Agricole
        "FR7610907000000000000000000",  # BNP Paribas
        "FR7617206000000000000000000",  # BRED
    ]
    
    results = []
    for iban in test_ibans:
        bank = detect_bank_from_iban(iban)
        results.append({"iban": iban, "bank_detected": bank})
    
    return jsonify({
        "test_results": results,
        "function_status": "OK",
        "total_tests": len(test_ibans)
    })

@app.route('/test-ovh-cgi')
def test_ovh_cgi():
    """Test du webhook OVH format CGI"""
    from urllib.parse import urlencode
    
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
        "service": "webhook-ovh-telegram",
        "telegram_configured": bool(TELEGRAM_TOKEN and CHAT_ID),
        "clients_loaded": upload_stats["total_clients"],
        "iban_detection": "enabled",
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
