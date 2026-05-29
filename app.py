import os
import io
import re
import time
from datetime import datetime
import pandas as pd
from flask import Flask, jsonify, request, render_template, send_file, redirect, url_for, flash
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text 

os.environ['TZ'] = 'America/Lima'
try: time.tzset()
except AttributeError: pass 

app = Flask(__name__, template_folder='templates')
CORS(app)

app.config['SECRET_KEY'] = 'GLI_EXECUTIVE_PRO_2026'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///gli_database.sqlite')
if app.config['SQLALCHEMY_DATABASE_URI'] and app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)
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
    
    pv_str = db.Column(db.String(10), default='')
    dist_str = db.Column(db.String(10), default='')
    
    es_manual = db.Column(db.Boolean, default=False)
    oculto = db.Column(db.Boolean, default=False)
    nota = db.Column(db.String(250), default='') 
    categoria = db.Column(db.String(100), default='')
    tipo_origen = db.Column(db.String(20), default='COMPRADO')
    visible_ventas = db.Column(db.Boolean, default=True)
    usd_converted = db.Column(db.Boolean, default=False)
    fecha_act = db.Column(db.DateTime, default=datetime.utcnow)

class Alerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(50))
    msg = db.Column(db.String(250))
    producto = db.Column(db.String(250))
    tipo = db.Column(db.String(50), default="INFO")

with app.app_context(): 
    db.create_all()
    try: 
        db.session.execute(text("ALTER TABLE producto ADD COLUMN pv_str VARCHAR(10) DEFAULT ''"))
        db.session.execute(text("ALTER TABLE producto ADD COLUMN dist_str VARCHAR(10) DEFAULT ''"))
        db.session.commit()
    except: 
        db.session.rollback()

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# =========================================================
# 🛠️ FUNCIONES Y EXCEPCIONES LOGICAS
# =========================================================
FLETE_ESTANDAR = 0.11 

# CLÁSICOS (Base y Fab se dividen/multiplican x 4)
EXCEPCIONES_SOLES = [
    "COLAGENO HIDROLIZADO GELNEX X 1KG", "COLAGENO HIDROLIZADO GELNEX X 400G",
    "FOSFATO PARA JAMONES BUDENHEIM X 1KG", "FOSFATO PARA JAMONES BUDENHEIM X 5KG",
    "FOSFATO PARA MASAS BUDENHEIM X 1KG", "FOSFATO PARA MASAS BUDENHEIM X 5KG",
    "POLVO DE HORNEAR LEVAMAX TOP P40 LINROS X 25KG", "POLVO DE HORNEAR LEVAMAX TOP P40 LINROS X 5KG",
    "PREPARADO VITAMINA C LINROS X 500G",
    "SAL DE CURA CONCENTRADA TECNAS X 1KG", "SAL DE CURA CONCENTRADA TECNAS X 25KG", "SAL DE CURA CONCENTRADA TECNAS X 5KG",
    "AMILASA MALTOGENICA MTG1500"
]

# NATIVOS (Símbolo en Soles pero Costos Intactos)
EXCEPCIONES_NATIVAS = [
    "AGITADOR DE LECHE",
    "ALCOHOLIMETRO",
    "CARBONATO DE CALCIO",
    "MOLDERA ACERO INOX"
]

EXCEPCIONES_SACCO_USD = ["LYOTO M 536 R", "LYOTO M 536 S", "LYOFAST AB 1", "LYOFAST Y 438 A", "LYOFAST Y 470 E"]
EXCEPCIONES_CLERICI_USD = ["TRANSGLUTAMINASA CAGLIFICIO CLERICI"]

def get_tc_actual():
    c = Config.query.filter_by(clave='tipo_cambio').first()
    if not c:
        c = Config(clave='tipo_cambio', valor=3.80); db.session.add(c); db.session.commit()
    return c.valor

