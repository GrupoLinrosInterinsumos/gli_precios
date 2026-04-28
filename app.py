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

# =========================================================
# ⏱️ AJUSTE DE ZONA HORARIA (PERÚ)
# =========================================================
os.environ['TZ'] = 'America/Lima'
try:
    time.tzset()
except AttributeError:
    pass 

app = Flask(__name__, template_folder='templates')
CORS(app)

# =========================================================
# 🔒 CONFIGURACIÓN GENERAL, SEGURIDAD Y BASE DE DATOS
# =========================================================
app.config['SECRET_KEY'] = 'GLI_SECURITY_KEY_2026_SUPER_SECRET'
ADMIN_SECRET = "Gli_Admin" 

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///gli_database.sqlite')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
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
    role = db.Column(db.String(20), nullable=False) # 'SuperAdmin', 'Admin', 'Vendedor', 'TC'

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True)
    valor = db.Column(db.Float, default=3.80)

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(250), unique=True, nullable=False)
    codigo = db.Column(db.String(100), default='S/C')
    categoria = db.Column(db.String(100), default='GENERAL')
    marca = db.Column(db.String(100), default='GENERICO')
    empresa = db.Column(db.String(100), default='')
    proveedor = db.Column(db.String(100), default='')
    kg = db.Column(db.Float, default=1.0)
    unidad_tipo = db.Column(db.String(20), default='KG')
    moneda_simbolo = db.Column(db.String(10), default='$')
    moneda_texto = db.Column(db.String(10), default='USD')
    factor_moneda = db.Column(db.Float, default=1.0)
    
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
    fecha_act = db.Column(db.DateTime, default=datetime.utcnow)

class Alerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.String(50))
    msg = db.Column(db.String(250))
    producto = db.Column(db.String(250))
    tipo = db.Column(db.String(50), default="INFO")

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# =========================================================
# 📘 TARIFAS Y EXCEPCIONES DE FLETE Y MONEDA
# =========================================================
def get_tc_actual():
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    return conf.valor if conf else 3.80

TARIFA_FLETE_DEFECTO = 0.11

EXCEPCIONES_PEN = [
    "COLAGENO HIDROLIZADO GELNEX X 1KG",
    "COLAGENO HIDROLIZADO GELNEX X 400G"
]

EXCEPCIONES_SACCO_USD = [
    "LYOTO M 536 R P/50LTS SACCO",
    "LYOTO M 536 S P/50LTS SACCO",
    "LYOFAST AB 1 DOSIS 30 SACCO",
    "LYOFAST Y 438 A 50UC SACCO",
    "LYOFAST Y 470 E 40UC SACCO",
    "MIX PROFUXION 100 BLN SACCO X 20KG"
]

DICCIONARIO_PROVEEDORES = {
    "CREMA CHIRIMOYA 850019 CRAMER X 4KG": "CRAMER",
    "CREMA CHOCOLATE SUIZO 1528519 CRAMER X 5KG": "CRAMER",
    "LYOTO M 536 R P/50LTS SACCO": "SACCO",
    "LYOFAST AB 1 DOSIS 30 SACCO": "SACCO"
}

def detectar_proveedor_exacto(nombre_odoo, empresa_col=""):
    if "CRAMER" in str(nombre_odoo).upper(): return "CRAMER"
    if "SACCO" in str(nombre_odoo).upper(): return "SACCO"
    return DICCIONARIO_PROVEEDORES.get(" ".join(str(nombre_odoo).upper().strip().split()), str(empresa_col).strip().upper())

def get_currency_info(nombre, proveedor):
    nombre_clean = re.sub(r'\s+', '', nombre.upper())
    for exc_pen in EXCEPCIONES_PEN:
        if re.sub(r'\s+', '', exc_pen.upper()) in nombre_clean:
            return "S/", "PEN", get_tc_actual()
    es_usd = True
    if proveedor == "SACCO" or "SACCO" in nombre.upper():
        es_usd = False
        for exc in EXCEPCIONES_SACCO_USD:
            if re.sub(r'\s+', '', exc.upper()) in nombre_clean:
                es_usd = True
                break
    if es_usd: return "$", "USD", 1.0
    else: return "S/", "PEN", get_tc_actual()

