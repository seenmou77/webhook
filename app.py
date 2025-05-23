#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - Application Flask complète avec détection automatique des banques
Webhook OVH-Telegram avec gestion intelligente des clients CSV
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
from typing import Dict, List, Optional, Tuple

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
        # Dictionnaire étendu des codes banque français (codes 5 chiffres)
        self.bank_codes = {
            # Grandes banques nationales
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
            
            # Banques en ligne et néobanques
            '20041': 'Boursorama Banque',
            '16967': 'Hello Bank (BNP Paribas)',
            '30056': 'Monabanq',
            '18206': 'Fortuneo',
            '19138': 'BforBank (Crédit Agricole)',
            '20395': 'ING Direct',
            '16586': 'Revolut',
            '14437': 'N26',
            '16958': 'Nickel (Compte-Nickel)',
            '30003': 'Orange Bank',
            '20041': 'Ma French Bank',
            
            # Banques régionales et spécialisées
            '17515': 'CIC',
            '30027': 'Crédit du Nord',
            '13135': 'Crédit Coopératif',
            '27052': 'BRED',
            '30066': 'Crédit Agricole Consumer Finance',
            '30788': 'Natixis',
            '18327': 'Axa Banque',
            '10863': 'CCF (Crédit Commercial de France)',
            '12548': 'Crédit Industriel et Commercial',
            '14707': 'Banque Neuflize OBC',
            '75012': 'Crédit Agricole International',
            
            # Codes spécifiques par région
            '16515': 'Crédit Mutuel Centre Est Europe',
            '10278': 'Crédit Mutuel de Bretagne',
            '15589': 'Crédit Mutuel Sud-Est',
            '30056': 'Caisse d\'Épargne Ile-de-France',
            '18206': 'Caisse d\'Épargne CEPAC',
            '16515': 'CIC Est',
            '30027': 'Crédit du Nord Lille',
            
            # Autres établissements financiers
            '17806': 'Crédit Mutuel Océan',
            '18315': 'Crédit Mutuel Loire-Atlantique',
            '19455': 'Crédit Mutuel Maine-Anjou',
            '14707': 'Banque Palatine',
            '30002': 'LCL Banque Privée',
            '11315': 'Crédit Mutuel Nord Europe',
            '17218': 'Crédit Mutuel Dauphiné-Vivarais',
        }
    
    def extract_bank_code(self, iban: str) -> Optional[str]:
        """
        Extrait le code banque de l'IBAN français
        
        Args:
            iban (str): IBAN français
            
        Returns:
            str: Code banque (5 chiffres) ou None si invalide
        """
        if not iban or not isinstance(iban, str):
            return None
            
        # Nettoyer l'IBAN (supprimer espaces et convertir en majuscules)
        clean_iban = re.sub(r'\s+', '', iban.upper())
        
        # Vérifier format IBAN français
        if not re.match(r'^FR\d{2}\d{10,}', clean_iban):
            return None
            
        # Extraire le code banque (caractères 4 à 8 après FR76)
        try:
            bank_code = clean_iban[4:9]
            return bank_code if bank_code.isdigit() else None
        except IndexError:
            return None
    
    def detect_bank(self, iban: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Détecte le nom de la banque à partir de l'IBAN
        
        Args:
            iban (str): IBAN français
            
        Returns:
            tuple: (nom_banque, code_banque) ou (None, None) si non trouvé
        """
        bank_code = self.extract_bank_code(iban)
        
        if not bank_code:
            return None, None
            
        # Recherche exacte
        if bank_code in self.bank_codes:
            return self.bank_codes[bank_code], bank_code
            
        # Recherche approximative (codes proches)
        for code, name in self.bank_codes.items():
            if bank_code.startswith(code[:3]):  # 3 premiers chiffres
                return f"{name} (code approx.)", bank_code
                
        return f"Banque inconnue (code: {bank_code})", bank_code

# Instance globale du détecteur de banques
bank_detector = BankDetector()

def is_old_csv_format(headers):
    """Détecte si le CSV utilise l'ancien format avec mapping incorrect"""
    headers_lower = [h.lower().strip() for h in headers if h]
    return 'banque' in headers_lower and 'swift' in headers_lower

def fix_csv_column_mapping(row_data, headers):
    """
    Corrige le mapping des colonnes pour l'ancien format problématique
    
    Args:
        row_data (list): Données de la ligne CSV
        headers (list): En-têtes du CSV
        
    Returns:
        dict: Données correctement mappées
    """
    if not is_old_csv_format(headers):
        # Format normal - mapping direct
        normalized_row = {}
        for i, header in enumerate(headers):
            if i < len(row_data) and header:
                normalized_row[header.lower().strip()] = str(row_data[i]).strip() if row_data[i] else ""
        return normalized_row
    
    # Ancien format - correction du mapping
    corrected_mapping = {
        0: 'telephone',        # Position 0: téléphone ✓
        1: 'nom',             # Position 1: nom ✓
        2: 'prenom',          # Position 2: prénom ✓
        3: 'email',           # Position 3: email ✓
        4: 'entreprise',      # Position 4: entreprise ✓
        5: 'adresse',         # Position 5: adresse ✓
        6: 'ville',           # Position 6: ville ✓
        7: 'code_postal',     # Position 7: code postal ✓
        8: 'iban',            # Position 8: IBAN (pas banque!)
        9: 'iban_backup',     # Position 9: IBAN dupliqué
        10: 'sexe',           # Position 10: sexe (pas iban!)
        11: 'date_naissance', # Position 11: date de naissance (pas sexe!)
        12: 'nationalite',    # Position 12: nationalité (pas date!)
        13: 'statut',         # Position 13: statut (pas lieu!)
        14: 'lieu_naissance', # Position 14: lieu de naissance (pas profession!)
        15: 'profession',     # Position 15: profession (pas nationalité!)
        16: 'nationalite_finale', # Position 16: nationalité finale (pas situation!)
        17: 'situation_familiale'  # Position 17: situation familiale (pas statut!)
    }
    
    corrected_row = {}
    for i, value in enumerate(row_data):
        if i in corrected_mapping:
            field_name = corrected_mapping[i]
            corrected_row[field_name] = str(value).strip() if value else ""
    
    # Pour l'ancien format, utiliser nationalite_finale comme nationalite principale
    if 'nationalite_finale' in corrected_row and corrected_row['nationalite_finale']:
        corrected_row['nationalite'] = corrected_row['nationalite_finale']
    
    return corrected_row

def detect_and_add_bank_info(client_data):
    """
    Détecte automatiquement la banque à partir de l'IBAN et ajoute les infos
    
    Args:
        client_data (dict): Données client
        
    Returns:
        dict: Données client enrichies avec info banque
    """
    iban = client_data.get('iban', '') or client_data.get('iban_backup', '')
    
    if iban and iban != 'N/A':
        bank_name, bank_code = bank_detector.detect_bank(iban)
        
        if bank_name:
            client_data['banque_detectee'] = bank_name
            client_data['code_banque'] = bank_code or ''
            
            # Si pas de banque manuelle, utiliser la détection
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
    """Charge les clients depuis un contenu CSV avec détection automatique des banques"""
    global clients_database, upload_stats
    
    clients_database = {}
    banks_detected = 0
    
    # Lecture CSV avec gestion des erreurs
    try:
        # Utilisation du module csv de Python
        csv_reader = csv.reader(io.StringIO(file_content))
        
        # Lire l'en-tête
        headers = next(csv_reader, [])
        if not headers:
            raise ValueError("Fichier CSV vide ou sans en-têtes")
        
        print(f"🔍 En-têtes détectés: {headers}")
        
        # Détecter format et adapter
        is_old_format = is_old_csv_format(headers)
        if is_old_format:
            print("🔧 Ancien format CSV détecté - Application du mapping correct...")
        
        for row_data in csv_reader:
            if not row_data:  # Ignorer lignes vides
                continue
            
            # Mapping correct selon le format
            if is_old_format:
                normalized_row = fix_csv_column_mapping(row_data, headers)
            else:
                # Format normal
                normalized_row = {}
                for i, header in enumerate(headers):
                    if i < len(row_data) and header:
                        normalized_row[header.lower().strip()] = str(row_data[i]).strip() if row_data[i] else ""
            
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
                client_data = {
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
                    
                    # Informations bancaires
                    "banque": normalized_row.get('banque', ''),
                    "swift": normalized_row.get('swift', ''),
                    "iban": normalized_row.get('iban', ''),
                    
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
                
                # 🏦 DÉTECTION AUTOMATIQUE DE LA BANQUE
                client_data = detect_and_add_bank_info(client_data)
                
                # Compter les banques détectées
                if client_data.get('banque_detectee') and client_data['banque_detectee'] not in ['Non détectée', 'Pas d\'IBAN']:
                    banks_detected += 1
                
                clients_database[telephone] = client_data
        
        total_clients = len(clients_database)
        detection_rate = (banks_detected / total_clients * 100) if total_clients > 0 else 0
        
        upload_stats["total_clients"] = total_clients
        upload_stats["banks_detected"] = banks_detected
        upload_stats["detection_rate"] = round(detection_rate, 1)
        upload_stats["last_upload"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        print(f"✅ {total_clients} clients chargés")
        print(f"🏦 {banks_detected} banques détectées ({detection_rate:.1f}%)")
        
        return total_clients
        
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
    """Formate un message client pour Telegram avec info banque détectée"""
    if context == "appel":
        emoji_statut = "📞" if client_info['statut'] != "Non référencé" else "❓"
        
        # Emoji banque selon détection
        banque_detectee = client_info.get('banque_detectee', 'N/A')
        if banque_detectee and banque_detectee != 'N/A' and banque_detectee != 'Non détectée':
            bank_emoji = "🏦✅"
        else:
            bank_emoji = "🏦❓"
            
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

{bank_emoji} <b>INFORMATIONS BANCAIRES</b>
▪️ Banque détectée: <b>{banque_detectee}</b>
▪️ Code banque: <code>{client_info.get('code_banque', 'N/A')}</code>
▪️ IBAN: <code>{client_info.get('iban', 'N/A')}</code>
▪️ SWIFT: <code>{client_info.get('swift', 'N/A')}</code>

📊 <b>CAMPAGNE</b>
▪️ Statut: <b>{client_info['statut']}</b>
▪️ Nb appels: {client_info['nb_appels']}
▪️ Dernier appel: {client_info['dernier_appel'] or 'Premier appel'}
        """
    else:  # Recherche manuelle
        banque_detectee = client_info.get('banque_detectee', 'N/A')
        bank_emoji = "🏦✅" if banque_detectee and banque_detectee not in ['N/A', 'Non détectée', 'Pas d\'IBAN'] else "🏦❓"
        
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

{bank_emoji} <b>INFORMATIONS BANCAIRES</b>
🏛️ Banque détectée : <b>{banque_detectee}</b>
🔢 Code banque : <code>{client_info.get('code_banque', 'N/A')}</code>
🏦 IBAN : <code>{client_info.get('iban', 'N/A')}</code>
💳 SWIFT : <code>{client_info.get('swift', 'N/A')}</code>

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
            detected_banks = len([c for c in clients_database.values() 
                                if c.get('banque_detectee') and c['banque_detectee'] not in ['Non détectée', 'Pas d\'IBAN']])
            
            stats_message = f"""
📊 <b>STATISTIQUES CAMPAGNE</b>

👥 Clients total: {upload_stats['total_clients']}
📁 Dernier upload: {upload_stats['last_upload'] or 'Aucun'}
📋 Fichier: {upload_stats['filename'] or 'Aucun'}

🏦 <b>DÉTECTION BANQUES</b>
▪️ Banques détectées: {upload_stats.get('banks_detected', 0)} ({upload_stats.get('detection_rate', 0)}%)
▪️ Taux de détection: {upload_stats.get('detection_rate', 0)}%

📞 <b>APPELS DU JOUR</b>
▪️ Clients appelants: {len([c for c in clients_database.values() if c['dernier_appel'] and c['dernier_appel'].startswith(datetime.now().strftime('%d/%m/%Y'))])}
▪️ Nouveaux contacts: {len([c for c in clients_database.values() if c['nb_appels'] == 0])}
            """
            send_telegram_message(stats_message)
            return {"status": "stats_sent"}
            
        elif message_text.startswith('/banques'):
            # Statistiques des banques détectées
            bank_stats = {}
            for client in clients_database.values():
                bank = client.get('banque_detectee', 'Non détectée')
                if bank and bank != 'Non détectée' and bank != 'Pas d\'IBAN':
                    bank_stats[bank] = bank_stats.get(bank, 0) + 1
            
            top_banks = sorted(bank_stats.items(), key=lambda x: x[1], reverse=True)[:10]
            
            banks_message = "🏦 <b>TOP BANQUES DÉTECTÉES</b>\n\n"
            for bank, count in top_banks:
                percentage = round(count / upload_stats['total_clients'] * 100, 1)
                banks_message += f"▪️ {bank}: {count} clients ({percentage}%)\n"
            
            send_telegram_message(banks_message)
            return {"status": "banks_stats_sent"}
            
        elif message_text.startswith('/help'):
            help_message = """
🤖 <b>COMMANDES DISPONIBLES</b>

📞 <code>/numero 0123456789</code>
   → Affiche la fiche client complète avec banque détectée

📊 <code>/stats</code>
   → Statistiques de la campagne et détection banques

🏦 <code>/banques</code>
   → Répartition des clients par banque détectée

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
            print(f"📞 Appelé: {called_number}")
            print(f"📋 Type: {event_type}")
        else:
            data = request.get_json() or {}
            caller_number = data.get('callerIdNumber', request.args.get('caller', 'Inconnu'))
            call_status = data.get('status', 'incoming')
            
            print(f"🔔 [{timestamp}] Appel JSON:")
            print(f"📋 Données: {json.dumps(data, indent=2)}")
        
        # Récupération fiche client avec info banque
        client_info = get_client_info(caller_number)
        
        # Message Telegram formaté avec banque détectée
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
    <title>🤖 Webhook OVH-Telegram - Gestion Clients avec Détection Banques</title>
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
        .btn-danger { background: #f44336; }
        .btn-success { background: #4CAF50; }
        .btn-bank { background: #FF9800; }
        .links { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
        .success { color: #4CAF50; font-weight: bold; }
        .error { color: #f44336; font-weight: bold; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
        .info-box { background: #e8f5e8; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .bank-feature { background: #fff3e0; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #FF9800; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Webhook OVH-Telegram 🏦</h1>
            <p class="success">✅ Serveur Railway actif 24/7 - Bot configuré avec détection automatique des banques</p>
        </div>

        <div class="bank-feature">
            <h3>🚀 NOUVELLE FONCTIONNALITÉ : Détection Automatique des Banques</h3>
            <p><strong>🏦 Le système analyse automatiquement les IBANs et détecte :</strong></p>
            <p>✅ BNP Paribas • Société Générale • Crédit Agricole • LCL • Boursorama • Hello Bank • ING Direct • Revolut • N26 • et 40+ autres banques</p>
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
            <h2>📂 Upload fichier clients (CSV uniquement)</h2>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <div class="info-box">
                    <p><strong>📋 Format supporté:</strong> CSV (.csv)</p>
                    <p><strong>🔥 Colonne obligatoire:</strong> <code>telephone</code> (ou tel, phone, numero)</p>
                    <p><strong>🏦 Détection banque:</strong> <code>iban</code> (analysé automatiquement)</p>
                    <p><strong>✨ Colonnes optionnelles:</strong></p>
                    <ul style="text-align: left; max-width: 800px; margin: 0 auto;">
                        <li><strong>Identité:</strong> nom, prenom, sexe, date_naissance, lieu_naissance, nationalite</li>
                        <li><strong>Contact:</strong> email, adresse, ville, code_postal</li>
                        <li><strong>Professionnel:</strong> entreprise, profession</li>
                        <li><strong>Bancaire:</strong> banque, swift, iban (🏦 <strong>IBAN analysé automatiquement</strong>)</li>
                        <li><strong>Divers:</strong> statut, situation_familiale</li>
                    </ul>
                    <p><strong>🔧 Gestion intelligente:</strong> Le système détecte automatiquement l'ancien format avec mapping incorrect et le corrige !</p>
                </div>
                <input type="file" name="file" accept=".csv" required style="margin: 10px 0;">
                <br>
                <button type="submit" class="btn btn-success">📁 Charger fichier CSV</button>
            </form>
        </div>

        <h2>🔧 Tests & Configuration</h2>
        <div class="links">
            <a href="/clients" class="btn">👥 Voir clients</a>
            <a href="/banks-stats" class="btn btn-bank">🏦 Stats banques</a>
            <a href="/setup-telegram-webhook" class="btn">⚙️ Config Telegram</a>
            <a href="/test-telegram" class="btn">📧 Test Telegram</a>
            <a href="/test-command" class="btn">🎯 Test /numero</a>
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
            <li><code>/numero 0123456789</code> - Affiche fiche client complète avec banque détectée</li>
            <li><code>/stats</code> - Statistiques campagne + détection banques</li>
            <li><code>/banques</code> - Répartition des clients par banque</li>
            <li><code>/help</code> - Aide et liste des commandes</li>
        </ul>

        <div class="info-box">
            <h3>🎯 Comment ça marche :</h3>
            <ol>
                <li>📂 Uploadez votre fichier CSV avec les clients</li>
                <li>🏦 Le système détecte automatiquement les banques via les IBANs</li>
                <li>📞 Configurez l'URL OVH CTI</li>
                <li>✅ Chaque appel entrant affiche la fiche client + banque dans Telegram</li>
                <li>🔍 Utilisez <code>/numero XXXXXXXXXX</code> pour rechercher un client</li>
                <li>📊 Consultez <code>/banques</code> pour voir la répartition par banque</li>
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

@app.route('/banks-stats')
def banks_stats():
    """Page des statistiques des banques détectées"""
    bank_stats = {}
    total_clients = len(clients_database)
    
    for client in clients_database.values():
        bank = client.get('banque_detectee', 'Non détectée')
        if bank:
            bank_stats[bank] = bank_stats.get(bank, 0) + 1
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>🏦 Statistiques Banques Détectées</title>
    <meta charset="UTF-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1000px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .header { text-align: center; margin-bottom: 30px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .bank-card { background: #e8f5e8; padding: 20px; border-radius: 8px; border-left: 4px solid #4CAF50; }
        .bank-unknown { background: #ffebee; border-left-color: #f44336; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; border-radius: 5px; text-decoration: none; display: inline-block; margin: 5px; }
        .percentage-bar { background: #e0e0e0; border-radius: 10px; height: 8px; margin: 10px 0; }
        .percentage-fill { background: #4CAF50; height: 100%; border-radius: 10px; transition: width 0.3s; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background: #f2f2f2; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏦 Statistiques des Banques Détectées</h1>
            <p>Total clients: <strong>{{ total_clients }}</strong> | Banques détectées: <strong>{{ banks_count }}</strong></p>
            <a href="/" class="btn">🏠 Accueil</a>
            <a href="/clients" class="btn">👥 Voir clients</a>
        </div>
        
        <table>
            <tr>
                <th>🏦 Banque</th>
                <th>👥 Nombre de clients</th>
                <th>📊 Pourcentage</th>
                <th>📈 Répartition</th>
            </tr>
            {% for bank, count in banks_sorted %}
            <tr class="{% if 'Non détectée' in bank or 'inconnue' in bank %}bank-unknown{% endif %}">
                <td><strong>{{ bank }}</strong></td>
                <td style="text-align: center;">{{ count }}</td>
                <td style="text-align: center;">{{ "%.1f"|format(count / total_clients * 100) }}%</td>
                <td>
                    <div class="percentage-bar">
                        <div class="percentage-fill" style="width: {{ count / total_clients * 100 }}%"></div>
                    </div>
                </td>
            </tr>
            {% endfor %}
        </table>
        
        <div style="margin-top: 30px; padding: 20px; background: #e3f2fd; border-radius: 8px;">
            <h3>📊 Résumé de la détection :</h3>
            <p>✅ <strong>{{ detected_count }}</strong> clients avec banque détectée ({{ "%.1f"|format(detected_count / total_clients * 100) }}%)</p>
            <p>❓ <strong>{{ undetected_count }}</strong> clients sans détection ({{ "%.1f"|format(undetected_count / total_clients * 100) }}%)</p>
            <p>🎯 <strong>Taux de réussite :</strong> {{ "%.1f"|format(detection_rate) }}%</p>
        </div>
    </div>
</body>
</html>
    """,
    total_clients=total_clients,
    banks_count=len(bank_stats),
    banks_sorted=sorted(bank_stats.items(), key=lambda x: x[1], reverse=True),
    detected_count=len([c for c in clients_database.values() 
                       if c.get('banque_detectee') and c['banque_detectee'] not in ['Non détectée', 'Pas d\'IBAN']]),
    undetected_count=len([c for c in clients_database.values() 
                         if not c.get('banque_detectee') or c['banque_detectee'] in ['Non détectée', 'Pas d\'IBAN']]),
    detection_rate=(len([c for c in clients_database.values() 
                        if c.get('banque_detectee') and c['banque_detectee'] not in ['Non détectée', 'Pas d\'IBAN']]) / total_clients * 100) if total_clients > 0 else 0
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload et traitement du fichier CSV avec détection banques"""
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
        else:
            return jsonify({"error": "Seuls les fichiers CSV sont supportés dans cette version"}), 400
        
        return jsonify({
            "status": "success",
            "message": f"{nb_clients} clients chargés avec succès",
            "filename": filename,
            "total_clients": nb_clients,
            "banks_detected": upload_stats.get("banks_detected", 0),
            "detection_rate": f"{upload_stats.get('detection_rate', 0)}%"
        })
        
    except Exception as e:
        return jsonify({"error": f"Erreur upload: {str(e)}"}), 500

@app.route('/clients')
def view_clients():
    """Interface de visualisation des clients avec info banques"""
    search = request.args.get('search', '')
    
    if search:
        search_lower = search.lower()
        filtered_clients = {k: v for k, v in clients_database.items() 
                          if search_lower in f"{v['nom']} {v['prenom']} {v['telephone']} {v['entreprise']} {v['email']} {v['ville']} {v.get('banque_detectee', '')}".lower()}
    else:
        # Limite à 100 pour la performance
        filtered_clients = dict(list(clients_database.items())[:100])
    
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>👥 Gestion Clients avec Banques Détectées</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1800px; margin: 0 auto; }
        .search { margin-bottom: 20px; }
        .search input { padding: 10px; width: 400px; border: 1px solid #ddd; border-radius: 5px; }
        .btn { background: #2196F3; color: white; padding: 10px 20px; border: none; cursor: pointer; border-radius: 5px; margin: 5px; text-decoration: none; display: inline-block; }
        .btn:hover { background: #1976D2; }
        .btn-bank { background: #FF9800; }
        table { width: 100%; border-collapse: collapse; font-size: 11px; }
        th, td { border: 1px solid #ddd; padding: 4px; text-align: left; }
        th { background: #f2f2f2; position: sticky; top: 0; }
        .status-prospect { background: #fff3e0; }
        .status-client { background: #e8f5e8; }
        .bank-detected { background: #e8f5e8; font-weight: bold; }
        .bank-unknown { background: #ffebee; color: #d32f2f; }
        .stats { background: #f0f4f8; padding: 15px; margin-bottom: 20px; border-radius: 5px; }
        .table-container { max-height: 600px; overflow-y: auto; }
        .highlight { background: yellow; }
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
        <h1>👥 Base Clients avec Banques Détectées ({{ total_clients }} total)</h1>
        
        <div class="stats">
            <strong>📊 Statistiques:</strong> 
            Total: {{ total_clients }} | 
            Affichés: {{ displayed_count }} |
            🏦 Banques détectées: {{ banks_detected }} ({{ detection_rate }}%) |
            Avec appels: {{ with_calls }} |
            Aujourd'hui: {{ today_calls }}
        </div>
        
        <div class="search">
            <form method="GET">
                <input type="text" name="search" placeholder="Rechercher (nom, téléphone, entreprise, email, ville, banque...)" value="{{ search }}">
                <button type="submit" class="btn">🔍 Rechercher</button>
                <a href="/clients" class="btn">🔄 Tout afficher</a>
                <a href="/banks-stats" class="btn btn-bank">🏦 Stats banques</a>
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
                    <th>🏦 Banque Détectée</th>
                    <th>🔢 Code</th>
                    <th>💳 IBAN</th>
                    <th>📊 Statut</th>
                    <th>📈 Appels</th>
                    <th>🕐 Dernier</th>
                </tr>
                {% for tel, client in clients %}
                <tr class="status-{{ client.statut.lower().replace(' ', '') }}">
                    <td><strong>{{ tel }}</strong></td>
                    <td>{{ client.nom }}</td>
                    <td>{{ client.prenom }}</td>
                    <td>{{ client.entreprise }}</td>
                    <td>{{ client.email }}</td>
                    <td>{{ client.ville }}</td>
                    <td class="{% if client.get('banque_detectee') and client.banque_detectee not in ['Non détectée', 'Pas d\'IBAN'] %}bank-detected{% else %}bank-unknown{% endif %}">
                        {{ client.get('banque_detectee', 'N/A') }}
                    </td>
                    <td><code>{{ client.get('code_banque', '') }}</code></td>
                    <td style="font-size: 10px;"><code>{{ client.get('iban', '')[:15] }}{% if client.get('iban') and client.iban|length > 15 %}...{% endif %}</code></td>
                    <td><strong>{{ client.statut }}</strong></td>
                    <td style="text-align: center;">{{ client.nb_appels }}</td>
                    <td>{{ client.dernier_appel or '-' }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        {% if displayed_count >= 100 and total_clients > 100 %}
        <p style="color: orange;"><strong>⚠️ Affichage limité aux 100 premiers clients. Utilisez la recherche pour filtrer.</strong></p>
        {% endif %}
    </div>
</body>
</html>
    """,
    clients=filtered_clients.items(),
    total_clients=upload_stats["total_clients"],
    displayed_count=len(filtered_clients),
    banks_detected=upload_stats.get("banks_detected", 0),
    detection_rate=f"{upload_stats.get('detection_rate', 0)}%",
    with_calls=len([c for c in clients_database.values() if c['nb_appels'] > 0]),
    today_calls=len([c for c in clients_database.values() if c['dernier_appel'] and c['dernier_appel'].startswith(datetime.now().strftime('%d/%m/%Y'))]),
    search=search
    )

@app.route('/clear-clients')
def clear_clients():
    """Vide la base de données clients"""
    global clients_database, upload_stats
    clients_database = {}
    upload_stats = {"total_clients": 0, "last_upload": None, "filename": None, "banks_detected": 0, "detection_rate": 0}
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
    message = f"🧪 Test de connexion avec détection banques - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    result = send_telegram_message(message)
    
    if result:
        return jsonify({"status": "success", "message": "Test Telegram envoyé avec succès"})
    else:
        return jsonify({"status": "error", "message": "Échec du test Telegram"})

@app.route('/test-command')
def test_command():
    """Test de la commande /numero avec détection banque"""
    # Test avec un client existant s'il y en a
    if clients_database:
        test_number = list(clients_database.keys())[0]
    else:
        test_number = "0767328146"  # Numéro par défaut
    
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
        test_caller = "0767328146"
    
    params = {
        'caller': test_caller,
        'callee': '0033185093001', 
        'type': 'start_ringing'
    }
    
    return f"""
    <h2>🧪 Test OVH CGI avec Détection Banque</h2>
    <p>Simulation d'un appel OVH avec paramètres CGI</p>
    <p><a href="/webhook/ovh?{urlencode(params)}" style="background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🎯 Déclencher test appel</a></p>
    <p><strong>Paramètres de test:</strong> {params}</p>
    <p>🏦 Le test affichera automatiquement la banque détectée si un IBAN est présent</p>
    <p><a href="/" style="background: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">🏠 Retour accueil</a></p>
    """

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy", 
        "service": "webhook-ovh-telegram-bank-detection",
        "telegram_configured": bool(TELEGRAM_TOKEN and CHAT_ID),
        "clients_loaded": upload_stats["total_clients"],
        "banks_detected": upload_stats.get("banks_detected", 0),
        "detection_rate": upload_stats.get("detection_rate", 0),
        "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "features": ["client_management", "telegram_webhook", "ovh_webhook", "bank_detection", "iban_analysis"]
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