def detectar_proveedor_exacto(nombre_odoo, empresa_col=""):
    n_up = str(nombre_odoo).upper()
    n_clean = re.sub(r'\s+', '', n_up)
    if "NATAMICINA" in n_clean or "NISINA" in n_clean: return "INTERINSUMOS"
    if "CRAMER" in n_up: return "CRAMER"
    if "SACCO" in n_up: return "SACCO"
    if "CLERICI" in n_up or "CAGLIFICIO" in n_up: return "CAGLIFICIO CLERICI"
    if "LUDAFA" in n_up: return "JM LUDAFA"
    return str(empresa_col).strip().upper()

# DEFINICIÓN DE FAMILIAS
def es_nativo_soles(nombre):
    n_up = nombre.upper()
    return any(exc in n_up for exc in EXCEPCIONES_NATIVAS)

def es_proveedor_soles_mixto(nombre, prov):
    n_upper = nombre.upper()
    if prov == "CAGLIFICIO CLERICI" or "CLERICI" in n_upper or "CAGLIFICIO" in n_upper:
        return not any(e in n_upper.replace(" ", "") for e in EXCEPCIONES_CLERICI_USD)
    if prov == "JM LUDAFA" or "LUDAFA" in n_upper:
        return True
    return False

def es_excepcion_soles_clasica(nombre):
    n_clean = re.sub(r'\s+', '', nombre.upper()).replace('Á', 'A').replace('Ó', 'O')
    if any(exc.replace(" ", "") in n_clean for exc in EXCEPCIONES_SOLES): return True
    if "COLAGENO" in n_clean and ("1KG" in n_clean or "400G" in n_clean): return True
    return False

def get_currency_info(nombre, proveedor):
    if es_nativo_soles(nombre): return "S/", "PEN"
    if es_proveedor_soles_mixto(nombre, proveedor): return "S/", "PEN"
    if es_excepcion_soles_clasica(nombre): return "S/", "PEN"
    
    if proveedor == "SACCO" or "SACCO" in nombre.upper():
        n_clean = re.sub(r'\s+', '', nombre.upper()).replace('Á', 'A').replace('Ó', 'O')
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
    if man is not None: return float(man)
    if ex is not None: return float(ex)
    return default

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
    core = re.sub(r'\bX?\s*\d+(?:[\.,]\d+)?\s*(?:KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', '', name, flags=re.IGNORECASE)
    core = re.sub(r'[^a-zA-Z0-9\s]', '', core)
    return re.sub(r'\s+', ' ', core).strip()

def get_quantity_normalized(name):
    match = re.search(r'\bX?\s*(\d+(?:[\.,]\d+)?)\s*(KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', name, flags=re.IGNORECASE)
    if match:
        try: 
            val = float(match.group(1).replace(',', '.'))
            unit = match.group(2).upper()
            if unit in ['G', 'GR', 'GRS', 'ML']: val /= 1000.0
            return val
        except: pass
    return 0.0

def es_excepcion_herencia(nombre):
    n_clean = re.sub(r'\s+', '', nombre).upper()
    excs = ["COLAGENOHIDROLIZADOGELNEXX1KG", "COLAGENOHIDROLIZADOGELNEXX400G", "FOSFATOPARAJAMONES", "FOSFATOPARAMASAS", "AMILASAMALTOGENICAMTG1500"]
    return any(e in n_clean for e in excs)

# =========================================================
# 🚀 RUTAS Y LÓGICA DE NEGOCIO
# =========================================================
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
    db.session.commit(); return jsonify({"success": True})

