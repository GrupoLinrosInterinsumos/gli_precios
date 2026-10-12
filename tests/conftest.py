import os
import sys

import pytest

# Base de datos en memoria: las pruebas nunca tocan la SQLite local ni producción.
os.environ['DATABASE_URL'] = 'sqlite://'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402
import app as modulo_app  # noqa: E402
from app import app as flask_app, db, User, Producto  # noqa: E402

from flask import g  # noqa: E402

# Las pruebas mantienen un app_context abierto que comparten todas las peticiones; sin esto,
# Flask-Login reutilizaría el usuario de la petición anterior aunque sea de otro cliente.
@flask_app.before_request
def _olvidar_usuario_cacheado(): g.pop('_login_user', None)

@pytest.fixture
def app():
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        db.drop_all(); db.create_all()
        modulo_app._linea_base_lista = False
        yield flask_app
        db.session.remove()

@pytest.fixture
def crear(app):
    def _crear(nombre, **kw):
        kw.setdefault('tipo_origen', 'COMPRADO')
        p = Producto(nombre=nombre, **kw); db.session.add(p); db.session.commit()
        return p
    return _crear

def _cliente(app, rol):
    email = f"{rol.lower()}@test.local"
    if not User.query.filter_by(email=email).first():
        db.session.add(User(email=email, password=generate_password_hash('x'), role=rol)); db.session.commit()
    c = app.test_client()
    c.post('/login', data={'email': email, 'password': 'x'})
    return c

@pytest.fixture
def admin(app): return _cliente(app, 'Admin')

@pytest.fixture
def vendedor(app): return _cliente(app, 'Vendedor')