def detectar_info_basica(nombre, codigo=""):
    nombre = str(nombre).upper()
    codigo = str(codigo).upper().strip()
    match = re.search(r'X?\s*(\d+\.?\d*)\s*(KG|G|L|LT|ML)', nombre)
    if match: 
        kg = float(match.group(1))
        if match.group(2) in ['G', 'ML']: kg = kg / 1000.0
    else:
        match_cod = re.search(r'-(\d{3})$', codigo)
        if match_cod: kg = float(match_cod.group(1))
        else:
            if '1LT' in nombre or '1 LT' in nombre: kg = 1.0
            elif 'GALON' in nombre: kg = 3.785
            elif '250ML' in nombre: kg = 0.25
            else: kg = 1.0 
    return kg

def robust_numeric(val):
    if pd.isna(val): return 0.0
    s = str(val).strip().replace('$', '').replace('S/', '').replace('%', '')
    if ',' in s and '.' in s: s = s.replace(',', '')
    elif ',' in s: s = s.replace(',', '.')
    try: return float(s)
    except: return 0.0

def parse_percentage(val, default=0.0):
    if pd.isna(val): return default
    s = str(val).strip()
    if s.lower() == 'nan' or s == '': return default
    has_percent = '%' in s
    s = s.replace('%', '').replace(',', '.')
    try:
        v = float(s)
        if has_percent: return v / 100.0
        if v >= 10: return v / 100.0
        return v
    except: return default

# =========================================================
# 🔐 RUTAS DE AUTENTICACIÓN Y CONFIGURACIÓN
# =========================================================
@app.route('/setup-admin')
def setup_admin():
    if not User.query.filter_by(role='SuperAdmin').first():
        db.session.add(User(email='admin@gli.com', password=generate_password_hash('admin123'), role='SuperAdmin'))
    if not Config.query.filter_by(clave='tipo_cambio').first():
        db.session.add(Config(clave='tipo_cambio', valor=3.80))
    db.session.commit()
    return "✅ OK"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            if user.role == 'Vendedor': return redirect(url_for('vista_vendedor'))
            return redirect(url_for('vista_admin'))
        flash('Correo o contraseña incorrectos')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
def home():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    if current_user.role == 'Vendedor': return redirect(url_for('vista_vendedor'))
    return redirect(url_for('vista_admin'))

@app.route('/admin')
@login_required
def vista_admin():
    if current_user.role not in ['Admin', 'SuperAdmin', 'TC']: return "Acceso Denegado", 403
    return render_template('index_admin.html')

@app.route('/vendedor')
@login_required
def vista_vendedor():
    return render_template('index_vendedor.html')

def is_admin_api(req):
    token = req.json.get('token') if req.is_json else req.form.get('token')
    if token == ADMIN_SECRET: return True
    if current_user.is_authenticated and current_user.role in ['Admin', 'SuperAdmin', 'TC']: return True
    return False

# =========================================================
# 🚀 RUTAS DE PERSONAL Y TC
# =========================================================
@app.route('/usuarios')
@login_required
def gestion_usuarios():
    if current_user.role != 'SuperAdmin': return redirect(url_for('home'))
    return render_template('superadmin.html', usuarios=User.query.all())

@app.route('/api/crear-usuario', methods=['POST'])
@login_required
def crear_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No autorizado"}), 403
    d = request.json
    if User.query.filter_by(email=d['email']).first(): return jsonify({"error": "El usuario ya existe"}), 400
    db.session.add(User(email=d['email'], password=generate_password_hash(d['password']), role=d['role']))
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No autorizado"}), 403
    d = request.json
    u = User.query.get(d['id'])
    if u:
        u.role = d['role']
        if d.get('password') and d['password'].strip() != "":
            u.password = generate_password_hash(d['password'])
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/eliminar-usuario/<int:id>', methods=['POST'])
@login_required
def eliminar_usuario(id):
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No autorizado"}), 403
    u = User.query.get(id)
    if u and u.id != current_user.id:
        db.session.delete(u); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/update-tc', methods=['POST'])