@app.route('/api/editar-usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    d = request.json; u = User.query.get(d['id'])
    if u:
        u.role = d['role']
        if d.get('password'): u.password = generate_password_hash(d['password'])
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/eliminar-usuario/<int:id>', methods=['POST'])
@login_required
def eliminar_usuario(id):
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    u = User.query.get(id)
    if u and u.id != current_user.id: db.session.delete(u); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/update-tc', methods=['POST'])
@login_required
def update_tc():
    if current_user.role not in ['TC', 'Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
    c = Config.query.filter_by(clave='tipo_cambio').first()
    if not c: c = Config(clave='tipo_cambio', valor=3.80); db.session.add(c)
    c.valor = float(request.json['tc']); db.session.commit()
    return jsonify({"success": True, "tc": c.valor})

@app.route('/subir-maestro', methods=['POST'])
@login_required
def subir_maestro():
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "Sin archivo"}), 400
    try: df = pd.read_excel(f, header=None)
    except: return jsonify({"error": "Error al leer"}), 400
    
    header_idx = 0
    for idx, row in df.iterrows():
        rs = ' '.join(str(x).lower() for x in row.values if pd.notna(x))
        if 'nombre' in rs or 'producto' in rs: header_idx = idx; break
            
    f.seek(0); df = pd.read_excel(f, header=header_idx)
    df.columns = [str(c).strip().lower().replace('.', '').replace('ó', 'o').replace('í', 'i') for c in df.columns]
    
    for _, row in df.iterrows():
        nombre = str(get_col_val(row, ['nombre', 'producto'], '')).strip().upper()
        if not nombre or nombre == 'NAN': continue
        
        c_base = robust_numeric(get_col_val(row, ['costo real', 'costo base', 'costo real (usd)']))
        c_fab = robust_numeric(get_col_val(row, ['costo de fabricacion', 'costo fab', 'costo fab (usd)']))
        coyun = robust_numeric(get_col_val(row, ['costo coyuntural', 'coyuntural', 'coyuntural (usd)']))
        
        emp = str(get_col_val(row, ['empresa', 'marca'], '')).strip().upper()
        n_clean_check = re.sub(r'\s+', '', nombre)
        if "NATAMICINA" in n_clean_check or "NISINA" in n_clean_check: emp = "INTERINSUMOS"
            
        prov = detectar_proveedor_exacto(nombre, emp)
        
        # Filtros de familia para subida
        is_mixto = es_proveedor_soles_mixto(nombre, prov)
        is_nativo = es_nativo_soles(nombre)
        is_clasica = es_excepcion_soles_clasica(nombre)

        if is_nativo:
            pass # Nativos se quedan puros
        elif is_mixto:
            c_fab /= 4.0 # Mixtos solo dividen el FAB
        elif is_clasica:
            c_base /= 4.0; c_fab /= 4.0
            if coyun > 0: coyun /= 4.0

        p = Producto.query.filter_by(nombre=nombre).first()
        if not p: p = Producto(nombre=nombre, oculto=False, usd_converted=True); db.session.add(p)
            
        p.codigo = str(get_col_val(row, ['referencia interna', 'codigo', 'referencia'], 'S/C')).strip()
        p.empresa = emp; p.proveedor = prov
        p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
        p.oculto = False 
        
        p.costo_base_ex = c_base
        p.costo_fab_ex = c_fab
        p.coyuntural_ex = coyun
        p.margen_ex = parse_percentage(get_col_val(row, ['margen', 'margen %', 'margen (%)']), 0.20)
        p.merma_pct_man = parse_percentage(get_col_val(row, ['margen de merma', 'merma', 'merma (%)']), 0.0)
        
        p.dscto_pv_ex = parse_percentage(get_col_val(row, ['pv autorizado', 'dscto pv', 'descuento pv']), 0.0)
        p.dscto_dist_ex = parse_percentage(get_col_val(row, ['dist exclusivo', 'dscto dist', 'descuento dist']), 0.0)
        
        p.costo_base_man = None
        p.costo_fab_man = None
        p.coyuntural_man = None
        p.margen_man = None
        p.dscto_pv_man = None
        p.dscto_dist_man = None
        
        pv_val = str(get_col_val(row, ['pv autorizado', 'dscto pv', 'descuento pv'], '')).strip()
        dist_val = str(get_col_val(row, ['dist exclusivo', 'dscto dist', 'descuento dist'], '')).strip()
        
        if pv_val.lower() == 'nan': pv_val = ''
        if dist_val.lower() == 'nan': dist_val = ''
        
        p.pv_str = pv_val[:10]
        p.dist_str = dist_val[:10]
        
        p.nota = str(get_col_val(row, ['nota', 'notas'], p.nota)).strip()
        if p.nota.lower() == 'nan': p.nota = ''
        
        p.fecha_act = datetime.utcnow()

    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/subir-relaciones', methods=['POST'])
