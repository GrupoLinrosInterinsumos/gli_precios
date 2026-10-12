import os
import io
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import pandas as pd
from flask import Flask, jsonify, request, render_template, send_file, redirect, url_for, flash
from flask_cors import CORS
from flask_compress import Compress
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, func, and_
from sqlalchemy.exc import IntegrityError

os.environ['TZ'] = 'America/Lima'
try: time.tzset()
except AttributeError: pass 

app = Flask(__name__, template_folder='templates')
CORS(app)
Compress(app)

app.config['SECRET_KEY'] = 'GLI_EXECUTIVE_PRO_2026'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///gli_database.sqlite')
# SQLAlchemy 2.1+ usa psycopg (v3) por defecto para "postgresql://"; forzamos psycopg2, que es el instalado.
for prefijo in ("postgres://", "postgresql://"):
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith(prefijo):
        app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace(prefijo, "postgresql+psycopg2://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# =========================================================
# 📊 MODELOS DE BASE DE DATOS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) 

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True)
    valor = db.Column(db.Float, default=3.80)

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(250), unique=True, nullable=False)
    codigo = db.Column(db.String(100), default='S/C')
    empresa = db.Column(db.String(100), default='')
    proveedor = db.Column(db.String(100), default='')
    moneda_simbolo = db.Column(db.String(10), default='$')
    moneda_texto = db.Column(db.String(10), default='USD')
    
    costo_base_ex = db.Column(db.Float, default=0.0)
    costo_fab_ex = db.Column(db.Float, default=0.0)
    coyuntural_ex = db.Column(db.Float, default=0.0)
    margen_ex = db.Column(db.Float, default=0.20)
    dscto_pv_ex = db.Column(db.Float, default=0.0)
    dscto_dist_ex = db.Column(db.Float, default=0.0)
    
    costo_base_man = db.Column(db.Float, nullable=True)
    costo_fab_man = db.Column(db.Float, nullable=True)
    coyuntural_man = db.Column(db.Float, nullable=True)
    margen_man = db.Column(db.Float, nullable=True)
    merma_pct_man = db.Column(db.Float, default=0.0)
    dscto_pv_man = db.Column(db.Float, nullable=True)
    dscto_dist_man = db.Column(db.Float, nullable=True)
    flete_man = db.Column(db.Float, nullable=True)

    pv_str = db.Column(db.String(10), default='')
    dist_str = db.Column(db.String(10), default='')
    
    es_manual = db.Column(db.Boolean, default=False)
    oculto = db.Column(db.Boolean, default=False)
    
    nota = db.Column(db.String(250), default='') 
    nota_visible = db.Column(db.Boolean, default=False)
    
    categoria = db.Column(db.String(100), default='')
    tipo_origen = db.Column(db.String(20), default='COMPRADO')
    en_odoo = db.Column(db.String(10), default='NO') 
    
    visible_ventas = db.Column(db.Boolean, default=True)
    usd_converted = db.Column(db.Boolean, default=False)
    fecha_act = db.Column(db.DateTime, default=lambda: ahora_utc())

class Alerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(50))
    msg = db.Column(db.String(250))
    producto = db.Column(db.String(250))
    tipo = db.Column(db.String(50), default="INFO")

def ahora_utc(): return datetime.now(timezone.utc).replace(tzinfo=None)

def fecha_lima(dt): return (dt - timedelta(hours=5)).strftime('%d/%m/%Y %H:%M') if dt else ''

class HistorialCambio(db.Model):
    """Auditoría: quién cambió qué dato, cuándo, y el valor anterior/nuevo."""
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=ahora_utc, index=True)
    usuario = db.Column(db.String(100), default='')
    producto_id = db.Column(db.Integer, index=True)
    producto_nombre = db.Column(db.String(250), default='')
    accion = db.Column(db.String(30), default='')
    campo = db.Column(db.String(50), default='')
    valor_anterior = db.Column(db.String(250), default='')
    valor_nuevo = db.Column(db.String(250), default='')

class HistorialPrecio(db.Model):
    """Foto de los precios calculados de un producto cada vez que cambian (en su moneda de visualización)."""
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=ahora_utc, index=True)
    producto_id = db.Column(db.Integer, index=True)
    producto_nombre = db.Column(db.String(250), default='')
    moneda = db.Column(db.String(10), default='USD')
    costo_total = db.Column(db.Float, default=0.0)
    costo_coyuntural = db.Column(db.Float, default=0.0)
    margen = db.Column(db.Float, default=0.0)
    precio_lima = db.Column(db.Float, default=0.0)
    precio_provincia = db.Column(db.Float, default=0.0)
    usuario = db.Column(db.String(100), default='')
    motivo = db.Column(db.String(250), default='')

with app.app_context(): 
    db.create_all()
    try:
        db.session.execute(text("ALTER TABLE producto ADD COLUMN pv_str VARCHAR(10) DEFAULT ''"))
        db.session.execute(text("ALTER TABLE producto ADD COLUMN dist_str VARCHAR(10) DEFAULT ''"))
        db.session.commit()
    except: db.session.rollback()
    try:
        db.session.execute(text("ALTER TABLE producto ADD COLUMN flete_man FLOAT"))
        db.session.commit()
    except: db.session.rollback()
    
    try:
        db.session.execute(text("ALTER TABLE producto ADD COLUMN nota_visible BOOLEAN DEFAULT FALSE"))
        db.session.commit()
    except: db.session.rollback()
    
    try:
        db.session.execute(text("ALTER TABLE producto ADD COLUMN en_odoo VARCHAR(10) DEFAULT 'NO'"))
        db.session.commit()
    except: db.session.rollback()

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

# =========================================================
# 🛠️ FUNCIONES Y EXCEPCIONES LOGICAS
# =========================================================
FLETE_ESTANDAR = 0.11 

EXCEPCIONES_SOLES = [
    "COLAGENO HIDROLIZADO GELNEX X 1KG", "COLAGENO HIDROLIZADO GELNEX X 400G",
    "PREPARADO VITAMINA C LINROS X 500G"
]

EXCEPCIONES_NATIVAS = [
    "AGITADOR DE LECHE", "ALCOHOLIMETRO", "CARBONATO DE CALCIO", "MOLDERA ACERO INOX",
    "LYOFAST Y 470 E", "LYOFAST Y 438 A"
]

