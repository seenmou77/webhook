from flask import Flask, request, jsonify
import os
import json
import requests
from datetime import datetime

app = Flask(__name__)

# Configuration Telegram
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '7822148813:AAEhWJWToLUY5heVP1G_yqM1Io-vmAMlbLg')
CHAT_ID = os.environ.get('CHAT_ID', '-1002652961145')

def get_client_info(phone_number):
    """Récupère les infos client (base de données simulée)"""
    # Base de données clients simulée
    clients_db = {
        "0767328146": {
            "nom": "CHAIZE",
            "prenom": "GWENDOLINE",
            "banque": "Boursorama Banque",
            "swift": "BOUSFRPPXXXX",
            "iban": "FR76406188027900040096368?8",
            "adresse": "47 RUE DE SAINT CYR",
            "code_postal": "69009",
            "ville": "LYON",
            "email": "gwendoline.chaize@gmail.com",
            "telephone": "0767328146",
            "sexe": "M",
            "date_naissance": "Non renseigné",
            "lieu_naissance": "Non renseigné",
            "contrat": "Premium Support",
            "derniere_intervention": "20/05/2025"
        },
        "0123456789": {
            "nom": "MARTIN",
            "prenom": "DUPONT",
            "banque": "BNP Paribas",
            "swift": "BNPAFRPPXXX",
            "iban": "FR1420041010050500013M02606",
            "adresse": "15 Avenue des Champs",
            "code_postal": "75008",
            "ville": "PARIS",
            "email": "martin.dupont@tech-solutions.fr",
            "telephone": "0123456789",
            "sexe": "M",
            "date_naissance": "15/03/1985",
            "lieu_naissance": "Paris",
            "contrat": "Premium Support",
            "derniere_intervention": "15/05/2025"
        }
    }
    
    return clients_db.get(phone_number, {
        "nom": "INCONNU",
        "prenom": "CLIENT",
        "banque": "N/A",
        "swift": "N/A", 
        "iban": "N/A",
        "adresse": "N/A",
        "code_postal": "N/A",
        "ville": "N/A",
        "email": "N/A",
        "telephone": phone_number,
        "sexe": "N/A",
        "date_naissance": "N/A",
        "lieu_naissance": "N/A",
        "contrat": "À vérifier",
        "derniere_intervention": "N/A"
    })

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
        return f"""
🔔 <b>APPEL ENTRANT</b>
📞 Numéro: <code>{client_info['telephone']}</code>
🕐 Heure: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}

👤 <b>FICHE CLIENT</b>
▪️ Nom: {client_info['nom']}
▪️ Prénom: {client_info['prenom']}
▪️ Email: {client_info['email']}
▪️ Contrat: {client_info['contrat']}
▪️ Dernière intervention: {client_info['derniere_intervention']}

🏠 <b>ADRESSE</b>
▪️ {client_info['adresse']}
▪️ {client_info['code_postal']} {client_info['ville']}
        """
    else:  # Recherche manuelle
        return f"""
📋 <b>RÉSULTAT TROUVÉ :</b>
🙋 Nom : <b>{client_info['nom']}</b>
👤 Prénom : <b>{client_info['prenom']}</b>
🏦 Banque : {client_info['banque']}
💳 SWIFT : <code>{client_info['swift']}</code>
🏦 IBAN : <code>{client_info['iban']}</code>
🏠 Adresse : {client_info['adresse']}
📮 Code postal : {client_info['code_postal']}
🏘️ Ville : {client_info['ville']}
📧 Email : {client_info['email']}
📞 Téléphone : <code>{client_info['telephone']}</code>
👥 Sexe : {client_info['sexe']}
🎂 Date de naissance : {client_info['date_naissance']}
📍 Lieu de naissance : {client_info['lieu_naissance']}

💼 <b>CONTRAT</b>
▪️ Type: {client_info['contrat']}
▪️ Dernière intervention: {client_info['derniere_intervention']}
        """

def process_telegram_command(message_text, chat_id):
    """Traite les commandes Telegram reçues"""
    try:
        if message_text.startswith('/numero '):
            # Extraction du numéro de téléphone
            phone_number = message_text.replace('/numero ', '').strip()
            
            # Recherche du client
            client_info = get_client_info(phone_number)
            
            # Formatage et envoi de la réponse
            response_message = format_client_message(client_info, context="recherche")
            send_telegram_message(response_message)
            
            return {"status": "command_processed", "command": "numero", "phone": phone_number}
            
        elif message_text.startswith('/help'):
            help_message = """
🤖 <b>COMMANDES DISPONIBLES</b>

📞 <code>/numero 0123456789</code>
   → Affiche la fiche client complète

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

@app.route('/webhook/ovh', methods=['POST'])
def ovh_webhook():
    """Webhook pour recevoir les appels OVH"""
    try:
        data = request.get_json()
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        # Extraction des données d'appel
        caller_number = data.get('callerIdNumber', 'Inconnu')
        call_status = data.get('status', 'incoming')
        
        print(f"🔔 [{timestamp}] Appel de: {caller_number}")
        print(f"📋 Données: {json.dumps(data, indent=2)}")
        
        # Récupération fiche client
        client_info = get_client_info(caller_number)
        
        # Message Telegram formaté
        telegram_message = format_client_message(client_info, context="appel")
        telegram_message += f"\n📊 Statut: {call_status}"
        
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
            "telegram_sent": telegram_result is not None,
            "client": f"{client_info['prenom']} {client_info['nom']}"
        })
        
    except Exception as e:
        print(f"❌ Erreur webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    """Webhook pour recevoir les commandes Telegram"""
    try:
        data = request.get_json()
        
        # Vérification de la présence d'un message
        if 'message' in data and 'text' in data['message']:
            message_text = data['message']['text']
            chat_id = data['message']['chat']['id']
            user_name = data['message']['from'].get('first_name', 'Utilisateur')
            
            print(f"📱 Commande reçue de {user_name}: {message_text}")
            
            # Traitement de la commande
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
    return f"""
    <h1>🤖 Webhook OVH-Telegram</h1>
    <p>✅ Serveur Railway actif 24/7</p>
    <p>📡 Endpoint OVH: /webhook/ovh</p>
    <p>📱 Endpoint Telegram: /webhook/telegram</p>
    <p>🤖 Bot configuré pour chat {CHAT_ID}</p>
    
    <h2>🔧 Configuration</h2>
    <p><a href="/setup-telegram-webhook">⚙️ Configurer webhook Telegram</a></p>
    <p><a href="/test-telegram">📧 Test envoi Telegram</a></p>
    <p><a href="/test-command">🎯 Test commande /numero</a></p>
    
    <h2>📋 Commandes disponibles</h2>
    <ul>
        <li><code>/numero 0767328146</code> - Affiche fiche client</li>
        <li><code>/help</code> - Aide</li>
    </ul>
    
    <p>🕐 Dernière mise à jour: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}</p>
    """

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
    result = process_telegram_command("/numero 0767328146", CHAT_ID)
    return jsonify({"test_result": result})

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "service": "webhook-ovh",
        "telegram_configured": bool(TELEGRAM_TOKEN and CHAT_ID)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