@login_required
def subir_relaciones():
    if not is_admin_api(): return jsonify({"error": "No"}), 403
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "No hay archivo"}), 400
    try:
        df = pd.read_excel(f)
        df.columns = [str(c).strip().lower() for c in df.columns]
        for _, row in df.iterrows():
            nombre = str(row.get('nombre', '')).strip().upper()
            if not nombre: continue
            
            nombre_clean = re.sub(r'\s+', ' ', nombre)
            p = Producto.query.filter_by(nombre=nombre).first()
            if not p:
                for prod in Producto.query.all():
                    if re.sub(r'\s+', ' ', prod.nombre.upper()) == nombre_clean:
                        p = prod; break

            if p:
                p.categoria = str(row.get('categoria', '')).strip().upper()
                p.codigo = str(row.get('referencia interna', p.codigo)).strip()
                p.tipo_origen = str(row.get('columna1', 'COMPRADO')).strip().upper()
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/crear-producto', methods=['POST'])
@login_required
def crear_producto():
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    
    nombre_original = d.get('nombre_original')
    nombre = str(d.get('nombre', '')).upper().strip()
    if not nombre: return jsonify({"error": "El producto debe tener un nombre."}), 400
    
    codigo_val = str(d.get('codigo', '')).upper().strip() or 'S/C'
    empresa_val = str(d.get('empresa', '')).upper().strip()
    n_clean_check = re.sub(r'\s+', '', nombre)
    if "NATAMICINA" in n_clean_check or "NISINA" in n_clean_check: empresa_val = "INTERINSUMOS"
    tipo_origen_val = str(d.get('origen', 'COMPRADO')).upper().strip()
    
    prov = detectar_proveedor_exacto(nombre, empresa_val)
    
    c_base = robust_numeric(d.get('costo_base'))
    c_fab = robust_numeric(d.get('costo_fab'))
    coyun = robust_numeric(d.get('coyuntural'))

    if not nombre_original: 
        is_mixto = es_proveedor_soles_mixto(nombre, prov)
        is_nativo = es_nativo_soles(nombre)
        is_clasica = es_excepcion_soles_clasica(nombre)

        if is_nativo: pass
        elif is_mixto: c_fab /= 4.0
        elif is_clasica:
            c_base /= 4.0; c_fab /= 4.0
            if coyun > 0: coyun /= 4.0

    merma = (robust_numeric(str(d.get('merma', '')).strip()) / 100.0) if str(d.get('merma', '')).strip() else 0.0
    margen_val = str(d.get('margen', '')).strip()
    margen = (robust_numeric(margen_val) / 100.0) if margen_val != '' else 0.20
    
    pv_str_val = str(d.get('dscto_pv', '')).strip()[:10]
    dist_str_val = str(d.get('dscto_dist', '')).strip()[:10]

    if nombre_original:
        p = Producto.query.filter_by(nombre=nombre_original).first()
        if p:
            p.nombre = nombre
            p.codigo = codigo_val
            p.empresa = empresa_val
            p.proveedor = prov
            p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
            p.margen_man = margen; p.merma_pct_man = merma
            p.pv_str = pv_str_val; p.dist_str = dist_str_val
            p.tipo_origen = tipo_origen_val
    else:
        p = Producto.query.filter_by(nombre=nombre).first()
        if p:
            p.oculto = False; p.es_manual = True; p.codigo = codigo_val; p.empresa = empresa_val
            p.proveedor = prov; p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
            p.margen_man = margen; p.merma_pct_man = merma
            p.pv_str = pv_str_val; p.dist_str = dist_str_val
            p.tipo_origen = tipo_origen_val
        else:
            p = Producto(nombre=nombre, codigo=codigo_val, empresa=empresa_val, es_manual=True, oculto=False, tipo_origen=tipo_origen_val, usd_converted=True)
            p.proveedor = prov; p.moneda_simbolo = '$'; p.moneda_texto = 'USD'
            p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
            p.margen_man = margen; p.merma_pct_man = merma
            p.pv_str = pv_str_val; p.dist_str = dist_str_val
            db.session.add(p)
        
    db.session.commit(); return jsonify({"success": True})