EXCEPCIONES_SACCO_USD = ["LYOTO M 536 R", "LYOTO M 536 S", "LYOFAST AB 1"]
EXCEPCIONES_CLERICI_USD = ["TRANSGLUTAMINASA CAGLIFICIO CLERICI"]

def limpiar_texto(txt):
    try:
        if txt is None: return ""
        if isinstance(txt, float) and pd.isna(txt): return ""
        t = str(txt).replace('\xa0', ' ').strip().upper()
        if t == 'NAN': return ""
        return re.sub(r'\s+', ' ', t).strip()
    except: return ""

def get_tc_actual():
    try:
        c = Config.query.filter_by(clave='tipo_cambio').first()
        if not c:
            c = Config(clave='tipo_cambio', valor=3.80); db.session.add(c); db.session.commit()
        return c.valor
    except: return 3.80

def detectar_proveedor_exacto(nombre_odoo, empresa_col=""):
    n_up = limpiar_texto(nombre_odoo)
    n_clean = n_up.replace(' ', '')
    if "NATAMICINA" in n_clean or "NISINA" in n_clean: return "INTERINSUMOS"
    if "CRAMER" in n_up: return "CRAMER"
    if "SACCO" in n_up: return "SACCO"
    if "CLERICI" in n_up or "CAGLIFICIO" in n_up: return "CAGLIFICIO CLERICI"
    if "LUDAFA" in n_up: return "JM LUDAFA"
    return limpiar_texto(empresa_col)

def es_nativo_soles(nombre):
    n_up = limpiar_texto(nombre)
    return any(exc in n_up for exc in EXCEPCIONES_NATIVAS)

def es_proveedor_soles_mixto(nombre, prov):
    n_upper = limpiar_texto(nombre)
    n_clean = n_upper.replace(' ', '')
    if prov == "CAGLIFICIO CLERICI" or "CLERICI" in n_upper or "CAGLIFICIO" in n_upper:
        return not any(e.replace(" ", "") in n_clean for e in EXCEPCIONES_CLERICI_USD)
    if prov == "JM LUDAFA" or "LUDAFA" in n_upper: return True
    return False

def es_excepcion_soles_clasica(nombre):
    n_clean = limpiar_texto(nombre).replace(' ', '').replace('Á', 'A').replace('Ó', 'O')
    if any(exc.replace(" ", "") in n_clean for exc in EXCEPCIONES_SOLES): return True
    if "COLAGENO" in n_clean and ("1KG" in n_clean or "400G" in n_clean): return True
    if "FOSFATOPARAJAMONES" in n_clean: return True
    if "FOSFATOPARAMASAS" in n_clean: return True
    if "SALDECURACONCENTRADA" in n_clean: return True
    if "POLVODEHORNEARLEVAMAX" in n_clean: return True
    return False

def get_currency_info(nombre, proveedor):
    if es_nativo_soles(nombre): return "S/", "PEN"
    if es_proveedor_soles_mixto(nombre, proveedor): return "S/", "PEN"
    if es_excepcion_soles_clasica(nombre): return "S/", "PEN"
    
    n_upper = limpiar_texto(nombre)
    if proveedor == "SACCO" or "SACCO" in n_upper:
        n_clean = n_upper.replace(' ', '').replace('Á', 'A').replace('Ó', 'O')
        if any(exc.replace(" ", "") in n_clean for exc in EXCEPCIONES_SACCO_USD): return "$", "USD"
        return "S/", "PEN"
    return "$", "USD"

def format_discount(str_val, num_val_man, num_val_ex):
    if str_val and str(str_val).strip() and str(str_val).strip().lower() != 'nan':
        s = str(str_val).strip()
        if s not in ["0", "0.0", "0.00"]:
            try:
                f = float(s)
                if 0.0 < f <= 1.0: 
                    num_rounded = round(f * 100, 2)
                    return f"{int(num_rounded)}%" if num_rounded.is_integer() else f"{num_rounded}%"
                else: 
                    num_rounded = round(f, 2)
                    return f"{int(num_rounded)}%" if num_rounded.is_integer() else f"{num_rounded}%"
            except ValueError: return s
    num = get_val(num_val_man, num_val_ex, 0.0) * 100
    if num == 0: return ""
    num_rounded = round(num, 2)
    return f"{int(num_rounded)}%" if num_rounded.is_integer() else f"{num_rounded}%"

def get_val(man, ex, default):
    try:
        if man is not None and not pd.isna(man): return float(man)
        if ex is not None and not pd.isna(ex): return float(ex)
        return float(default)
    except: return float(default)

def robust_numeric(val):
    if val is None or pd.isna(val): return 0.0
    s = str(val).strip().replace('$', '').replace('S/', '').replace('%', '')
    if s == '' or s.lower() == 'nan': return 0.0
    if ',' in s and '.' in s: s = s.replace(',', '')
    elif ',' in s: s = s.replace(',', '.')
    try: return float(s)
    except: return 0.0

def parse_percentage(val, default=0.0):
    if val is None or pd.isna(val): return default
    s = str(val).strip().replace('%', '').replace(',', '.')
    try:
        v = float(s)
        if v > 1 and v <= 100: return v / 100.0
        return v
    except: return default

def get_col_val(row, poss_names, def_val=0):
    for n in poss_names:
        if n in row: return row[n]
    return def_val

def get_core_name(name):
    core = re.sub(r'\bX?\s*\d+(?:[\.,]\d+)?\s*(?:KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', '', limpiar_texto(name), flags=re.IGNORECASE)
    core = re.sub(r'[^a-zA-Z0-9\s]', '', core)
    return re.sub(r'\s+', ' ', core).strip()

def get_quantity_normalized(name):
    match = re.search(r'\bX?\s*(\d+(?:[\.,]\d+)?)\s*(KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', limpiar_texto(name), flags=re.IGNORECASE)
    if match:
        try: 
            val = float(match.group(1).replace(',', '.'))
            unit = match.group(2).upper()
            if unit in ['G', 'GR', 'GRS', 'ML']: val /= 1000.0
            return val
        except: pass
    return 0.0

