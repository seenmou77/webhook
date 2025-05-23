def load_clients_from_csv_simple(file_content):
    """Charge les clients depuis un contenu CSV - VERSION SIMPLE"""
    global clients_database, upload_stats
    
    clients_database = {}
    
    # Lecture CSV basique
    lines = file_content.strip().split('\n')
    if len(lines) < 2:
        return 0
    
    # En-têtes
    headers = [h.strip().lower() for h in lines[0].split(',')]
    
    # Recherche colonne téléphone
    tel_index = -1
    for i, header in enumerate(headers):
        if header in ['telephone', 'tel', 'phone', 'numero']:
            tel_index = i
            break
    
    if tel_index == -1:
        raise ValueError("Colonne téléphone introuvable")
    
    # Traitement des lignes
    for line in lines[1:]:
        values = [v.strip() for v in line.split(',')]
        if len(values) > tel_index:
            telephone = values[tel_index]
            
            # Normalisation
            telephone = telephone.replace(' ', '').replace('.', '').replace('-', '')
            if telephone.startswith('+33'):
                telephone = '0' + telephone[3:]
            elif telephone.startswith('33'):
                telephone = '0' + telephone[2:]
            
            if len(telephone) >= 10:
                clients_database[telephone] = {
                    "nom": values[headers.index('nom')] if 'nom' in headers else '',
                    "prenom": values[headers.index('prenom')] if 'prenom' in headers else '',
                    "email": values[headers.index('email')] if 'email' in headers else '',
                    "entreprise": values[headers.index('entreprise')] if 'entreprise' in headers else '',
                    "telephone": telephone,
                    "adresse": values[headers.index('adresse')] if 'adresse' in headers else '',
                    "ville": values[headers.index('ville')] if 'ville' in headers else '',
                    "code_postal": values[headers.index('code_postal')] if 'code_postal' in headers else '',
                    "statut": values[headers.index('statut')] if 'statut' in headers else 'Prospect',
                    "date_upload": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "nb_appels": 0,
                    "dernier_appel": None
                }
    
    upload_stats["total_clients"] = len(clients_database)
    upload_stats["last_upload"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return len(clients_database)
