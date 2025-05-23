from flask import Flask, request, jsonify
import os
import json
from datetime import datetime

app = Flask(__name__)

@app.route('/webhook/ovh', methods=['POST'])
def ovh_webhook():
    """Webhook pour recevoir les appels OVH"""
    try:
        data = request.get_json()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Log de l'appel reçu
        caller_number = data.get('callerIdNumber', 'Inconnu')
        print(f"🔔 [{timestamp}] Appel de: {caller_number}")
        print(f"📋 Données complètes: {json.dumps(data, indent=2)}")
        
        # TODO: Ici on ajoutera la logique Telegram
        
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "caller": caller_number
        })
    except Exception as e:
        print(f"❌ Erreur webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    return """
    <h1>🤖 Webhook OVH-Telegram</h1>
    <p>✅ Serveur Railway actif 24/7</p>
    <p>📡 Endpoint: /webhook/ovh</p>
    <p>🕐 Dernière mise à jour: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
    """

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "webhook-ovh"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)