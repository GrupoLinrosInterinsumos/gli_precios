r"""Compara los precios actuales contra una "foto" guardada antes de un cambio de código.

  1) Antes de tocar el cálculo:  .venv\Scripts\python scripts\verificar_precios.py --guardar
  2) Después del cambio:         .venv\Scripts\python scripts\verificar_precios.py

Usa la SQLite local y el usuario de pruebas de .env. La foto se guarda en instance/ (no se versiona).
"""
import io
import json
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'))
os.environ['DATABASE_URL'] = 'sqlite:///gli_database.sqlite'

import pandas as pd  # noqa: E402
from app import app  # noqa: E402

FOTO = os.path.join(ROOT, 'instance', 'snapshot_buscar_antes.json')
CAMPOS = ['costo_base', 'costo_fab', 'merma_porcentaje', 'merma_monto', 'costo_actual', 'costo_coyuntural', 'margen',
          'precio_lima', 'precio_provincia', 'flete', 'moneda_simbolo', 'moneda_texto', 'pv_str', 'dist_str',
          'es_pen_exception', 'visible_ventas', 'editable_costo', 'en_odoo', 'nota']

c = app.test_client()
c.post('/login', data={'email': os.environ['LOCAL_TEST_EMAIL'], 'password': os.environ['LOCAL_TEST_PASSWORD']})
actual = c.get('/buscar?q=').get_json()

if '--guardar' in sys.argv:
    json.dump(actual, open(FOTO, 'w', encoding='utf-8'), ensure_ascii=False)
    sys.exit(f"Foto guardada: {len(actual['productos'])} productos")

antes = json.load(open(FOTO, encoding='utf-8'))
prev = {p['nombre']: p for p in antes['productos']}
ahora = {p['nombre']: p for p in actual['productos']}
dif = [(n, k, prev[n][k], ahora[n][k]) for n in prev if n in ahora for k in CAMPOS if prev[n][k] != ahora[n][k]]
print(f"Pantalla: {len(ahora)} productos (antes {len(prev)}), {len(dif)} diferencias")
for d in dif[:20]: print("  ", d)
print(f"Orden igual: {[p['nombre'] for p in antes['productos']] == [p['nombre'] for p in actual['productos']]}")
print(f"Alertas: antes {len(antes['alertas'])}, ahora {len(actual['alertas'])}, iguales: "
      f"{[(a['producto'], a['msg']) for a in antes['alertas']] == [(a['producto'], a['msg']) for a in actual['alertas']]}")

# El Excel debe coincidir con la pantalla
r = c.post('/api/exportar', json={'ids': [p['id'] for p in actual['productos']]})
ex = pd.read_excel(io.BytesIO(r.data)).set_index('Nombre')
pares = [('precio_lima', 'Precio de venta'), ('precio_provincia', 'Precio PROVINCIA'), ('costo_coyuntural', 'Coyuntural'),
         ('costo_actual', 'Costo Total'), ('costo_base', 'Costo Real'), ('costo_fab', 'Costo Fab')]
dif_x = [(p['nombre'], a, round(p[a], 2), float(ex.loc[p['nombre'], b])) for p in actual['productos'] for a, b in pares
         if abs(round(p[a], 2) - float(ex.loc[p['nombre'], b])) > 0.011]
print(f"Excel vs pantalla: {len(ex)} filas, {len(dif_x)} diferencias")
for d in dif_x[:10]: print("  ", d)
sys.exit(1 if dif or dif_x else 0)