@login_required
def update_tc():
    if current_user.role not in ['TC', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    conf.valor = float(request.json['tc'])
    db.session.commit()
    return jsonify({"success": True, "tc": conf.valor})

# =========================================================
# 🚀 MOTOR PRINCIPAL: LECTURA, MATEMÁTICA Y BD
# =========================================================
def cargar_y_limpiar_excel(filepath_or_stream):
    try:
        df_temp = pd.read_excel(filepath_or_stream, header=None)
        header_row_idx = 0
        for idx, row in df_temp.iterrows():
            row_str = ' '.join(str(x).lower() for x in row.values if pd.notna(x))
            if 'nombre' in row_str and ('costo' in row_str or 'margen' in row_str):
                header_row_idx = idx
                break
        filepath_or_stream.seek(0)
        df = pd.read_excel(filepath_or_stream, header=header_row_idx)
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df
    except Exception as e: return None

@app.route('/subir-maestro', methods=['POST'])
def subir_maestro():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "No se envió archivo"}), 400
    df = cargar_y_limpiar_excel(f)
    if df is None or len(df.columns) < 13: return jsonify({"error": "Archivo no válido"}), 400

    col_cat, col_codigo, col_marca, col_nombre, col_empresa = df.columns[0], df.columns[1], df.columns[2], df.columns[3], df.columns[4]
    col_costo_fab, col_costo_base, col_coyuntural, col_margen = df.columns[7], df.columns[8], df.columns[9], df.columns[10]
    col_dscto_pv, col_dscto_dist = df.columns[11], df.columns[12]

    now_str = datetime.now().strftime('%d/%m %H:%M')

    for _, row in df.iterrows():
        nombre_full = str(row[col_nombre]).strip()
        if nombre_full == 'nan' or not nombre_full: continue
        
        c_base, c_fab = robust_numeric(row[col_costo_base]), robust_numeric(row[col_costo_fab])
        if (c_base + c_fab) <= 0.0001: continue
        
        empresa = str(row[col_empresa]).strip().upper() if pd.notna(row[col_empresa]) else ''
        codigo = str(row[col_codigo]).strip() if pd.notna(row[col_codigo]) else 'S/C'
        proveedor = detectar_proveedor_exacto(nombre_full, empresa)
        ms, mt, fm = get_currency_info(nombre_full, proveedor)
        
        prod = Producto.query.filter_by(nombre=nombre_full).first()
        if not prod:
            prod = Producto(nombre=nombre_full)
            db.session.add(prod)
            db.session.add(Alerta(fecha=now_str, msg=f"🆕 Producto Nuevo: {nombre_full}", producto=nombre_full, tipo="INFO"))

        prod.categoria = str(row[col_cat]).strip().upper() if pd.notna(row[col_cat]) else 'GENERAL'
        prod.codigo = codigo
        prod.marca = str(row[col_marca]).strip().upper() if pd.notna(row[col_marca]) else 'GENERICO'
        prod.empresa = empresa
        prod.proveedor = proveedor
        prod.kg = detectar_info_basica(nombre_full, codigo)
        prod.unidad_tipo = 'KG' if prod.kg >= 1 else 'G'
        prod.moneda_simbolo, prod.moneda_texto, prod.factor_moneda = ms, mt, fm
        
        prod.costo_base_ex, prod.costo_fab_ex = c_base, c_fab
        prod.coyuntural_ex = robust_numeric(row[col_coyuntural])
        prod.margen_ex = parse_percentage(row[col_margen], 0.20)
        prod.dscto_pv_ex, prod.dscto_dist_ex = parse_percentage(row[col_dscto_pv], 0.0), parse_percentage(row[col_dscto_dist], 0.0)
        prod.fecha_act = datetime.utcnow()

    alertas_viejas = Alerta.query.order_by(Alerta.id.desc()).offset(30).all()
    for a in alertas_viejas: db.session.delete(a)
    db.session.commit()
    return jsonify({"mensaje": "✅ Excel procesado"})

