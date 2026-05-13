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
    es_manual = db.Column(db.Boolean, default=False)
    oculto = db.Column(db.Boolean, default=False)
    nota = db.Column(db.String(250), default='') 
    categoria = db.Column(db.String(100), default='')
    tipo_origen = db.Column(db.String(20), default='COMPRADO')
    visible_ventas = db.Column(db.Boolean, default=True)
    fecha_act = db.Column(db.DateTime, default=datetime.utcnow)

class Alerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(50))
    msg = db.Column(db.String(250))
    producto = db.Column(db.String(250))
    tipo = db.Column(db.String(50), default="INFO")

with app.app_context(): 
    db.create_all()
    migraciones = [
        ('nota', 'VARCHAR(250)', "''"), 
        ('categoria', 'VARCHAR(100)', "''"), 
        ('tipo_origen', 'VARCHAR(20)', "'COMPRADO'"),
        ('visible_ventas', 'BOOLEAN', 'TRUE')
    ]
    for col, tip, val_def in migraciones:
        try:
            db.session.execute(text(f"ALTER TABLE producto ADD COLUMN {col} {tip} DEFAULT {val_def};"))
            db.session.commit()
        except: db.session.rollback()

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# =========================================================
# 🛠️ FUNCIONES Y EXCEPCIONES
# =========================================================
FLETE_ESTANDAR = 0.11 

