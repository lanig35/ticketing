import argparse
import os
import uuid
import sys
import json
import requests

DB_NAME = "ticketing"
SESSION_FILE = os.path.expanduser("~/.tracker_session.json")
CONFIG_FILE = os.path.expanduser("~/.tracker_config.json")

def get_base_url(args):
    """Résout l'URL complète de CouchDB selon l'ordre de priorité défini."""
    saved_config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            saved_config = json.load(f)

    # 1. CLI > 2. Env > 3. Fichier init > 4. Défaut
    url = (
        args.url
        or os.environ.get("TRACKER_COUCHDB_URL")
        or saved_config.get("url")
        or "http://localhost"
    )
    port = (
        args.port
        or os.environ.get("TRACKER_COUCHDB_PORT")
        or saved_config.get("port")
        or "5984"
    )

    url = url.rstrip("/")
    return f"{url}:{port}"

def get_client(args):
    """Initialise une session Requests avec soit -u/-p, soit le cookie sauvegardé."""
    session = requests.Session()
    
    if args.auth_user and args.auth_pass:
        session.auth = (args.auth_user, args.auth_pass)
        return session

    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            data = json.load(f)
            token = data.get("AuthSession")
            if token:
                session.cookies.set("AuthSession", token)
                return session

    print("Erreur : Non connecté. Utilisez 'tracker.py login' ou fournissez -u et -p.", file=sys.stderr)
    sys.exit(1)

def handle_response(resp):
    """Affiche le résultat ou l'erreur retournée par CouchDB."""
    if resp.status_code in (200, 201):
        print("Succès :", resp.json())
    elif resp.status_code == 401:
        print("Erreur (401) : Session expirée ou identifiants invalides. Veuillez relancer 'login'.", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Erreur ({resp.status_code}) :", resp.text, file=sys.stderr)
        sys.exit(1)

# --- COMMANDE INIT ---
def init_config(args):
    """Enregistre l'URL et le port de CouchDB dans le fichier de configuration."""
    saved_config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            saved_config = json.load(f)

    # Priorité : argument spécifique init > argument global > env > existant > défaut
    url = (
        args.init_url
        or args.url
        or os.environ.get("TRACKER_COUCHDB_URL")
        or saved_config.get("url")
        or "http://localhost"
    )
    port = (
        args.init_port
        or args.port
        or os.environ.get("TRACKER_COUCHDB_PORT")
        or saved_config.get("port")
        or "5984"
    )

    config_data = {"url": url.rstrip("/"), "port": str(port)}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=2)
    
    print(f"Configuration enregistrée : {config_data['url']}:{config_data['port']} (dans {CONFIG_FILE})")

# --- COMMANDE LOGIN ---
def login(args):
    base_url = get_base_url(args)
    session = requests.Session()
    resp = session.post(f"{base_url}/_session", json={"name": args.user, "password": args.password})
    
    if resp.status_code == 200:
        token = session.cookies.get("AuthSession")
        with open(SESSION_FILE, "w") as f:
            json.dump({"user": args.user, "AuthSession": token}, f)
        os.chmod(SESSION_FILE, 0o600)
        print(f"Connecté à {base_url} en tant que '{args.user}'. Jeton sauvegardé.")
    else:
        handle_response(resp)

# --- COMMANDES USERS ---
def create_user(args):
    base_url = get_base_url(args)
    client = get_client(args)
    user_doc = {
        "_id": f"org.couchdb.user:{args.pseudo}",
        "name": args.pseudo,
        "password": args.password,
        "roles": [args.role],
        "type": "user",
        "nom": args.nom,
        "prenom": args.prenom,
        "email": args.email
    }
    url = f"{base_url}/_users/org.couchdb.user:{args.pseudo}"
    resp = client.put(url, json=user_doc)
    handle_response(resp)

def list_users(args):
    base_url = get_base_url(args)
    client = get_client(args)
    url = f"{base_url}/_users/_all_docs?include_docs=true"
    resp = client.get(url)
    if resp.status_code == 200:
        for row in resp.json().get("rows", []):
            doc = row.get("doc", {})
            if doc.get("type") == "user":
                roles = ", ".join(doc.get("roles", []))
                print(f"[{doc.get('name')}] {doc.get('prenom', '')} {doc.get('nom', '')} | {doc.get('email', '')} (Rôle: {roles})")
    else:
        handle_response(resp)

# --- COMMANDES PROJECT ---
def create_project(args):
    base_url = get_base_url(args)
    client = get_client(args)
    project_id = str(uuid.uuid4())
    project_doc = {
        "_id": project_id,
        "type": "project",
        "name": args.name,
        "description": args.description
    }
    url = f"{base_url}/{DB_NAME}/{project_id}"
    resp = client.put(url, json=project_doc)
    handle_response(resp)

def list_projects(args):
    base_url = get_base_url(args)
    client = get_client(args)
    url = f"{base_url}/{DB_NAME}/_find"
    resp = client.post(url, json={"selector": {"type": "project"}})
    if resp.status_code == 200:
        for doc in resp.json().get("docs", []):
            print(f"[{doc['_id']}] {doc['name']} - {doc['description']}")
    else:
        handle_response(resp)