def get_quantity(name):
    match = re.search(r'\bX?\s*(\d+(?:[\.,]\d+)?)\s*(?:KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', limpiar_texto(name), flags=re.IGNORECASE)
    if match:
        try: return float(match.group(1).replace(',', '.'))
        except: pass
    return ""

def es_excepcion_herencia(nombre):
    n_clean = limpiar_texto(nombre).replace(' ', '')
    excs = ["COLAGENOHIDROLIZADOGELNEXX1KG", "COLAGENOHIDROLIZADOGELNEXX400G", "FOSFATOPARAJAMONES", "FOSFATOPARAMASAS", "AMILASAMALTOGENICAMTG1500"]
    return any(e in n_clean for e in excs)


# =========================================================
# 🧮 MOTOR DE CÁLCULO (único: lo usan la pantalla, el Excel y el historial)
# =========================================================
PROVEEDORES_SIN_FLETE = ["CRAMER", "SACCO", "JM LUDAFA"]

def construir_contexto(prods):
    """Prepara los COMPRADOS (padres) agrupados por nombre base, con el coyuntural heredado entre hermanos."""
    data_comprados = []
    por_core = defaultdict(list)
    for c in prods:
        if c.tipo_origen == 'COMPRADO' and not c.oculto:
            d = {
                'nombre': c.nombre, 'costo_usd': get_val(c.costo_base_man, c.costo_base_ex, 0.0),
                'coyun_usd': get_val(c.coyuntural_man, c.coyuntural_ex, 0.0),
                'core': get_core_name(c.nombre), 'peso': get_quantity_normalized(c.nombre)
            }
            data_comprados.append(d)
            por_core[d['core']].append(d)

    for d in data_comprados:
        if d['coyun_usd'] <= 0:
            hermanos_con_coyun = [h for h in por_core[d['core']] if h['coyun_usd'] > 0]
            if hermanos_con_coyun:
                normales = [h for h in hermanos_con_coyun if h['peso'] < 100.0]
                hermano_original = max(normales, key=lambda x: x['peso']) if normales else max(hermanos_con_coyun, key=lambda x: x['peso'])
                d['coyun_usd'] = hermano_original['coyun_usd']

    return {'por_nombre': {d['nombre']: d for d in data_comprados}, 'por_core': por_core}

def es_visible_ventas(p):
    if "CUAJO IL CASARO SACHETS CAGLIFICIO CLERICI" in p.nombre.upper(): return True
    return p.visible_ventas if p.visible_ventas is not None else True

