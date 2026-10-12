r"""Importa precios históricos al historial de precios a partir de Excel antiguos:
exportaciones de la app (Precios_GLI*.xlsx) y la lista manual previa (PRECIOS GLI AL AAAA.MM.DD ... .xls).

Uso:
  .venv\Scripts\python scripts\importar_historial.py ARCHIVO [ARCHIVO ...]            -> solo muestra qué importaría
  .venv\Scripts\python scripts\importar_historial.py ARCHIVO [ARCHIVO ...] --aplicar   -> guarda en la SQLite local
  ... --aplicar --produccion                                                         -> guarda en la BD de Render

- Fecha de cada archivo: la del nombre (20260325, 2026.03.09) o, si no tiene, la de descarga del archivo.
- Se emparejan por nombre de producto y, si no, por código (solo si el código es único).
- Se omiten filas sin precio y filas cuya moneda no coincide con la moneda actual del producto.
- Si hay varios archivos del mismo día, vale el último.
- Se descartan valores aislados a más de x2.5 del precio típico del producto (errores de unidad o de carga).
- Puntos consecutivos con el mismo precio se guardan una sola vez.
- Volver a ejecutarlo reemplaza lo importado antes (no duplica).
"""
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'))
# Por defecto, la SQLite local. Con --produccion escribe en la BD de Render (PROD_DATABASE_URL de .env):
# solo agrega filas al historial de precios, no modifica productos.
os.environ['DATABASE_URL'] = os.environ['PROD_DATABASE_URL'] if '--produccion' in sys.argv else 'sqlite:///gli_database.sqlite'

from app import (app, db, Producto, HistorialPrecio, limpiar_texto, construir_contexto,  # noqa: E402
                 calcular_producto, moneda_reporte, registrar_precios)

PREFIJO_MOTIVO = 'Importado de '
FACTOR_ATIPICO = 2.5

def num(v):
    if v is None or (isinstance(v, float) and math.isnan(v)): return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace('%', '').replace('$', '').replace('S/', '').replace(',', '')
    try: return float(s)
    except ValueError: return None

def fecha_archivo(ruta):
    """Fecha en hora de Lima: la del nombre si existe (con la hora de descarga si coincide el día)."""
    descarga = datetime.fromtimestamp(os.path.getmtime(ruta))
    m = re.search(r'(20\d{2})[.\-]?(\d{2})[.\-]?(\d{2})', os.path.basename(ruta))
    if not m: return descarga
    dia = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0)
    return descarga if descarga.date() == dia.date() else dia

def primera(cols, opciones):
    return next((c for c in opciones if c in cols), None)

def leer_exportacion(df):
    cols = set(df.columns)
    c_nombre, c_lima = primera(cols, ['Nombre', 'Producto']), primera(cols, ['Precio de venta', 'Precio LIMA'])
    c_prov = primera(cols, ['Precio PROVINCIA', 'Precio AQP / TRU'])
    if not (c_nombre and c_lima and c_prov and 'Moneda' in cols): return None
    c_cod = primera(cols, ['Referencia interna', 'Código'])
    c_costo = primera(cols, ['Costo Total', 'Costo Total (USD)', 'Costo Actual'])
    c_coy = primera(cols, ['Coyuntural', 'Coyuntural (USD)', 'Costo Coyuntural'])
    filas = []
    for _, r in df.iterrows():
        moneda = str(r['Moneda']).strip().upper()
        # En algunas versiones los costos venían siempre en USD aunque el precio fuera en soles (factor 4)
        factor = 4.0 if (c_costo or '').endswith('(USD)') and moneda == 'PEN' else 1.0
        filas.append(dict(nombre=r[c_nombre], codigo=r[c_cod] if c_cod else None, moneda=moneda,
                          lima=num(r[c_lima]), prov=num(r[c_prov]),
                          costo=(num(r[c_costo]) or 0.0) * factor if c_costo else 0.0,
                          coy=max((num(r[c_coy]) or 0.0), 0.0) * factor if c_coy else 0.0,
                          margen=num(r.get('Margen (%)')) or 0.0))
    return filas

def leer_lista_antigua(ruta):
    """Lista manual previa a la app: hoja GENERAL, nombre en col. D, precios Lima/AQP en G/H (en USD)."""
    df = pd.read_excel(ruta, sheet_name='GENERAL', header=None)
    filas = []
    for _, r in df.iterrows():
        lima, nombre = num(r[6]), r[3]
        if not lima or not isinstance(nombre, str): continue
        margen = num(r[5]) if num(r[5]) is not None else num(r[1])
        filas.append(dict(nombre=nombre, codigo=None, moneda='USD', lima=lima, prov=num(r[7]),
                          costo=num(r[12]) or num(r[10]) or 0.0, coy=0.0, margen=round((margen or 0.0) * 100, 2)))
    return filas

def leer(ruta):
    if ruta.lower().endswith('.xls'):
        try: return leer_lista_antigua(ruta), 'lista manual antigua'
        except Exception as e: return None, f'no se pudo leer ({e.__class__.__name__})'
    try: df = pd.read_excel(ruta)
    except Exception: return None, 'no es un Excel válido'
    filas = leer_exportacion(df)
    return (filas, 'exportación de la app') if filas is not None else (None, 'sin columnas de precio y moneda')