# --- COMMANDES TICKET ---
def create_ticket(args):
    base_url = get_base_url(args)
    client = get_client(args)
    proj_resp = client.get(f"{base_url}/{DB_NAME}/{args.project_id}")
    if proj_resp.status_code != 200 or proj_resp.json().get("type") != "project":
        print("Erreur : L'UUID du projet spécifié est introuvable.", file=sys.stderr)
        sys.exit(1)

    ticket_id = str(uuid.uuid4())
    ticket_doc = {
        "_id": ticket_id,
        "type": "ticket",
        "project_id": args.project_id,
        "title": args.title,
        "description": args.description,
        "assignee": args.assignee,
        "status": "open"
    }
    url = f"{base_url}/{DB_NAME}/{ticket_id}"
    resp = client.put(url, json=ticket_doc)
    handle_response(resp)

def list_tickets(args):
    base_url = get_base_url(args)
    client = get_client(args)
    url = f"{base_url}/{DB_NAME}/_find"
    selector = {"type": "ticket"}
    if args.project_id:
        selector["project_id"] = args.project_id

    resp = client.post(url, json={"selector": selector})
    if resp.status_code == 200:
        for doc in resp.json().get("docs", []):
            print(f"[{doc['_id']}] Projet: {doc['project_id']} | {doc['title']} (Assigné à: {doc['assignee']}) [{doc['status']}]")
    else:
        handle_response(resp)

# --- CONFIGURATION ARGPARSE ---
def main():
    parser = argparse.ArgumentParser(description="Gestionnaire de tickets multi-utilisateurs sur CouchDB")
    # Options globales de connexion et d'authentification
    parser.add_argument("--url", help="URL du serveur CouchDB (ex: http://192.168.1.10)")
    parser.add_argument("--port", help="Port du serveur CouchDB (ex: 5984)")
    parser.add_argument("-u", "--auth-user", help="Identifiant CouchDB (optionnel si connecté via login)")
    parser.add_argument("-p", "--auth-pass", help="Mot de passe CouchDB (optionnel si connecté via login)")

    subparsers = parser.add_subparsers(dest="command", required=True, help="Commandes disponibles")

    # 0a. Sous-commande INIT
    init_parser = subparsers.add_parser("init", help="Configurer l'URL et le port de CouchDB")
    init_parser.add_argument("--url", dest="init_url", help="URL du serveur CouchDB")
    init_parser.add_argument("--port", dest="init_port", help="Port du serveur CouchDB")
    init_parser.set_defaults(func=init_config)

    # 0b. Sous-commande LOGIN
    login_parser = subparsers.add_parser("login", help="Se connecter et sauvegarder le jeton de session")
    login_parser.add_argument("-u", "--user", required=True, help="Pseudo de l'utilisateur")
    login_parser.add_argument("-p", "--password", required=True, help="Mot de passe")
    login_parser.set_defaults(func=login)

    # 1. Sous-commande USERS
    user_parser = subparsers.add_parser("users", help="Gestion des utilisateurs (Admin uniquement)")
    user_sub = user_parser.add_subparsers(dest="action", required=True)
    
    user_create = user_sub.add_parser("create", help="Créer un utilisateur")
    user_create.add_argument("--pseudo", required=True, help="Pseudo de l'utilisateur (identifiant)")
    user_create.add_argument("--nom", required=True, help="Nom de famille")
    user_create.add_argument("--prenom", required=True, help="Prénom")
    user_create.add_argument("--email", required=True, help="Adresse e-mail")
    user_create.add_argument("--password", required=True, help="Mot de passe")
    user_create.add_argument("--role", choices=["leader", "develop"], required=True, help="Rôle attribué")
    user_create.set_defaults(func=create_user)

    user_list = user_sub.add_parser("list", help="Lister les utilisateurs")
    user_list.set_defaults(func=list_users)

    # 2. Sous-commande PROJECT
    proj_parser = subparsers.add_parser("project", help="Gestion des projets")
    proj_sub = proj_parser.add_subparsers(dest="action", required=True)

    proj_create = proj_sub.add_parser("create", help="Créer un projet (Leader uniquement)")
    proj_create.add_argument("--name", required=True, help="Nom du projet")
    proj_create.add_argument("--description", default="", help="Description du projet")
    proj_create.set_defaults(func=create_project)

    proj_list = proj_sub.add_parser("list", help="Lister les projets")
    proj_list.set_defaults(func=list_projects)

    # 3. Sous-commande TICKET
    tick_parser = subparsers.add_parser("ticket", help="Gestion des tickets")
    tick_sub = tick_parser.add_subparsers(dest="action", required=True)
    
    tick_create = tick_sub.add_parser("create", help="Créer un ticket")
    tick_create.add_argument("--project-id", required=True, help="UUID du projet associé")
    tick_create.add_argument("--title", required=True, help="Titre du ticket")
    tick_create.add_argument("--description", default="", help="Détails du ticket")
    tick_create.add_argument("--assignee", required=True, help="Pseudo de l'utilisateur assigné")
    tick_create.set_defaults(func=create_ticket)
    
    tick_list = tick_sub.add_parser("list", help="Lister les tickets")
    tick_list.add_argument("--project-id", help="Filtrer par UUID de projet")
    tick_list.set_defaults(func=list_tickets)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