def calcular_producto(p, ctx):
    """Devuelve todos los costos y precios de un producto. No modifica la base de datos."""
    alertas = []
    prov_real = detectar_proveedor_exacto(p.nombre, str(p.empresa or ''))
    moneda_simbolo, moneda_texto = get_currency_info(p.nombre, prov_real)

    c_base_db = get_val(p.costo_base_man, p.costo_base_ex, 0.0)
    coyun_db = get_val(p.coyuntural_man, p.coyuntural_ex, 0.0)
    editable_costo = True

    if p.tipo_origen == 'COMPRADO':
        resuelto = ctx['por_nombre'].get(p.nombre)
        if resuelto and coyun_db <= 0:
            coyun_db = resuelto['coyun_usd']

    elif p.tipo_origen == 'FABRICADO' and not es_excepcion_herencia(p.nombre):
        c_heredado_usd = 0.0
        coyun_heredado_usd = 0.0
        p_padres = ctx['por_core'].get(get_core_name(p.nombre), [])

        if p_padres:
            if 'ESENCIA' in str(p.categoria).upper() or 'ESENCIA' in p.nombre.upper():
                # Esencias: se hereda del empaque de 1 kg
                p_1 = [d for d in p_padres if d['peso'] == 1.0]
                padres_con_coyun = [d for d in (p_1 if p_1 else p_padres) if d['coyun_usd'] > 0]
                padre_elegido = max(padres_con_coyun, key=lambda x: x['peso']) if padres_con_coyun else (p_1[0] if p_1 else p_padres[0])
            else:
                padres_con_coyun = [d for d in p_padres if d['coyun_usd'] > 0]
                candidatos = padres_con_coyun if padres_con_coyun else p_padres
                normales = [p_ for p_ in candidatos if p_['peso'] < 100.0]
                padre_elegido = max(normales, key=lambda x: x['peso']) if normales else max(candidatos, key=lambda x: x['peso'])

            c_heredado_usd = padre_elegido['costo_usd']
            coyun_heredado_usd = padre_elegido['coyun_usd']

        if c_heredado_usd > 0:
            if p.costo_base_man is not None and p.costo_base_man > 0:
                c_base_db = p.costo_base_man; editable_costo = True
            else:
                c_base_db = c_heredado_usd; editable_costo = False

        if coyun_heredado_usd > 0:
            if p.coyuntural_man is None or p.coyuntural_man <= 0:
                coyun_db = coyun_heredado_usd

    c_fab_db = get_val(p.costo_fab_man, p.costo_fab_ex, 0.0)
    mg = get_val(p.margen_man, p.margen_ex, 0.20)
    merma_pct = p.merma_pct_man or 0.0

    is_nativo = es_nativo_soles(p.nombre)
    is_mixto = es_proveedor_soles_mixto(p.nombre, prov_real)
    is_clasica = es_excepcion_soles_clasica(p.nombre)
    is_frag = 'FRAGANCIA' in str(p.categoria).upper() or 'FRAGANCIA' in p.nombre.upper()

    if mg <= 0: alertas.append(f"Margen Crítico ({round(mg*100,2)}%)")

    if is_nativo or is_mixto:
        # Costos guardados en soles (en mixtos, el costo de fabricación está en USD/4)
        base_pen = c_base_db; fab_pen = c_fab_db if is_nativo else c_fab_db * 4.0; coyun_pen = coyun_db
        merma_pen = base_pen * merma_pct
        ct_pen = base_pen + fab_pen + merma_pen
        coyun_total_pen = (coyun_pen + fab_pen + (coyun_pen * merma_pct)) if coyun_pen > 0 else 0.0

        c_ref_pen = coyun_total_pen if coyun_total_pen > 0 else ct_pen
        p_lima_pen = c_ref_pen * (1 + mg)
        if is_nativo:
            flete_pen_calc = 0.0 if prov_real in PROVEEDORES_SIN_FLETE else FLETE_ESTANDAR * 4.0
            flete_usd_send = get_val(p.flete_man, None, flete_pen_calc / 4.0)
        else:
            flete_usd_send = get_val(p.flete_man, None, 0.0)
        p_prov_pen = p_lima_pen + (flete_usd_send * 4.0)

        c_base_usd_send = base_pen / 4.0; c_fab_usd_send = fab_pen / 4.0 if is_nativo else c_fab_db
        coyun_usd_send = coyun_total_pen / 4.0
        merma_monto_usd_send = merma_pen / 4.0; ct_usd_send = ct_pen / 4.0
        p_lima_usd_send = p_lima_pen / 4.0; p_prov_usd_send = p_prov_pen / 4.0
        factor_display = 4.0

    else:
        c_base_usd_send = c_base_db; c_fab_usd_send = c_fab_db; coyun_base_usd = coyun_db
        merma_monto_usd_send = c_base_usd_send * merma_pct
        ct_usd_send = c_base_usd_send + c_fab_usd_send + merma_monto_usd_send
        coyun_usd_send = (coyun_base_usd + c_fab_usd_send + (coyun_base_usd * merma_pct)) if coyun_base_usd > 0 else 0.0

        c_ref_usd = coyun_usd_send if coyun_usd_send > 0 else ct_usd_send
        p_lima_usd_send = c_ref_usd * (1 + mg)
        if is_clasica:
            flete_calc = 0.0 if prov_real in PROVEEDORES_SIN_FLETE else FLETE_ESTANDAR
        else:
            flete_calc = 0.0 if (prov_real in PROVEEDORES_SIN_FLETE and not is_frag) else FLETE_ESTANDAR
        flete_usd_send = get_val(p.flete_man, None, flete_calc)
        p_prov_usd_send = p_lima_usd_send + flete_usd_send
        factor_display = 4.0 if is_clasica else 1.0

    if coyun_usd_send > 0 and ct_usd_send > coyun_usd_send: alertas.append("Costo Total superó Coyuntural")

    return {
        "id": p.id, "nombre": str(p.nombre), "codigo": str(p.codigo), "empresa": str(p.empresa or ''),
        "categoria": str(p.categoria or ''), "tipo_origen": str(p.tipo_origen or ''),

        "costo_base": float(c_base_usd_send * factor_display),
        "costo_fab": float(c_fab_usd_send * factor_display),
        "merma_porcentaje": round(merma_pct * 100, 2),
        "merma_monto": float(merma_monto_usd_send * factor_display),
        "costo_actual": float(ct_usd_send * factor_display),
        "costo_coyuntural": float(coyun_usd_send * factor_display),
        "margen": round(mg * 100, 2),
        "precio_lima": float(p_lima_usd_send * factor_display),
        "precio_provincia": float(p_prov_usd_send * factor_display),
        "flete": float(flete_usd_send * factor_display),

        "costo_base_usd": float(c_base_usd_send), "costo_fab_usd": float(c_fab_usd_send),
        "merma_monto_usd": float(merma_monto_usd_send), "costo_actual_usd": float(ct_usd_send),
        "costo_coyuntural_usd": float(coyun_usd_send), "precio_lima_usd": float(p_lima_usd_send),
        "precio_provincia_usd": float(p_prov_usd_send), "flete_usd": float(flete_usd_send),
        "es_pen_exception": (is_nativo or is_mixto or is_clasica),
        "en_odoo": str(p.en_odoo) if p.en_odoo else "NO",

        "moneda_simbolo": str(moneda_simbolo), "moneda_texto": str(moneda_texto),
        "pv_str": str(format_discount(p.pv_str, p.dscto_pv_man, p.dscto_pv_ex)),
        "dist_str": str(format_discount(p.dist_str, p.dscto_dist_man, p.dscto_dist_ex)),

        "nota": str(p.nota) if p.nota else "",
        "nota_visible": bool(p.nota_visible),
        "visible_ventas": es_visible_ventas(p), "editable_costo": editable_costo,
        "alertas": alertas
    }

def moneda_reporte(r): return 'PEN' if r['es_pen_exception'] else r['moneda_texto']

# Lo único que recibe un Vendedor/TC: nada de costos, márgenes ni notas privadas.
CAMPOS_VENDEDOR = ('id', 'nombre', 'codigo', 'empresa', 'moneda_simbolo', 'moneda_texto',
                   'precio_lima', 'precio_provincia', 'pv_str', 'dist_str', 'nota', 'nota_visible')

def para_vendedor(r):
    d = {k: r[k] for k in CAMPOS_VENDEDOR}
    if not d['nota_visible']: d['nota'] = ''
    return d

# =========================================================
# 🕓 HISTORIAL
# =========================================================
def usuario_actual(): return current_user.email if current_user and current_user.is_authenticated else 'sistema'

def a_texto(v):
    if v is None: return ''
    if isinstance(v, float): return f"{round(v, 4):g}"
    return str(v)[:250]

def log_cambio(accion, p=None, campo='', antes=None, despues=None):
    if accion == 'editar' and a_texto(antes) == a_texto(despues): return
    db.session.add(HistorialCambio(
        usuario=usuario_actual(), producto_id=p.id if p is not None else None,
        producto_nombre=p.nombre if p is not None else '', accion=accion, campo=campo,
        valor_anterior=a_texto(antes), valor_nuevo=a_texto(despues)))

def valores_efectivos(p):
    return {
        'costo_real': get_val(p.costo_base_man, p.costo_base_ex, 0.0), 'costo_fab': get_val(p.costo_fab_man, p.costo_fab_ex, 0.0),
        'coyuntural': get_val(p.coyuntural_man, p.coyuntural_ex, 0.0), 'margen': get_val(p.margen_man, p.margen_ex, 0.20),
        'merma': p.merma_pct_man or 0.0, 'flete': p.flete_man, 'pv': p.pv_str or '', 'dist': p.dist_str or '', 'nota': p.nota or '',
        'codigo': p.codigo or '', 'empresa': p.empresa or '', 'origen': p.tipo_origen or '', 'odoo': p.en_odoo or '',
        'categoria': p.categoria or '', 'nombre': p.nombre or ''
    }