def main(rutas, aplicar):
    with app.app_context():
        print(f"Destino: {db.engine.url.render_as_string(hide_password=True)}\n")
        prods =[p for p in Producto.query.all() if not p.oculto]
        ctx = construir_contexto(prods)
        actuales = {p.id: calcular_producto(p, ctx) for p in prods}
        moneda_actual = {pid: moneda_reporte(r) for pid, r in actuales.items()}
        por_nombre = {limpiar_texto(p.nombre): p for p in prods}
        por_codigo = defaultdict(list)
        for p in prods:
            if p.codigo and p.codigo.upper() not in ('S/C', 'NAN', ''): por_codigo[p.codigo.strip().upper()].append(p)

        puntos = defaultdict(list)
        print(f"{'Archivo':52s} {'Fecha':16s} {'Filas':>6s} {'Usadas':>6s} {'SinPar':>6s} {'OtraMon':>7s} {'Sin$':>5s}")
        for ruta in sorted(rutas, key=fecha_archivo):
            filas, tipo = leer(ruta)
            nombre_arch = os.path.basename(ruta)
            if filas is None:
                print(f"{nombre_arch[:52]:52s} {'-':16s} se omite: {tipo}")
                continue
            fecha = fecha_archivo(ruta)
            codigos_archivo = defaultdict(int)
            for f in filas:
                if f['codigo'] is not None: codigos_archivo[str(f['codigo']).strip().upper()] += 1
            usadas = sin_par = moneda_dif = sin_precio = 0
            vistos = set()
            for f in filas:
                if not f['lima'] or f['lima'] <= 0 or f['prov'] is None:
                    sin_precio += 1; continue
                p = por_nombre.get(limpiar_texto(f['nombre']))
                if p is None and f['codigo'] is not None:
                    cod = str(f['codigo']).strip().upper()
                    if len(por_codigo.get(cod, [])) == 1 and codigos_archivo[cod] == 1: p = por_codigo[cod][0]
                if p is None: sin_par += 1; continue
                if f['moneda'] != moneda_actual[p.id]: moneda_dif += 1; continue
                if p.id in vistos: continue
                vistos.add(p.id); usadas += 1
                puntos[p.id].append(dict(fecha=fecha, archivo=nombre_arch, **f))
            print(f"{nombre_arch[:52]:52s} {fecha:%Y-%m-%d %H:%M} {len(filas):6d} {usadas:6d} {sin_par:6d} {moneda_dif:7d} {sin_precio:5d}")

        nuevos = []
        descartados_dia = descartados_atipicos = 0
        for pid, lista in puntos.items():
            # Varios archivos el mismo día: vale el último (los anteriores suelen ser pruebas mientras se editaba)
            por_dia = {}
            for pt in sorted(lista, key=lambda x: x['fecha']): por_dia[pt['fecha'].date()] = pt
            descartados_dia += len(lista) - len(por_dia)
            # Valores aislados muy lejos de lo normal del producto (errores de unidad o de carga)
            referencia = sorted([pt['lima'] for pt in por_dia.values()] + [actuales[pid]['precio_lima']] * (actuales[pid]['precio_lima'] > 0))
            mediana = referencia[len(referencia) // 2]
            validos = [pt for pt in por_dia.values() if mediana <= 0 or 1 / FACTOR_ATIPICO <= pt['lima'] / mediana <= FACTOR_ATIPICO]
            descartados_atipicos += len(por_dia) - len(validos)

            previo = None
            for pt in validos:
                clave = (round(pt['lima'], 2), round(pt['prov'], 2))
                if clave == previo: continue
                previo = clave
                nuevos.append(HistorialPrecio(
                    fecha=pt['fecha'] + timedelta(hours=5), producto_id=pid, producto_nombre=db.session.get(Producto, pid).nombre,
                    moneda=pt['moneda'], costo_total=round(pt['costo'], 4), costo_coyuntural=round(pt['coy'], 4),
                    margen=round(pt['margen'], 2), precio_lima=round(pt['lima'], 4), precio_provincia=round(pt['prov'], 4),
                    usuario='importación', motivo=f"{PREFIJO_MOTIVO}{pt['archivo']}"))

        fechas = [n.fecha for n in nuevos]
        print(f"\nDescartados: {descartados_dia} por haber otro archivo más reciente el mismo día, "
              f"{descartados_atipicos} por estar a más de x{FACTOR_ATIPICO} del precio típico del producto")
        print(f"Productos con historial importado: {len(puntos)} de {len(prods)}")
        print(f"Puntos de precio a guardar: {len(nuevos)}" + (f" (del {min(fechas) - timedelta(hours=5):%d/%m/%Y} al {max(fechas) - timedelta(hours=5):%d/%m/%Y})" if fechas else ''))

        if not aplicar:
            print("\nNo se guardó nada. Para guardar, repite el comando con --aplicar")
            return
        borrados = HistorialPrecio.query.filter(HistorialPrecio.motivo.like(f'{PREFIJO_MOTIVO}%')).delete(synchronize_session=False)
        db.session.add_all(nuevos)
        db.session.commit()
        with app.test_request_context():
            registrar_precios('Registro inicial')   # asegura que el precio actual sea el último punto
        print(f"Guardado. (Se reemplazaron {borrados} puntos importados antes.)")

if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a not in ('--aplicar', '--produccion')]
    if not args: sys.exit(__doc__)
    main(args, '--aplicar' in sys.argv)