@app.route('/api/eliminar-producto', methods=['POST'])
@login_required
def eliminar_producto():
    if not is_admin_api(): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p:
        if p.es_manual: db.session.delete(p)
        else: p.oculto = True
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/toggle-visibilidad', methods=['POST'])
@login_required
def toggle_visibilidad():
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p:
        estado_actual = p.visible_ventas if p.visible_ventas is not None else True
        p.visible_ventas = not estado_actual
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-<tipo>', methods=['POST'])
@login_required
def editar_celdas(tipo):
    if not is_admin_api(): return jsonify({"error": "No autorizado"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if not p: return jsonify({"error": "No existe"}), 404
    
    val_str = str(request.json.get('valor', '')).strip()
    
    if tipo == 'nota': p.nota = val_str
    elif tipo == 'dscto': p.pv_str = val_str[:10]
    elif tipo == 'dscto-dist': p.dist_str = val_str[:10]
    else:
        val = robust_numeric(request.json.get('valor', 0))
        if tipo == 'margen': p.margen_man = val / 100.0
        elif tipo == 'merma': p.merma_pct_man = val / 100.0
        elif tipo == 'costo-real': p.costo_base_man = val if val >= 0 else None
        elif tipo == 'costo-fab': p.costo_fab_man = val if val >= 0 else None
        elif tipo == 'costo-coyuntural': p.coyuntural_man = val if val > 0 else -1.0
        
    db.session.commit(); return jsonify({"success": True})

@app.route('/buscar')
@login_required
def buscar():
    try:
        q = request.args.get('q', '').upper()
        tc = get_tc_actual()
        Alerta.query.filter_by(tipo="ACTIVA").delete()
        es_vendedor = current_user.role in ['Vendedor', 'TC']
        
        prods = Producto.query.all()
        res = []
        data_comprados = []
        for c in prods:
            if c.tipo_origen == 'COMPRADO' and not c.oculto:
                core_val = get_core_name(c.nombre)
                data_comprados.append({'nombre': c.nombre,'costo_usd': get_val(c.costo_base_man, c.costo_base_ex, 0.0),'core': core_val,'clean': c.nombre.replace(' ', '').upper()})

        for p in prods:
            if p.oculto: continue
            if "CUAJO IL CASARO SACHETS CAGLIFICIO CLERICI" in p.nombre.upper(): p.visible_ventas = True
            visible = p.visible_ventas if p.visible_ventas is not None else True
            if es_vendedor and not visible: continue
            if q and q not in p.nombre.upper() and q not in str(p.codigo).upper(): continue
            
            prov_real = detectar_proveedor_exacto(p.nombre, str(p.empresa or ''))
            p.moneda_simbolo, p.moneda_texto = get_currency_info(p.nombre, prov_real)
            
            c_base_db = get_val(p.costo_base_man, p.costo_base_ex, 0.0)
            editable_costo = True 
            
            if p.tipo_origen == 'FABRICADO' and not es_excepcion_herencia(p.nombre):
                core_fab = get_core_name(p.nombre)
                c_heredado_usd = 0.0
                p_padres = [d for d in data_comprados if d['core'] == core_fab]
                if p_padres:
                    if 'ESENCIA' in str(p.categoria).upper():
                        p_5 = [d for d in p_padres if '5K' in d['clean'] or '5L' in d['clean']]
                        c_heredado_usd = p_5[0]['costo_usd'] if p_5 else p_padres[0]['costo_usd']
                    else: c_heredado_usd = p_padres[0]['costo_usd']
                if c_heredado_usd > 0:
                    if p.costo_base_man is not None and p.costo_base_man > 0:
                        c_base_db = p.costo_base_man; editable_costo = True 
                    else:
                        c_base_db = c_heredado_usd; editable_costo = False 

            c_fab_db = get_val(p.costo_fab_man, p.costo_fab_ex, 0.0)
            coyun_db = get_val(p.coyuntural_man, p.coyuntural_ex, 0.0)
            mg = get_val(p.margen_man, p.margen_ex, 0.20)
            merma_pct = p.merma_pct_man or 0.0
            
            is_nativo = es_nativo_soles(p.nombre)
            is_mixto = es_proveedor_soles_mixto(p.nombre, prov_real)
            is_clasica = es_excepcion_soles_clasica(p.nombre)
            is_frag = 'FRAGANCIA' in str(p.categoria).upper() or 'FRAGANCIA' in p.nombre.upper()

            # 🔥 CÁLCULO SEPARADO POR FAMILIAS 🔥
            if is_nativo:
                base_pen = c_base_db
                fab_pen = c_fab_db
                coyun_pen = coyun_db
                merma_pen = base_pen * merma_pct
                ct_pen = base_pen + fab_pen + merma_pen
                c_ref_pen = coyun_pen if (coyun_pen > 0 and ct_pen <= coyun_pen) else ct_pen
                p_lima_pen = c_ref_pen * (1 + mg)
                p_prov_pen = p_lima_pen + (0.0 if prov_real in ["CRAMER", "SACCO", "JM LUDAFA"] else FLETE_ESTANDAR * 4.0)
                
                c_base_usd_send = base_pen / 4.0
                c_fab_usd_send = fab_pen / 4.0
                coyun_usd_send = coyun_pen / 4.0
                merma_monto_usd_send = merma_pen / 4.0
                ct_usd_send = ct_pen / 4.0
                p_lima_usd_send = p_lima_pen / 4.0
                p_prov_usd_send = p_prov_pen / 4.0
                factor_display = 4.0

            elif is_mixto:
                base_pen = c_base_db
                fab_pen = c_fab_db * 4.0
                coyun_pen = coyun_db
                merma_pen = base_pen * merma_pct
                ct_pen = base_pen + fab_pen + merma_pen
                c_ref_pen = coyun_pen if (coyun_pen > 0 and ct_pen <= coyun_pen) else ct_pen
                p_lima_pen = c_ref_pen * (1 + mg)
                p_prov_pen = p_lima_pen + 0.0 # Ludafa y Clerici son flete cero
                
                c_base_usd_send = base_pen / 4.0
                c_fab_usd_send = c_fab_db
                coyun_usd_send = coyun_pen / 4.0
                merma_monto_usd_send = merma_pen / 4.0
                ct_usd_send = ct_pen / 4.0
                p_lima_usd_send = p_lima_pen / 4.0
                p_prov_usd_send = p_prov_pen / 4.0
                factor_display = 4.0

            elif is_clasica:
                c_base_usd_send = c_base_db
                c_fab_usd_send = c_fab_db
                coyun_usd_send = coyun_db
                merma_monto_usd_send = c_base_usd_send * merma_pct
                ct_usd_send = c_base_usd_send + c_fab_usd_send + merma_monto_usd_send
                c_ref_usd = coyun_usd_send if (coyun_usd_send > 0 and ct_usd_send <= coyun_usd_send) else ct_usd_send
                p_lima_usd_send = c_ref_usd * (1 + mg)
                p_prov_usd_send = p_lima_usd_send + (0.0 if prov_real in ["CRAMER", "SACCO", "JM LUDAFA"] else FLETE_ESTANDAR)
                factor_display = 4.0

            else:
                c_base_usd_send = c_base_db
                c_fab_usd_send = c_fab_db
                coyun_usd_send = coyun_db
                merma_monto_usd_send = c_base_usd_send * merma_pct
                ct_usd_send = c_base_usd_send + c_fab_usd_send + merma_monto_usd_send
                c_ref_usd = coyun_usd_send if (coyun_usd_send > 0 and ct_usd_send <= coyun_usd_send) else ct_usd_send
                p_lima_usd_send = c_ref_usd * (1 + mg)
                p_prov_usd_send = p_lima_usd_send + (0.0 if prov_real in ["CRAMER", "SACCO", "JM LUDAFA"] and not is_frag else FLETE_ESTANDAR)
                factor_display = 1.0

            if coyun_db > 0 and ct_usd_send > coyun_usd_send:
                try: db.session.add(Alerta(fecha="ACTIVA", msg="Superó Costo Coyuntural", producto=p.nombre, tipo="ACTIVA"))
                except: pass

            res.append({
                "nombre": str(p.nombre), "codigo": str(p.codigo), "empresa": str(p.empresa or ''), 
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
                
                "costo_base_usd": float(c_base_usd_send), "costo_fab_usd": float(c_fab_usd_send), 
                "merma_monto_usd": float(merma_monto_usd_send), "costo_actual_usd": float(ct_usd_send),
                "costo_coyuntural_usd": float(coyun_usd_send), "precio_lima_usd": float(p_lima_usd_send),
                "precio_provincia_usd": float(p_prov_usd_send), 
                "es_pen_exception": (is_nativo or is_mixto or is_clasica),
                
                "moneda_simbolo": str(p.moneda_simbolo), "moneda_texto": str(p.moneda_texto), 
                
                "pv_str": str(format_discount(p.pv_str, p.dscto_pv_man, p.dscto_pv_ex)), 
                "dist_str": str(format_discount(p.dist_str, p.dscto_dist_man, p.dscto_dist_ex)),
                
                "nota": str(p.nota) if hasattr(p, 'nota') and p.nota else "",
                "visible_ventas": visible, "editable_costo": editable_costo
            })
        
        try: db.session.commit()
        except: db.session.rollback()
        
        res.sort(key=lambda x: (get_core_name(x['nombre']), -get_quantity_normalized(x['nombre']), x['nombre']))
        return jsonify({"productos": res, "tc_actual": tc, "alertas": [{"producto": a.producto, "msg": a.msg} for a in Alerta.query.filter_by(tipo="ACTIVA").all()]})
    except Exception as e: 
        print(f"Error fatal en buscar: {e}")
        return jsonify({"productos": [], "tc_actual": 3.80, "alertas": [{"producto": "Error", "msg": str(e)}]}), 500

@app.route('/api/exportar', methods=['POST'])
@login_required
def exportar_excel():
    if not is_admin_api(): return jsonify({"error": "No"}), 403
    nombres = request.json.get('productos', [])
    prods = Producto.query.filter(Producto.nombre.in_(nombres)).all()
    tc = get_tc_actual(); data = []
    
    data_comprados = []
    for c in Producto.query.all():
        if c.tipo_origen == 'COMPRADO' and not c.oculto:
            core_val = get_core_name(c.nombre)
            data_comprados.append({'nombre': c.nombre, 'costo_usd': get_val(c.costo_base_man, c.costo_base_ex, 0.0), 'core': core_val, 'w_core': set(core_val.split()), 'clean': c.nombre.replace(' ', '').upper()})
    
    for p in prods:
        prov_real = detectar_proveedor_exacto(p.nombre, str(p.empresa or ''))
        sim_real, txt_real = get_currency_info(p.nombre, prov_real)
        
        c_base_db = get_val(p.costo_base_man, p.costo_base_ex, 0.0)
        
        if p.tipo_origen == 'FABRICADO' and not es_excepcion_herencia(p.nombre):
            core_fab = get_core_name(p.nombre)
            c_heredado_usd = 0.0
            posibles_padres = [d for d in data_comprados if d['core'] == core_fab]
            if posibles_padres:
                if 'ESENCIA' in str(p.categoria).upper():
                    p_5 = [d for d in posibles_padres if '5K' in d['clean'] or '5L' in d['clean']]
                    c_heredado_usd = p_5[0]['costo_usd'] if p_5 else posibles_padres[0]['costo_usd']
                else: c_heredado_usd = posibles_padres[0]['costo_usd']
            if c_heredado_usd > 0: c_base_db = p.costo_base_man if (p.costo_base_man is not None and p.costo_base_man > 0) else c_heredado_usd

        c_fab_db = get_val(p.costo_fab_man, p.costo_fab_ex, 0.0)
        coyun_db = get_val(p.coyuntural_man, p.coyuntural_ex, 0.0)
        mg = get_val(p.margen_man, p.margen_ex, 0.20)
        merma_pct = p.merma_pct_man or 0.0
        
        is_nativo = es_nativo_soles(p.nombre)
        is_mixto = es_proveedor_soles_mixto(p.nombre, prov_real)
        is_clasica = es_excepcion_soles_clasica(p.nombre)
        is_frag = 'FRAGANCIA' in str(p.categoria).upper() or 'FRAGANCIA' in p.nombre.upper()
        flete_usd = 0.0 if (prov_real in ["CRAMER", "SACCO", "JM LUDAFA"] and not is_frag) else FLETE_ESTANDAR

        if is_nativo or is_mixto:
            base_final = c_base_db
            fab_final = (c_fab_db * 4.0) if is_mixto else c_fab_db
            coyun_final = coyun_db
            merma_final = base_final * merma_pct
            ct_final = base_final + fab_final + merma_final
            c_ref_final = coyun_final if (coyun_final > 0 and ct_final <= coyun_final) else ct_final
            p_lima_final = c_ref_final * (1 + mg)
            p_prov_final = p_lima_final + (0.0 if prov_real in ["CRAMER", "SACCO", "JM LUDAFA"] else flete_usd * 4.0)
            txt_final = 'PEN'
        else:
            base_usd = c_base_db
            fab_usd = c_fab_db
            coyun_usd = coyun_db
            merma_usd = base_usd * merma_pct
            ct_usd = base_usd + fab_usd + merma_usd
            c_ref_usd = coyun_usd if (coyun_usd > 0 and ct_usd <= coyun_usd) else ct_usd
            p_lima_usd = c_ref_usd * (1 + mg)
            p_prov_usd = p_lima_usd + flete_usd
            
            if is_clasica:
                base_final = base_usd * 4.0
                fab_final = fab_usd * 4.0
                coyun_final = coyun_usd * 4.0
                merma_final = merma_usd * 4.0
                ct_final = ct_usd * 4.0
                p_lima_final = p_lima_usd * 4.0
                p_prov_final = p_prov_usd * 4.0
                txt_final = 'PEN'
            else:
                base_final = base_usd
                fab_final = fab_usd
                coyun_final = coyun_usd
                merma_final = merma_usd
                ct_final = ct_usd
                p_lima_final = p_lima_usd
                p_prov_final = p_prov_usd
                txt_final = txt_real
            
        data.append({
            "Producto": p.nombre, "Kilaje": get_quantity(p.nombre), "Código": p.codigo, "Empresa": p.empresa, "Categoría": p.categoria, "Origen": p.tipo_origen, "Moneda": txt_final,
            "Costo Real": round(base_final, 2), "Costo Fab": round(fab_final, 2), "Merma (%)": round(merma_pct*100, 2), "Costo Total": round(ct_final, 2),
            "Coyuntural": round(coyun_final, 2), "Margen (%)": round(mg*100, 2), 
            "PV Autorizado": str(format_discount(p.pv_str, p.dscto_pv_man, p.dscto_pv_ex)), 
            "Dist Exclusivo": str(format_discount(p.dist_str, p.dscto_dist_man, p.dscto_dist_ex)),
            "Precio LIMA": round(p_lima_final, 2), "Precio PROVINCIA": round(p_prov_final, 2), 
            "Visible Ventas": "SÍ" if (p.visible_ventas if p.visible_ventas is not None else True) else "NO", "Nota": p.nota
        })
    df = pd.DataFrame(data); output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0); return send_file(output, download_name='Precios_GLI.xlsx', as_attachment=True)

if __name__ == '__main__': app.run(debug=True)