def log_diferencias(accion, p, antes):
    for campo, valor in valores_efectivos(p).items():
        if a_texto(antes.get(campo)) != a_texto(valor):
            log_cambio(accion, p, campo=campo, antes=antes.get(campo), despues=valor)

def registrar_precios(motivo):
    """Guarda una foto de precios de cada producto cuyo precio cambió (incluye efectos en cascada)."""
    try:
        prods = Producto.query.all()
        ctx = construir_contexto(prods)
        # Último registro por fecha (los importados de Excel antiguos tienen id mayor pero fecha anterior)
        sub = db.session.query(HistorialPrecio.producto_id, func.max(HistorialPrecio.fecha).label('fecha')).group_by(HistorialPrecio.producto_id).subquery()
        ultimos = {}
        for h in HistorialPrecio.query.join(sub, and_(HistorialPrecio.producto_id == sub.c.producto_id, HistorialPrecio.fecha == sub.c.fecha)).order_by(HistorialPrecio.id):
            ultimos[h.producto_id] = h
        usuario = usuario_actual()
        for p in prods:
            if p.oculto: continue
            r = calcular_producto(p, ctx)
            nuevo = dict(moneda=moneda_reporte(r), costo_total=round(r['costo_actual'], 4),
                         costo_coyuntural=round(r['costo_coyuntural'], 4), margen=r['margen'],
                         precio_lima=round(r['precio_lima'], 4), precio_provincia=round(r['precio_provincia'], 4))
            prev = ultimos.get(p.id)
            if prev and all(getattr(prev, k) == v for k, v in nuevo.items()): continue
            db.session.add(HistorialPrecio(producto_id=p.id, producto_nombre=p.nombre, usuario=usuario,
                                           motivo=motivo if prev else 'Registro inicial', **nuevo))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("No se pudo registrar el historial de precios")

_linea_base_lista = False

@app.before_request
def asegurar_linea_base():
    """Antes del primer cambio, guarda los precios actuales para que el historial tenga el punto de partida."""
    global _linea_base_lista
    if _linea_base_lista or request.method != 'POST' or not is_admin_api(): return
    if HistorialPrecio.query.first() is None: registrar_precios('Registro inicial')
    _linea_base_lista = True

def serializar_cambio(c):
    return {"fecha": fecha_lima(c.fecha), "usuario": c.usuario, "producto_id": c.producto_id, "producto": c.producto_nombre,
            "accion": c.accion, "campo": c.campo, "antes": c.valor_anterior, "despues": c.valor_nuevo}

# =========================================================
# 🚀 RUTAS Y LÓGICA DE NEGOCIO
# =========================================================
def error_interno(contexto):
    db.session.rollback()
    app.logger.exception(contexto)
    return jsonify({"error": "Ocurrió un error interno. Revisa los datos e intenta de nuevo."}), 500

def obtener_producto(d):
    """Busca el producto por id (preferido) o por nombre (compatibilidad)."""
    if d.get('id') not in (None, ''):
        try: return db.session.get(Producto, int(d['id']))
        except (TypeError, ValueError): return None
    nombre_req = d.get('nombre', '')
    return Producto.query.filter_by(nombre=nombre_req).first() or Producto.query.filter_by(nombre=limpiar_texto(nombre_req)).first()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form.get('email')).first()
        if u and check_password_hash(u.password, request.form.get('password')):
            login_user(u); return redirect(url_for('home'))
        flash('Acceso denegado')
    return render_template('login.html')

@app.route('/logout')
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/')
def home():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    return redirect(url_for('vista_vendedor')) if current_user.role in ['Vendedor', 'TC'] else redirect(url_for('vista_admin'))

@app.route('/admin')
@login_required
def vista_admin():
    if current_user.role not in ['Admin', 'SuperAdmin']: return redirect(url_for('home'))
    return render_template('index_admin.html')

@app.route('/vendedor')
@login_required
def vista_vendedor(): return render_template('index_vendedor.html')

@app.route('/usuarios')
@login_required
def gestion_usuarios():
    if current_user.role != 'SuperAdmin': return redirect(url_for('home'))
    return render_template('superadmin.html', usuarios=User.query.all())

def is_admin_api(): return current_user.is_authenticated and current_user.role in ['Admin', 'SuperAdmin']

@app.route('/api/crear-usuario', methods=['POST'])
@login_required
def crear_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    d = request.json
    if User.query.filter_by(email=d['email']).first(): return jsonify({"error": "Existe"}), 400
    db.session.add(User(email=d['email'], password=generate_password_hash(d['password']), role=d['role']))
    log_cambio('usuario', campo='crear', despues=f"{d['email']} ({d['role']})")
    db.session.commit(); return jsonify({"success": True})