EXCEPCIONES_SOLES = [
    "COLAGENO HIDROLIZADO GELNEX X 1KG", "COLAGENO HIDROLIZADO GELNEX X 400G",
    "FOSFATO PARA JAMONES BUDENHEIM X 1KG", "FOSFATO PARA JAMONES BUDENHEIM X 5KG",
    "FOSFATO PARA MASAS BUDENHEIM X 1KG", "FOSFATO PARA MASAS BUDENHEIM X 5KG",
    "POLVO DE HORNEAR LEVAMAX TOP P40 LINROS X 25KG", "PREPARADO VITAMINA C LINROS X 500G",
    "SAL DE CURA CONCENTRADA TECNAS X 1KG", "SAL DE CURA CONCENTRADA TECNAS X 25KG", "SAL DE CURA CONCENTRADA TECNAS X 5KG"
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

def get_currency_info(nombre, proveedor):
    n_upper = nombre.upper()
    n_clean = re.sub(r'\s+', '', n_upper).replace('Á', 'A').replace('Ó', 'O')
    for exc in EXCEPCIONES_SOLES:
        if exc.replace(" ", "").upper() in n_clean: return "S/", "PEN"
    if "COLAGENO" in n_clean:
        if "1KG" in n_clean or "400G" in n_clean: return "S/", "PEN"
        return "$", "USD"
    if proveedor == "CAGLIFICIO CLERICI" or "CLERICI" in n_upper or "CAGLIFICIO" in n_upper:
        for exc in EXCEPCIONES_CLERICI_USD:
            if exc.replace(" ", "").upper() in n_clean: return "$", "USD"
        return "S/", "PEN"
    if proveedor == "SACCO" or "SACCO" in n_upper:
        for exc in EXCEPCIONES_SACCO_USD:
            if exc.replace(" ", "") in n_clean: return "$", "USD"
        return "S/", "PEN"
    if proveedor == "JM LUDAFA" or "LUDAFA" in n_upper: return "S/", "PEN"
    return "$", "USD"

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

def get_quantity(name):
    match = re.search(r'\bX?\s*(\d+(?:[\.,]\d+)?)\s*(?:KG|KGS|KILO|KILOS|G|GR|GRS|L|LT|LTS|LITRO|LITROS|ML|LB|LBS|GAL|GALON|GALONES)\b', name, flags=re.IGNORECASE)
    if match:
        try: return float(match.group(1).replace(',', '.'))
        except: pass
    return 0.0

def son_familia(core1, core2):
    if core1 == core2: return True
    w1 = set(core1.split()); w2 = set(core2.split())
    if len(w1) >= 2 and len(w2) >= 2:
        if w1.issubset(w2) or w2.issubset(w1): return True
    return False

# 🔥 NUEVA REGLA: EXCEPCIONES QUE NO HEREDAN Y SON EDITABLES MANUALMENTE 🔥
def es_excepcion_herencia(nombre):
    n_clean = re.sub(r'\s+', '', nombre).upper()
    if "COLAGENOHIDROLIZADOGELNEXX1KG" in n_clean or "COLAGENOHIDROLIZADOGELNEXX400G" in n_clean: return True
    if "FOSFATOPARAJAMONES" in n_clean: return True
    if "FOSFATOPARAMASAS" in n_clean: return True
    return False

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
    df.columns = [str(c).strip().lower().replace('.', '').replace('ó', 'o') for c in df.columns]
    
    for _, row in df.iterrows():
        nombre = str(get_col_val(row, ['nombre', 'producto'], '')).strip().upper()
        if not nombre or nombre == 'NAN': continue
        
        c_base = robust_numeric(get_col_val(row, ['costo real', 'costo base']))
        c_fab = robust_numeric(get_col_val(row, ['costo de fabricacion', 'costo fab']))
        
        emp = str(get_col_val(row, ['empresa', 'marca'], '')).strip().upper()
        n_clean_check = re.sub(r'\s+', '', nombre)
        if "NATAMICINA" in n_clean_check or "NISINA" in n_clean_check: emp = "INTERINSUMOS"
            
        p = Producto.query.filter_by(nombre=nombre).first()
        if not p: p = Producto(nombre=nombre, oculto=False); db.session.add(p)
            
        p.codigo = str(get_col_val(row, ['referencia interna', 'codigo', 'referencia'], 'S/C')).strip()
        p.empresa = emp; p.proveedor = detectar_proveedor_exacto(nombre, emp)
        p.moneda_simbolo, p.moneda_texto = get_currency_info(nombre, p.proveedor)
        p.oculto = False 
        
        p.costo_base_ex = c_base; p.costo_fab_ex = c_fab
        p.coyuntural_ex = robust_numeric(get_col_val(row, ['costo coyuntural']))
        p.margen_ex = parse_percentage(get_col_val(row, ['margen', 'margen %']), 0.20)
        p.dscto_pv_ex = parse_percentage(get_col_val(row, ['dscto pv', 'descuento pv']), 0.0)
        p.dscto_dist_ex = parse_percentage(get_col_val(row, ['dscto dist', 'descuento dist']), 0.0)
        p.merma_pct_man = parse_percentage(get_col_val(row, ['margen de merma', 'merma']), 0.0)
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
    nombre = str(d.get('nombre', '')).upper().strip()
    if not nombre: return jsonify({"error": "El producto debe tener un nombre."}), 400
    
    codigo_val = str(d.get('codigo', '')).upper().strip() or 'S/C'
    empresa_val = str(d.get('empresa', '')).upper().strip()
    n_clean_check = re.sub(r'\s+', '', nombre)
    if "NATAMICINA" in n_clean_check or "NISINA" in n_clean_check: empresa_val = "INTERINSUMOS"
    
    p = Producto.query.filter_by(nombre=nombre).first()
    c_base = robust_numeric(d.get('costo_base'))
    c_fab = robust_numeric(d.get('costo_fab'))
    coyun = robust_numeric(d.get('coyuntural'))
    merma = (robust_numeric(str(d.get('merma', '')).strip()) / 100.0) if str(d.get('merma', '')).strip() else 0.0
    margen_val = str(d.get('margen', '')).strip()
    margen = (robust_numeric(margen_val) / 100.0) if margen_val != '' else 0.20
    dscto_pv = (robust_numeric(str(d.get('dscto_pv', '')).strip()) / 100.0) if str(d.get('dscto_pv', '')).strip() else 0.0
    dscto_dist = (robust_numeric(str(d.get('dscto_dist', '')).strip()) / 100.0) if str(d.get('dscto_dist', '')).strip() else 0.0

    if p:
        if not p.oculto: return jsonify({"error": "El producto ya existe y está activo."}), 400
        p.oculto = False; p.es_manual = True; p.codigo = codigo_val; p.empresa = empresa_val
        p.proveedor = detectar_proveedor_exacto(nombre, empresa_val)
        p.moneda_simbolo, p.moneda_texto = get_currency_info(nombre, p.proveedor)
        p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
        p.margen_man = margen; p.merma_pct_man = merma; p.dscto_pv_man = dscto_pv; p.dscto_dist_man = dscto_dist
    else:
        p = Producto(nombre=nombre, codigo=codigo_val, empresa=empresa_val, es_manual=True, oculto=False)
        p.proveedor = detectar_proveedor_exacto(nombre, p.empresa)
        p.moneda_simbolo, p.moneda_texto = get_currency_info(nombre, p.proveedor)
        p.costo_base_man = c_base; p.costo_fab_man = c_fab; p.coyuntural_man = coyun
        p.margen_man = margen; p.merma_pct_man = merma; p.dscto_pv_man = dscto_pv; p.dscto_dist_man = dscto_dist
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
    
    if tipo == 'nota': p.nota = str(request.json.get('valor', '')).strip()
    else:
        val = robust_numeric(request.json.get(tipo, request.json.get('costo', request.json.get('valor', 0))))
        if tipo == 'margen': p.margen_man = val / 100.0
        elif tipo == 'merma': p.merma_pct_man = val / 100.0
        elif tipo == 'costo-real': p.costo_base_man = val if val >= 0 else None
        elif tipo == 'costo-fab': p.costo_fab_man = val if val >= 0 else None
        elif tipo == 'costo-coyuntural': p.coyuntural_man = val if val > 0 else -1.0
        elif tipo == 'dscto': p.dscto_pv_man = val / 100.0
        elif tipo == 'dscto-dist': p.dscto_dist_man = val / 100.0
        
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
                data_comprados.append({
                    'nombre': c.nombre,
                    'costo': get_val(c.costo_base_man, c.costo_base_ex, 0.0),
                    'core': core_val,
                    'w_core': set(core_val.split()),
                    'clean': c.nombre.replace(' ', '').upper()
                })

        for p in prods:
            if p.oculto: continue
            
            visible = p.visible_ventas if p.visible_ventas is not None else True
            if es_vendedor and not visible: continue
            
            if q and q not in p.nombre.upper() and q not in str(p.codigo).upper(): continue
            
            prov_real = detectar_proveedor_exacto(p.nombre, p.empresa)
            sim_real, txt_real = get_currency_info(p.nombre, prov_real)
            
            n_clean_check = re.sub(r'\s+', '', p.nombre.upper())
            if "NATAMICINA" in n_clean_check or "NISINA" in n_clean_check:
                if p.empresa != "INTERINSUMOS": p.empresa = "INTERINSUMOS"
                
            if p.moneda_texto != txt_real or p.proveedor != prov_real:
                p.proveedor = prov_real; p.moneda_simbolo = sim_real; p.moneda_texto = txt_real
            
            c_base = get_val(p.costo_base_man, p.costo_base_ex, 0.0)
            editable_costo = True # Por defecto asumimos que se puede editar
            
            # 🔥 LÓGICA DE EXCEPCIONES: Si es Fabricado pero es excepción, NO hereda 🔥
            if p.tipo_origen == 'FABRICADO':
                if es_excepcion_herencia(p.nombre):
                    editable_costo = True # Mantiene su propio costo_base y botón activo
                else:
                    editable_costo = False # Se bloquea y hereda
                    core_fab = get_core_name(p.nombre)
                    c_heredado = 0.0
                    
                    posibles_padres = [d for d in data_comprados if d['core'] == core_fab]
                    if not posibles_padres:
                        w_fab = set(core_fab.split())
                        if len(w_fab) >= 2:
                            for d in data_comprados:
                                if w_fab.issubset(d['w_core']) or d['w_core'].issubset(w_fab): posibles_padres.append(d)
                    
                    if posibles_padres:
                        cat_upper = str(p.categoria).upper()
                        if 'ESENCIA' in cat_upper:
                            p_5 = [d for d in posibles_padres if '5K' in d['clean'] or '5L' in d['clean']]
                            if p_5: c_heredado = p_5[0]['costo']
                            else:
                                p_1 = [d for d in posibles_padres if '1K' in d['clean'] or '1L' in d['clean']]
                                if p_1: c_heredado = p_1[0]['costo']
                                else: c_heredado = posibles_padres[0]['costo']
                        else:
                            c_heredado = posibles_padres[0]['costo']
                    
                    if c_heredado > 0: 
                        c_base = c_heredado
                        if p.costo_base_man != c_heredado: p.costo_base_man = c_heredado
            
            c_fab = get_val(p.costo_fab_man, p.costo_fab_ex, 0.0)
            margen = get_val(p.margen_man, p.margen_ex, 0.20)
            coyun = get_val(p.coyuntural_man, p.coyuntural_ex, 0.0)
            merma_pct = p.merma_pct_man or 0.0
            dscto_pv = get_val(p.dscto_pv_man, p.dscto_pv_ex, 0.0)
            dscto_dist = get_val(p.dscto_dist_man, p.dscto_dist_ex, 0.0)
            
            if coyun < 0: coyun = 0.0
            merma_monto = c_base * merma_pct; c_total = c_base + c_fab + merma_monto
            
            if coyun > 0 and c_total > coyun:
                try: db.session.add(Alerta(fecha="ACTIVA", msg="Superó Costo Coyuntural", producto=p.nombre, tipo="ACTIVA"))
                except: pass
                
            c_ref = coyun if (coyun > 0 and c_total <= coyun) else c_total
            if c_ref <= 0.0001: margen = 0.0; p_lima = 0.0; p_prov = 0.0
            else:
                flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
                p_lima = c_ref * (1 + margen); p_prov = p_lima + flete
            
            res.append({
                "nombre": str(p.nombre), "codigo": str(p.codigo), "empresa": str(p.empresa or ''), "categoria": str(p.categoria or ''), "tipo_origen": str(p.tipo_origen or ''),
                "costo_base": c_base, "costo_fab": c_fab, "merma_porcentaje": round(merma_pct * 100, 2),
                "merma_monto": merma_monto, "costo_actual": c_total, "costo_coyuntural": coyun,
                "margen": round(margen * 100, 2), "precio_lima": p_lima, "precio_provincia": p_prov,
                "moneda_simbolo": str(p.moneda_simbolo), "moneda_texto": str(p.moneda_texto), 
                "dscto_pv": round(dscto_pv * 100, 2), "dscto_dist": round(dscto_dist * 100, 2),
                "nota": str(p.nota) if hasattr(p, 'nota') and p.nota else "",
                "visible_ventas": visible,
                "editable_costo": editable_costo # 🔴 Enviamos bandera al frontend
            })
        
        try: db.session.commit()
        except: db.session.rollback()

        res.sort(key=lambda x: x['nombre'])
        alertas_activas = [{"producto": a.producto, "msg": a.msg} for a in Alerta.query.filter_by(tipo="ACTIVA").all()]
        return jsonify({"productos": res, "tc_actual": tc, "alertas": alertas_activas})
    except Exception as e: return jsonify({"productos": [], "tc_actual": 3.80, "alertas": [{"producto": "Error de servidor", "msg": str(e)}]}), 500

@app.route('/api/exportar', methods=['POST'])
@login_required
def exportar_excel():
    if not is_admin_api(): return jsonify({"error": "No"}), 403
    nombres = request.json.get('productos', [])
    if not nombres: return jsonify({"error": "Vacío"}), 400
    prods = Producto.query.filter(Producto.nombre.in_(nombres)).all()
    
    data_comprados = []
    for c in Producto.query.all():
        if c.tipo_origen == 'COMPRADO' and not c.oculto:
            core_val = get_core_name(c.nombre)
            data_comprados.append({'nombre': c.nombre, 'costo': get_val(c.costo_base_man, c.costo_base_ex, 0.0), 'core': core_val, 'w_core': set(core_val.split()), 'clean': c.nombre.replace(' ', '').upper()})

    data = []; tc = get_tc_actual()
    for p in prods:
        c_base = get_val(p.costo_base_man, p.costo_base_ex, 0.0)
        
        if p.tipo_origen == 'FABRICADO' and not es_excepcion_herencia(p.nombre):
            core_fab = get_core_name(p.nombre)
            c_heredado = 0.0
            posibles_padres = [d for d in data_comprados if d['core'] == core_fab]
            if not posibles_padres:
                w_fab = set(core_fab.split())
                if len(w_fab) >= 2:
                    for d in data_comprados:
                        if w_fab.issubset(d['w_core']) or d['w_core'].issubset(w_fab): posibles_padres.append(d)
            if posibles_padres:
                if 'ESENCIA' in str(p.categoria).upper():
                    p_5 = [d for d in posibles_padres if '5K' in d['clean'] or '5L' in d['clean']]
                    if p_5: c_heredado = p_5[0]['costo']
                    else:
                        p_1 = [d for d in posibles_padres if '1K' in d['clean'] or '1L' in d['clean']]
                        if p_1: c_heredado = p_1[0]['costo']
                        else: c_heredado = posibles_padres[0]['costo']
                else: c_heredado = posibles_padres[0]['costo']
            if c_heredado > 0: c_base = c_heredado

        cf = get_val(p.costo_fab_man, p.costo_fab_ex, 0.0)
        mg = get_val(p.margen_man, p.margen_ex, 0.20)
        cy = get_val(p.coyuntural_man, p.coyuntural_ex, 0.0)
        merma_pct = p.merma_pct_man or 0.0
        
        merma = c_base * merma_pct; ct = c_base + cf + merma
        c_ref = cy if (cy > 0 and ct <= cy) else ct
        
        if c_ref <= 0.0001: mg = 0.0; pl = 0.0; pp = 0.0
        else:
            flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
            pl = c_ref * (1 + mg); pp = pl + flete
            
        visibilidad_str = "SÍ" if getattr(p, 'visible_ventas', True) else "NO (Oculto)"
            
        data.append({
            "Producto": p.nombre, "Código": p.codigo, "Empresa": p.empresa, "Categoría": p.categoria, "Origen": p.tipo_origen, "Moneda": p.moneda_texto,
            "Costo Real": c_base, "Costo Fab": cf, "Merma (%)": merma_pct*100, "Costo Total": ct,
            "Coyuntural": cy, "Margen (%)": mg*100, "Precio LIMA": pl, "Precio PROVINCIA": pp, "Visible Ventas": visibilidad_str, "Nota": p.nota
        })
    df = pd.DataFrame(data); output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0); return send_file(output, download_name='Precios_GLI.xlsx', as_attachment=True)

if __name__ == '__main__': app.run(debug=True)
