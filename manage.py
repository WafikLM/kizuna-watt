"""Outils d'administration (a lancer sur la machine du serveur).

  python manage.py create-steg        cree le compte STEG (e-mail + mot de passe saisis dans le terminal)
  python manage.py reset-password     change le mot de passe d'un compte
  python manage.py stats              nombre de comptes (aucun mot de passe affiche)
  python manage.py trust-models       enregistre l'empreinte des modeles d'IA du dossier models/ (a lancer apres verification)
  python manage.py import-streets     charge data/streets_tunisia.json (voir tools/fetch_streets.py) dans la base
  python manage.py backup             copie coherente de la base dans data/backups/ (14 dernieres gardees ; chiffree si KIZUNA_BACKUP_KEY)
  python manage.py security           controle de securite : HTTPS, cle des recompenses, comptes STEG, sauvegardes
  python manage.py sms-status         etat du module SMS (mode simulation ou fournisseur reel)
  python manage.py sms-test +216...   envoie un SMS de test (reel seulement si un fournisseur est configure)
  python manage.py mail-test a@b.com  envoie un e-mail de test (reel seulement si KIZUNA_MAIL_* est configure)

Le mot de passe n'est jamais affiche ni ecrit dans un fichier.
"""
import getpass
import sys

from kizuna import db

db.init()
cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "create-steg":
    email = input("E-mail du compte STEG : ").strip().lower()
    if not db.EMAIL_RE.match(email):
        sys.exit("Adresse e-mail invalide.")
    if db.by_email(email):
        sys.exit("Ce compte existe deja.")
    name = input("Nom affiche (ex. STEG - Exploitation) : ").strip() or "STEG"
    pw = getpass.getpass("Mot de passe : ")
    err = db.validate_password(pw, getpass.getpass("Confirmer : "), strict=True)
    if err:
        sys.exit(err)
    db.create_user(email, pw, dict(full_name=name, consent_terms=1), role="steg")
    print("Compte STEG cree. Connectez-vous sur /login : vous arriverez sur la carte des clients.")
elif cmd == "reset-password":
    email = input("E-mail du compte : ").strip().lower()
    pw = getpass.getpass("Nouveau mot de passe : ")
    err = db.validate_password(pw, getpass.getpass("Confirmer : "))
    if err:
        sys.exit(err)
    print("Mot de passe modifie." if db.set_password(email, pw) else "Compte introuvable.")
elif cmd == "trust-models":
    from kizuna import ai
    print(f"{ai.trust_models()} fichier(s) de modele enregistres dans models/MANIFEST.json")
elif cmd == "import-streets":
    import os
    from kizuna import lighting
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "streets_tunisia.json")
    print(f"{lighting.import_streets(path)} rues importees." if os.path.exists(path) else "Fichier absent : lancez d'abord python tools/fetch_streets.py")
elif cmd == "backup":
    from kizuna import security
    print("Sauvegarde :", security.backup())
    print(security.restore_hint())
elif cmd == "security":
    from kizuna import security
    for lvl, txt in security.check():
        print({"ok": "[OK]     ", "avert": "[A FAIRE]", "manque": "[MANQUE]"}[lvl], txt)
elif cmd == "sms-status":
    from kizuna import sms
    c = db.conn()
    st = sms.stats(c)
    c.close()
    print(st["description"], "| messages :", st["counts"] or "aucun", "| quota :", st["daily_max"], "par foyer et par 24 h")
elif cmd == "sms-test":
    from kizuna import sms
    num = sms.normalize(sys.argv[2] if len(sys.argv) > 2 else "")
    if not num:
        sys.exit("Numero invalide (8 chiffres tunisiens ou +indicatif).")
    c = db.conn()
    i = sms.enqueue(c, 0, num, "test", "Kizuna Watt : message de test.", "test")
    c.commit()
    sms.process(c)
    r = c.execute("SELECT status, segments, error FROM sms_outbox WHERE id=?", (i,)).fetchone()
    c.close()
    print("Mode :", sms.describe(), "| statut :", r["status"], "| segments :", r["segments"], "|", r["error"] or "")
elif cmd == "mail-test":
    from kizuna import sms
    to = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lower()
    if not sms.EMAIL_RE.match(to):
        sys.exit("Adresse e-mail invalide.")
    c = db.conn()
    i = sms.enqueue(c, 0, to, "test", "Kizuna Watt : message de test.", "test", channel="email", subject="Kizuna Watt - test")
    c.commit()
    sms.process(c)
    r = c.execute("SELECT status, error FROM sms_outbox WHERE id=?", (i,)).fetchone()
    c.close()
    print("Mode :", sms.mail_describe(), "| statut :", r["status"], "|", r["error"] or "")
elif cmd == "stats":
    c = db.count_by_role()
    print(f"Clients : {c.get('client', 0)} · Comptes STEG : {c.get('steg', 0)}")
else:
    print(__doc__)