@app.route('/api/editar-usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    d = request.json; u = db.session.get(User, d['id'])
    if u:
        if u.role != d['role']: log_cambio('usuario', campo=f"rol de {u.email}", antes=u.role, despues=d['role'])
        u.role = d['role']
        if d.get('password'):
            u.password = generate_password_hash(d['password'])
            log_cambio('usuario', campo=f"clave de {u.email}", despues='(cambiada)')
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/eliminar-usuario/<int:id>', methods=['POST'])
@login_required
def eliminar_usuario(id):
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    u = db.session.get(User, id)
    if u and u.id != current_user.id:
        log_cambio('usuario', campo='eliminar', antes=f"{u.email} ({u.role})")
        db.session.delete(u); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/update-tc', methods=['POST'])
@login_required
def update_tc():
    try:
        if current_user.role not in ['TC', 'Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
        c = Config.query.filter_by(clave='tipo_cambio').first()
        if not c: c = Config(clave='tipo_cambio', valor=3.80); db.session.add(c)
        nuevo = float(request.json['tc'])
        log_cambio('tipo-cambio', campo='tipo_cambio', antes=c.valor, despues=nuevo)
        c.valor = nuevo; db.session.commit()
        return jsonify({"success": True, "tc": c.valor})
    except (KeyError, TypeError, ValueError): return jsonify({"error": "Tipo de cambio inválido"}), 400
    except Exception: return error_interno("Error al actualizar TC")

@app.route('/subir-maestro', methods=['POST'])
@login_required
def subir_maestro():
    try:
        if not is_admin_api(): return jsonify({"error": "No authorized"}), 403
        f = request.files.get('archivo')
        if not f: return jsonify({"error": "Sin archivo"}), 400

        df = pd.read_excel(f, header=None)
        header_idx = 0
        for idx, row in df.iterrows():
            rs = ' '.join(str(x).lower() for x in row.values if pd.notna(x))
            if 'nombre' in rs or 'producto' in rs: header_idx = idx; break

        f.seek(0); df = pd.read_excel(f, header=header_idx)
        df.columns = [str(c).strip().lower().replace('.', '').replace('ó', 'o').replace('í', 'i') for c in df.columns]

        existentes = {p.nombre: p for p in Producto.query.all()}
        for _, row in df.iterrows():
            nombre = limpiar_texto(get_col_val(row, ['nombre', 'producto'], ''))
            if not nombre or nombre == 'NAN': continue

            c_base = robust_numeric(get_col_val(row, ['costo real', 'costo base', 'costo real (usd)']))
            c_fab = robust_numeric(get_col_val(row, ['costo de fabricacion', 'costo fab', 'costo fab (usd)']))
            coyun = robust_numeric(get_col_val(row, ['costo coyuntural', 'coyuntural', 'coyuntural (usd)']))

            emp = limpiar_texto(get_col_val(row, ['empresa', 'marca'], ''))
            if "NATAMICINA" in nombre.replace(' ', '') or "NISINA" in nombre.replace(' ', ''): emp = "INTERINSUMOS"

            prov = detectar_proveedor_exacto(nombre, emp)

            if es_nativo_soles(nombre): pass
            elif es_proveedor_soles_mixto(nombre, prov): c_fab /= 4.0
            elif es_excepcion_soles_clasica(nombre):
                c_base /= 4.0; c_fab /= 4.0
                if coyun > 0: coyun /= 4.0

            p = existentes.get(nombre)
            antes = valores_efectivos(p) if p else None
            if not p:
                p = Producto(nombre=nombre, oculto=False, usd_converted=True); db.session.add(p); existentes[nombre] = p

            p.codigo = str(get_col_val(row, ['referencia interna', 'codigo', 'referencia'], 'S/C')).strip()
            p.empresa = emp; p.proveedor = prov
            p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.oculto = False

            p.costo_base_ex = c_base; p.costo_fab_ex = c_fab; p.coyuntural_ex = coyun
            p.margen_ex = parse_percentage(get_col_val(row, ['margen', 'margen %', 'margen (%)']), 0.20)
            p.merma_pct_man = parse_percentage(get_col_val(row, ['margen de merma', 'merma', 'merma (%)']), 0.0)
            p.dscto_pv_ex = parse_percentage(get_col_val(row, ['pv autorizado', 'dscto pv', 'descuento pv']), 0.0)
            p.dscto_dist_ex = parse_percentage(get_col_val(row, ['dist exclusivo', 'dscto dist', 'descuento dist']), 0.0)

            p.costo_base_man = None; p.costo_fab_man = None; p.coyuntural_man = None
            p.margen_man = None; p.dscto_pv_man = None; p.dscto_dist_man = None

            pv_val = str(get_col_val(row, ['pv autorizado', 'dscto pv', 'descuento pv'], '')).strip()
            dist_val = str(get_col_val(row, ['dist exclusivo', 'dscto dist', 'descuento dist'], '')).strip()

            p.pv_str = '' if pv_val.lower() == 'nan' else pv_val[:10]
            p.dist_str = '' if dist_val.lower() == 'nan' else dist_val[:10]

            p.nota = str(get_col_val(row, ['nota', 'notas'], p.nota)).strip()
            if p.nota.lower() == 'nan': p.nota = ''
            p.fecha_act = ahora_utc()

            db.session.flush()
            if antes is None: log_cambio('carga-maestro', p, campo='crear', despues=nombre)
            else: log_diferencias('carga-maestro', p, antes)

        db.session.commit()
        registrar_precios(f"Carga de Excel maestro ({getattr(f, 'filename', '')})")
        return jsonify({"success": True})
    except Exception: return error_interno("Error en subir maestro")

@app.route('/api/subir-relaciones', methods=['POST'])
@login_required
def subir_relaciones():
    if not is_admin_api(): return jsonify({"error": "No"}), 403
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "No hay archivo"}), 400
    try:
        if f.filename.endswith('.csv'): df = pd.read_csv(f)
        else: df = pd.read_excel(f)

        df.columns = [str(c).strip().lower() for c in df.columns]
        todos = Producto.query.all()
        por_nombre = {p.nombre: p for p in todos}
        por_nombre_limpio = {}
        for prod in todos: por_nombre_limpio.setdefault(re.sub(r'\s+', ' ', prod.nombre.upper()), prod)

        for _, row in df.iterrows():
            nombre = ''
            if 'producto' in df.columns and pd.notna(row['producto']): nombre = limpiar_texto(row['producto'])
            elif 'nombre' in df.columns and pd.notna(row['nombre']): nombre = limpiar_texto(row['nombre'])

            if not nombre or nombre == 'NAN': continue

            p = por_nombre.get(nombre) or por_nombre_limpio.get(re.sub(r'\s+', ' ', nombre))

            if p:
                antes = valores_efectivos(p)
                if 'categoria' in df.columns and pd.notna(row['categoria']): p.categoria = limpiar_texto(row['categoria'])
                if 'referencia interna' in df.columns and pd.notna(row['referencia interna']): p.codigo = str(row['referencia interna']).strip()
                if 'codigo' in df.columns and pd.notna(row['codigo']): p.codigo = str(row['codigo']).strip()
                if 'columna1' in df.columns and pd.notna(row['columna1']): p.tipo_origen = limpiar_texto(row['columna1'])
                if 'odoo' in df.columns and pd.notna(row['odoo']): p.en_odoo = limpiar_texto(row['odoo'])
                log_diferencias('carga-atributos', p, antes)

        db.session.commit()
        registrar_precios(f"Carga de atributos ({f.filename})")
        return jsonify({"success": True})
    except Exception: return error_interno("Error en subir relaciones")

