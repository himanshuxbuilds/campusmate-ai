"""Create a real user (production-safe: prompts for the password; nothing is hard-coded).
   python scripts/create_user.py --code S2026X001 --name "Full Name" --role student --programme PGDM-AIDS --email a@b.c"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
from app.models import User
from app.security.passwords import hash_password, password_problems

ap = argparse.ArgumentParser()
ap.add_argument("--code", required=True); ap.add_argument("--name", required=True); ap.add_argument("--role", choices=["student", "staff", "admin"], required=True)
ap.add_argument("--programme"); ap.add_argument("--email")
a = ap.parse_args()
pw = getpass.getpass("Password: ")
if password_problems(pw):
    sys.exit("weak password: need " + ", ".join(password_problems(pw)))
db.init_engine()
with db.session_scope() as s:
    s.add(User(user_code=a.code, name=a.name, role=a.role, programme=a.programme, email=a.email, password_hash=hash_password(pw)))
print("created", a.code)
