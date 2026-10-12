r"""Copia los datos de la BD de producción (Render) a la SQLite local de pruebas.

La conexión a producción es de SOLO LECTURA (default_transaction_read_only=on),
así que este script no puede modificar nada en Render.

Uso:  .venv\Scripts\python scripts\sync_prod_to_local.py
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, select, MetaData

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'))

prod_url = os.environ.get('PROD_DATABASE_URL')
if not prod_url:
    sys.exit("Falta PROD_DATABASE_URL en .env")

# Importar app SIEMPRE apuntando a SQLite local: app.py ejecuta create_all/ALTER al importarse.
os.environ['DATABASE_URL'] = 'sqlite:///gli_database.sqlite'
from app import app, db, User, Config, Producto, Alerta, HistorialCambio, HistorialPrecio  # noqa: E402

prod_url = prod_url.replace("postgres://", "postgresql://", 1).replace("postgresql://", "postgresql+psycopg2://", 1)
prod = create_engine(prod_url, connect_args={'options': '-c default_transaction_read_only=on', 'sslmode': 'require'})
prod_meta = MetaData()
prod_meta.reflect(bind=prod)

with app.app_context():
    local = db.engine
    print(f"Local: {local.url}")
    for model in (User, Config, Producto, Alerta, HistorialCambio, HistorialPrecio):
        tabla = model.__table__
        if tabla.name not in prod_meta.tables:
            with local.begin() as lc: lc.execute(tabla.delete())
            print(f"  {tabla.name}: no existe en producción, se vacía la copia local")
            continue
        prod_tabla = prod_meta.tables[tabla.name]
        cols = [c.name for c in tabla.columns if c.name in prod_tabla.c]
        faltan = [c.name for c in prod_tabla.columns if c.name not in tabla.c]
        with prod.connect() as pc:
            filas = [dict(r._mapping) for r in pc.execute(select(*[prod_tabla.c[c] for c in cols]))]
        with local.begin() as lc:
            lc.execute(tabla.delete())
            if filas:
                lc.execute(tabla.insert(), filas)
        extra = f" (columnas solo en prod ignoradas: {faltan})" if faltan else ""
        print(f"  {tabla.name}: {len(filas)} filas copiadas{extra}")

print("Listo. BD local actualizada con una copia de producción.")