@app.route('/buscar')
def buscar():
    q = request.args.get('q', '').upper().strip()
    palabras = q.split() if q else []
    Alerta.query.filter_by(tipo="ACTIVA").delete()
    db.session.commit()
    
    tc_actual = get_tc_actual()
    productos_db = Producto.query.filter_by(oculto=False).all()
    resultados = []
    
    for p in productos_db:
        if palabras and not all(pal in p.nombre.upper() or pal in p.codigo.upper() for pal in palabras): continue
            
        costo_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        costo_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        coyuntural = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        if coyuntural < 0: coyuntural = 0.0
        
        merma_monto = costo_base * p.merma_pct_man
        costo_total = costo_base + costo_fab + merma_monto
        
        costo_calculo = costo_total
        if coyuntural > 0:
            if costo_total > coyuntural:
                db.session.add(Alerta(fecha="ACTIVA", msg=f"⚠️ <b>{p.nombre}</b> superó coyuntural.", producto=p.nombre, tipo="ACTIVA"))
            else: costo_calculo = coyuntural

        flete = 0.0
        if p.proveedor not in ["CRAMER", "SACCO"] and "CRAMER" not in p.nombre.upper() and "SACCO" not in p.nombre.upper():
            flete = TARIFA_FLETE_DEFECTO * (tc_actual if p.moneda_texto == 'USD' else 1.0)
            if "ACETICO" in p.nombre.upper() or "FOSFORICO" in p.nombre.upper():
                flete += (0.04 * (tc_actual if p.moneda_texto == 'USD' else 1.0))

        precio_lima = costo_calculo * (1 + margen)
        precio_prov = precio_lima + flete

        resultados.append({
            "nombre": p.nombre, "codigo": p.codigo, "empresa": p.empresa, "proveedor": p.proveedor,
            "merma_porcentaje": round(p.merma_pct_man * 100, 2), "merma_monto": round(merma_monto, 3),
            "margen": round(margen * 100, 2), "precio_lima": precio_lima, "precio_provincia": precio_prov,
            "presentacion": p.kg, "costo_fab": costo_fab, "costo_base": costo_base, "costo_actual": costo_total, 
            "costo_coyuntural": coyuntural, "dscto_dist": round((p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex) * 100, 2),
            "dscto_pv": round((p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex) * 100, 2),
            "moneda_simbolo": p.moneda_simbolo, "moneda_texto": p.moneda_texto
        })

    db.session.commit()
    resultados.sort(key=lambda x: (re.sub(r'\s*X?\s*\d+\.?\d*\s*(KG|G|L|LT|GALON|ML)\s*$', '', x['nombre'].upper()).strip(), -x['presentacion']))
    
    ultima_act = Producto.query.order_by(Producto.fecha_act.desc()).first()
    return jsonify({"productos": resultados, "ultima_actualizacion": ultima_act.fecha_act.strftime('%d/%m/%Y %H:%M') if ultima_act else "Sin datos", "tc_actual": tc_actual})

# APIS DE EDICIÓN MANUAL
@app.route('/api/editar-margen', methods=['POST'])
def editar_margen():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.margen_man = float(request.json['margen']) / 100.0; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-merma', methods=['POST'])
def editar_merma():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.merma_pct_man = float(request.json['merma']) / 100.0; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-real', methods=['POST'])
def editar_costo_real():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.costo_base_man = float(request.json['costo']) if float(request.json['costo']) > 0 else None; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-fab', methods=['POST'])
def editar_costo_fab():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.costo_fab_man = float(request.json['costo']) if float(request.json['costo']) >= 0 else None; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-coyuntural', methods=['POST'])
def editar_costo_coyuntural():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.coyuntural_man = float(request.json['costo']) if float(request.json['costo']) > 0 else -1.0; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-dscto-dist', methods=['POST'])
def editar_dscto_dist():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.dscto_dist_man = float(request.json['valor']) / 100.0 if str(request.json.get('valor','')).strip() else None; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-dscto', methods=['POST'])
def editar_dscto():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.dscto_pv_man = float(request.json['valor']) / 100.0 if str(request.json.get('valor','')).strip() else None; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/eliminar-producto', methods=['POST'])
def eliminar_producto():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    prod = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if prod:
        if prod.es_manual: db.session.delete(prod)
        else: prod.oculto = True
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/exportar', methods=['POST'])
def exportar_excel():
    if not is_admin_api(request): return jsonify({"error": "No"}), 403
    # Lógica de exportación igual
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