@app.route('/api/crear-producto', methods=['POST'])
@login_required
def crear_producto():
    try:
        if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
        d = request.json

        es_edicion = bool(d.get('id')) or bool(d.get('nombre_original'))
        nombre = limpiar_texto(d.get('nombre', ''))
        if not nombre: return jsonify({"error": "El producto debe tener un nombre."}), 400

        codigo_val = str(d.get('codigo', '')).upper().strip() or 'S/C'
        empresa_val = limpiar_texto(d.get('empresa', ''))
        if "NATAMICINA" in nombre.replace(' ', '') or "NISINA" in nombre.replace(' ', ''): empresa_val = "INTERINSUMOS"
        tipo_origen_val = limpiar_texto(d.get('origen', 'COMPRADO'))
        en_odoo_val = 'SI' if str(d.get('en_odoo', '')).strip().upper() in ['SI', 'SÍ'] else 'NO'

        prov = detectar_proveedor_exacto(nombre, empresa_val)

        c_base = robust_numeric(d.get('costo_base'))
        c_fab = robust_numeric(d.get('costo_fab'))
        coyun = robust_numeric(d.get('coyuntural'))

        if not es_edicion:
            if es_nativo_soles(nombre): pass
            elif es_proveedor_soles_mixto(nombre, prov): c_fab /= 4.0
            elif es_excepcion_soles_clasica(nombre):
                c_base /= 4.0; c_fab /= 4.0
                if coyun > 0: coyun /= 4.0

        merma = (robust_numeric(str(d.get('merma', '')).strip()) / 100.0) if str(d.get('merma', '')).strip() else 0.0
        margen_val = str(d.get('margen', '')).strip()
        margen = (robust_numeric(margen_val) / 100.0) if margen_val != '' else 0.20

        pv_str_val = str(d.get('dscto_pv', '')).strip()[:10]
        dist_str_val = str(d.get('dscto_dist', '')).strip()[:10]

        if d.get('id'): p = obtener_producto({'id': d['id']})
        elif d.get('nombre_original'): p = obtener_producto({'nombre': d['nombre_original']})
        else: p = Producto.query.filter_by(nombre=nombre).first()

        otro = Producto.query.filter_by(nombre=nombre).first()
        if es_edicion and p and otro and otro.id != p.id:
            return jsonify({"error": "Ya existe otro producto con ese nombre."}), 400

        if p:
            antes = valores_efectivos(p)
            p.nombre = nombre; p.codigo = codigo_val; p.empresa = empresa_val
            p.proveedor = prov; p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
            p.margen_man = margen; p.merma_pct_man = merma
            p.pv_str = pv_str_val; p.dist_str = dist_str_val; p.tipo_origen = tipo_origen_val
            p.en_odoo = en_odoo_val
            p.oculto = False; p.es_manual = True; p.fecha_act = ahora_utc()
            log_diferencias('editar', p, antes)
        else:
            p = Producto(nombre=nombre, codigo=codigo_val, empresa=empresa_val, es_manual=True, oculto=False, tipo_origen=tipo_origen_val, usd_converted=True, en_odoo=en_odoo_val)
            p.proveedor = prov; p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
            p.margen_man = margen; p.merma_pct_man = merma
            p.pv_str = pv_str_val; p.dist_str = dist_str_val
            db.session.add(p); db.session.flush()
            log_cambio('crear', p, campo='producto', despues=nombre)

        db.session.commit()
        registrar_precios(f"{'Edición' if es_edicion else 'Creación'} de {nombre}")
        return jsonify({"success": True, "id": p.id})
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Ya existe un producto con ese nombre."}), 400
    except Exception: return error_interno("Error al crear producto")

@app.route('/api/eliminar-producto', methods=['POST'])
@login_required
def eliminar_producto():
    try:
        if not is_admin_api(): return jsonify({"error": "No"}), 403
        p = obtener_producto(request.json)
        if p:
            nombre = p.nombre
            if p.es_manual:
                log_cambio('eliminar', p, campo='producto', antes=nombre)
                db.session.delete(p)
            else:
                log_cambio('ocultar', p, campo='oculto', antes='NO', despues='SÍ')
                p.oculto = True
            db.session.commit()
            registrar_precios(f"Eliminación de {nombre}")
        return jsonify({"success": True})
    except Exception: return error_interno("Error al eliminar producto")

@app.route('/api/toggle-visibilidad', methods=['POST'])
@login_required
def toggle_visibilidad():
    try:
        if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
        p = obtener_producto(request.json)
        if p:
            estado_actual = p.visible_ventas if p.visible_ventas is not None else True
            p.visible_ventas = not estado_actual
            log_cambio('visibilidad', p, campo='visible_ventas', antes='SÍ' if estado_actual else 'NO', despues='NO' if estado_actual else 'SÍ')
            db.session.commit()
        return jsonify({"success": True})
    except Exception: return error_interno("Error en toggle visibilidad")

@app.route('/api/toggle-nota', methods=['POST'])
@login_required
def toggle_nota_visibilidad():
    try:
        if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
        p = obtener_producto(request.json)
        if p:
            p.nota_visible = not p.nota_visible
            log_cambio('visibilidad', p, campo='nota_visible', antes='NO' if p.nota_visible else 'SÍ', despues='SÍ' if p.nota_visible else 'NO')
            db.session.commit()
        return jsonify({"success": True})
    except Exception: return error_interno("Error en toggle nota")

CAMPOS_EDITABLES = {'nota': 'nota', 'dscto': 'pv', 'dscto-dist': 'dist', 'margen': 'margen', 'merma': 'merma',
                    'costo-real': 'costo_real', 'costo-fab': 'costo_fab', 'costo-coyuntural': 'coyuntural', 'flete': 'flete'}

