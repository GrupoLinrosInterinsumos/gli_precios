"""Crea (o actualiza) un SuperAdmin de pruebas SOLO en la SQLite local.

Lee LOCAL_TEST_EMAIL / LOCAL_TEST_PASSWORD de .env.
"""
import os
import sys

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'))

os.environ['DATABASE_URL'] = 'sqlite:///gli_database.sqlite'
from app import app, db, User  # noqa: E402

email = os.environ['LOCAL_TEST_EMAIL']
clave = os.environ['LOCAL_TEST_PASSWORD']

with app.app_context():
    u = User.query.filter_by(email=email).first() or User(email=email, role='SuperAdmin')
    u.password = generate_password_hash(clave)
    u.role = 'SuperAdmin'
    db.session.add(u)
    db.session.commit()
    print(f"Usuario local listo: {email} (SuperAdmin). La clave está en .env")