@app.route('/api/editar-<tipo>', methods=['POST'])
@login_required
def editar_celdas(tipo):
    try:
        if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
        if tipo not in CAMPOS_EDITABLES: return jsonify({"error": "Campo no válido"}), 400

        p = obtener_producto(request.json)
        if not p: return jsonify({"error": "No existe"}), 404

        val_str = str(request.json.get('valor', '')).strip()
        campo = CAMPOS_EDITABLES[tipo]
        antes = valores_efectivos(p)[campo]

        if tipo == 'nota': p.nota = val_str
        elif tipo == 'dscto': p.pv_str = val_str[:10]
        elif tipo == 'dscto-dist': p.dist_str = val_str[:10]
        else:
            val = robust_numeric(request.json.get('valor', 0))
            if tipo == 'margen': p.margen_man = val / 100.0
            elif tipo == 'merma': p.merma_pct_man = val / 100.0
            elif tipo == 'costo-real': p.costo_base_man = val if val >= 0 else None
            elif tipo == 'costo-fab': p.costo_fab_man = val if val >= 0 else None
            elif tipo == 'flete': p.flete_man = val if val >= 0 else None
            elif tipo == 'costo-coyuntural':
                p.coyuntural_man = val if val > 0 else -1.0

                if p.tipo_origen == 'COMPRADO':
                    core_actual = get_core_name(p.nombre)
                    peso_actual = get_quantity_normalized(p.nombre)

                    todos_comprados = Producto.query.filter_by(tipo_origen='COMPRADO').all()
                    hermanos = [h for h in todos_comprados if get_core_name(h.nombre) == core_actual and not h.oculto]

                    if hermanos:
                        pesos = [get_quantity_normalized(h.nombre) for h in hermanos]
                        pesos_normales = [peso for peso in pesos if peso < 100.0]
                        peso_original = max(pesos_normales) if pesos_normales else (max(pesos) if pesos else 0.0)

                        if peso_actual == peso_original:
                            for hermano in hermanos:
                                if hermano.id != p.id:
                                    log_cambio('propagacion', hermano, campo='coyuntural', antes=valores_efectivos(hermano)['coyuntural'], despues=val if val > 0 else -1.0)
                                    hermano.coyuntural_man = val if val > 0 else -1.0

        log_cambio('editar', p, campo=campo, antes=antes, despues=valores_efectivos(p)[campo])
        p.fecha_act = ahora_utc()
        db.session.commit()
        if tipo not in ('nota', 'dscto', 'dscto-dist'): registrar_precios(f"Edición de {campo} en {p.nombre}")
        return jsonify({"success": True})
    except Exception: return error_interno(f"Error al editar {tipo}")

@app.route('/buscar')
@login_required
def buscar():
    """Lista de productos con precios calculados. Solo lectura: no escribe en la base de datos."""
    try:
        q = limpiar_texto(request.args.get('q', ''))
        tc = get_tc_actual()
        # ?como=vendedor: el admin ve exactamente lo mismo que un vendedor (vista previa)
        es_vendedor = current_user.role in ['Vendedor', 'TC'] or request.args.get('como') == 'vendedor'

        prods = Producto.query.all()
        ctx = construir_contexto(prods)
        res, alertas = [], []

        for p in prods:
            if p.oculto: continue
            if es_vendedor and not es_visible_ventas(p): continue
            if q and q not in p.nombre.upper() and q not in str(p.codigo).upper(): continue

            r = calcular_producto(p, ctx)
            alertas.extend({"producto": p.nombre, "id": p.id, "msg": m} for m in r.pop('alertas'))
            res.append(para_vendedor(r) if es_vendedor else r)

        res.sort(key=lambda x: (get_core_name(x['nombre']), -get_quantity_normalized(x['nombre']), x['nombre']))
        return jsonify({"productos": res, "tc_actual": tc, "alertas": [] if es_vendedor else alertas})
    except Exception:
        app.logger.exception("Error fatal en buscar")
        return jsonify({"productos": [], "tc_actual": 3.80, "alertas": [], "error": "No se pudieron cargar los productos."}), 500

@app.route('/api/exportar', methods=['POST'])
@login_required
def exportar_excel():
    try:
        if not is_admin_api(): return jsonify({"error": "No authorized"}), 403
        ids = {int(i) for i in request.json.get('ids', [])}
        nombres = set(request.json.get('productos', []))

        todos = Producto.query.all()
        ctx = construir_contexto(todos)
        data = []

        for p in todos:
            if p.id not in ids and p.nombre not in nombres: continue
            r = calcular_producto(p, ctx)
            data.append({
                "Nombre": str(p.nombre),
                "Referencia interna": str(p.codigo),
                "Precio de venta": round(r['precio_lima'], 2),
                "Kilaje": str(get_quantity(p.nombre)),
                "Empresa": str(p.empresa),
                "Categoría": str(p.categoria),
                "Origen": str(p.tipo_origen),
                "Odoo": r['en_odoo'],
                "Moneda": moneda_reporte(r),
                "Costo Real": round(r['costo_base'], 2),
                "Costo Fab": round(r['costo_fab'], 2),
                "Merma (%)": r['merma_porcentaje'],
                "Costo Total": round(r['costo_actual'], 2),
                "Coyuntural": round(r['costo_coyuntural'], 2),
                "Margen (%)": r['margen'],
                "PV Autorizado": r['pv_str'],
                "Dist Exclusivo": r['dist_str'],
                "Precio PROVINCIA": round(r['precio_provincia'], 2),
                "Visible Ventas": "SÍ" if (p.visible_ventas if p.visible_ventas is not None else True) else "NO",
                "Nota": str(p.nota)
            })
        df = pd.DataFrame(data); output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, download_name='Precios_GLI.xlsx', as_attachment=True)
    except Exception: return error_interno("Error fatal en exportar")

@app.route('/api/historial/<int:producto_id>')
@login_required
def historial_producto(producto_id):
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    precios = HistorialPrecio.query.filter_by(producto_id=producto_id).order_by(HistorialPrecio.fecha, HistorialPrecio.id).all()
    cambios = HistorialCambio.query.filter_by(producto_id=producto_id).order_by(HistorialCambio.id.desc()).limit(200).all()
    return jsonify({
        "precios": [{"fecha": fecha_lima(h.fecha), "moneda": h.moneda, "costo_total": h.costo_total, "coyuntural": h.costo_coyuntural,
                     "margen": h.margen, "precio_lima": h.precio_lima, "precio_provincia": h.precio_provincia,
                     "usuario": h.usuario, "motivo": h.motivo} for h in precios],
        "cambios": [serializar_cambio(c) for c in cambios]
    })

@app.route('/api/historial')
@login_required
def historial_reciente():
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    limite = min(request.args.get('limite', 300, type=int), 2000)
    cambios = HistorialCambio.query.order_by(HistorialCambio.id.desc()).limit(limite).all()
    return jsonify({"cambios": [serializar_cambio(c) for c in cambios]})

if __name__ == '__main__': app.run(debug=True)
