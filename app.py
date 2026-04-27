import os
import io
import re
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
    pass # tzset no está disponible en Windows, solo en Linux/Render

app = Flask(__name__, template_folder='templates')
CORS(app)

# =========================================================
# 🔒 CONFIGURACIÓN GENERAL, SEGURIDAD Y BASE DE DATOS
# =========================================================
app.config['SECRET_KEY'] = 'GLI_SECURITY_KEY_2026_SUPER_SECRET'
ADMIN_SECRET = "Gli_Admin" # Mantenido por compatibilidad temporal con el HTML antiguo

# Usa PostgreSQL en Render, o SQLite en entorno local
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///gli_database.sqlite')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# =========================================================
# 📘 TARIFAS Y EXCEPCIONES DE FLETE Y MONEDA
# =========================================================
TIPO_CAMBIO_PEN_USD = 4 
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
            return "S/", "PEN", TIPO_CAMBIO_PEN_USD
    es_usd = True
    if proveedor == "SACCO" or "SACCO" in nombre.upper():
        es_usd = False
        for exc in EXCEPCIONES_SACCO_USD:
            if re.sub(r'\s+', '', exc.upper()) in nombre_clean:
                es_usd = True
                break
    if es_usd: return "$", "USD", 1.0
    else: return "S/", "PEN", TIPO_CAMBIO_PEN_USD

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
    except: 
        return default

# =========================================================
# 📊 MODELOS DE BASE DE DATOS (SQLAlchemy)
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'SuperAdmin', 'Admin', 'Vendedor'

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
    
    # Valores extraídos del Excel
    costo_base_ex = db.Column(db.Float, default=0.0)
    costo_fab_ex = db.Column(db.Float, default=0.0)
    coyuntural_ex = db.Column(db.Float, default=0.0)
    margen_ex = db.Column(db.Float, default=0.20)
    dscto_pv_ex = db.Column(db.Float, default=0.0)
    dscto_dist_ex = db.Column(db.Float, default=0.0)
    
    # Valores sobreescritos manualmente (Overrides)
    costo_base_man = db.Column(db.Float, nullable=True)
    costo_fab_man = db.Column(db.Float, nullable=True)
    coyuntural_man = db.Column(db.Float, nullable=True)
    margen_man = db.Column(db.Float, nullable=True)
    merma_pct_man = db.Column(db.Float, default=0.0) # Merma es % aplicado al Costo Real
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
    tipo = db.Column(db.String(50), default="INFO") # ACTIVA, INFO

# Crear tablas al iniciar
with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# =========================================================
# 🔐 RUTAS DE AUTENTICACIÓN Y CONFIGURACIÓN
# =========================================================
@app.route('/setup-admin')
def setup_admin():
    # Esta ruta crea tu primer usuario automáticamente. Elimínala o protégela en el futuro.
    if not User.query.filter_by(role='SuperAdmin').first():
        u = User(email='admin@gli.com', password=generate_password_hash('admin123'), role='SuperAdmin')
        db.session.add(u)
        db.session.commit()
        return "✅ SuperAdmin creado con éxito. Correo: admin@gli.com | Clave: admin123"
    return "Ya existe un administrador en el sistema."

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.role == 'Vendedor': return redirect(url_for('vista_vendedor'))
            return redirect(url_for('vista_admin'))
        flash('Correo o contraseña incorrectos')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/')
def home():
    # Mantenemos esto por ahora para que tu viejo index.html siga funcionando
    return render_template('index.html')

@app.route('/admin')
@login_required
def vista_admin():
    if current_user.role not in ['Admin', 'SuperAdmin']: return "Acceso Denegado", 403
    return render_template('index_admin.html')

@app.route('/vendedor')
@login_required
def vista_vendedor():
    return render_template('index_vendedor.html')

# Función auxiliar para validar seguridad en las API
def is_admin_api(req):
    token = req.json.get('token') if req.is_json else req.form.get('token')
    if token == ADMIN_SECRET: return True
    if current_user.is_authenticated and current_user.role in ['Admin', 'SuperAdmin']: return True
    return False

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
    except Exception as e: 
        print("Error leyendo archivo:", e)
        return None

@app.route('/subir-maestro', methods=['POST'])
def subir_maestro():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "No se envió archivo"}), 400
    
    df = cargar_y_limpiar_excel(f)
    if df is None or len(df.columns) < 13: return jsonify({"error": "Archivo no válido o le faltan columnas"}), 400

    col_cat = df.columns[0]
    col_codigo = df.columns[1]
    col_marca = df.columns[2]
    col_nombre = df.columns[3]
    col_empresa = df.columns[4]
    col_costo_fab = df.columns[7]
    col_costo_base = df.columns[8]
    col_coyuntural = df.columns[9]  
    col_margen = df.columns[10]
    col_dscto_pv = df.columns[11]
    col_dscto_dist = df.columns[12]

    now_str = datetime.now().strftime('%d/%m %H:%M')

    for _, row in df.iterrows():
        nombre_full = str(row[col_nombre]).strip()
        if nombre_full == 'nan' or not nombre_full: continue
        
        c_base = robust_numeric(row[col_costo_base])
        c_fab = robust_numeric(row[col_costo_fab])
        if (c_base + c_fab) <= 0.0001: continue
        
        empresa = str(row[col_empresa]).strip().upper() if pd.notna(row[col_empresa]) else ''
        codigo = str(row[col_codigo]).strip() if pd.notna(row[col_codigo]) else 'S/C'
        proveedor = detectar_proveedor_exacto(nombre_full, empresa)
        ms, mt, fm = get_currency_info(nombre_full, proveedor)
        
        prod = Producto.query.filter_by(nombre=nombre_full).first()
        is_new = False
        if not prod:
            prod = Producto(nombre=nombre_full)
            db.session.add(prod)
            is_new = True
            
            alerta = Alerta(fecha=now_str, msg=f"🆕 Producto Nuevo: {nombre_full}", producto=nombre_full, tipo="INFO")
            db.session.add(alerta)

        prod.categoria = str(row[col_cat]).strip().upper() if pd.notna(row[col_cat]) else 'GENERAL'
        prod.codigo = codigo
        prod.marca = str(row[col_marca]).strip().upper() if pd.notna(row[col_marca]) else 'GENERICO'
        prod.empresa = empresa
        prod.proveedor = proveedor
        prod.kg = detectar_info_basica(nombre_full, codigo)
        prod.unidad_tipo = 'KG' if prod.kg >= 1 else 'G'
        prod.moneda_simbolo = ms
        prod.moneda_texto = mt
        prod.factor_moneda = fm
        
        prod.costo_base_ex = c_base
        prod.costo_fab_ex = c_fab
        prod.coyuntural_ex = robust_numeric(row[col_coyuntural])
        prod.margen_ex = parse_percentage(row[col_margen], 0.20)
        prod.dscto_pv_ex = parse_percentage(row[col_dscto_pv], 0.0)
        prod.dscto_dist_ex = parse_percentage(row[col_dscto_dist], 0.0)
        prod.fecha_act = datetime.utcnow()

    # Mantener máximo 30 alertas
    alertas_viejas = Alerta.query.order_by(Alerta.id.desc()).offset(30).all()
    for a in alertas_viejas: db.session.delete(a)
    
    db.session.commit()
    return jsonify({"mensaje": "✅ Excel Maestro procesado y Base de Datos actualizada"})


@app.route('/buscar')
def buscar():
    q = request.args.get('q', '').upper().strip()
    palabras = q.split() if q else []
    
    # Eliminar alertas activas previas (se recalculan al vuelo)
    Alerta.query.filter_by(tipo="ACTIVA").delete()
    db.session.commit()
    
    query = Producto.query.filter_by(oculto=False)
    productos_db = query.all()
    
    resultados = []
    for p in productos_db:
        if palabras and not all(pal in p.nombre.upper() or pal in p.codigo.upper() for pal in palabras):
            continue
            
        # Prioridad a las modificaciones manuales
        costo_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        costo_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        merma_pct = p.merma_pct_man
        coyuntural = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        if coyuntural < 0: coyuntural = 0.0
        
        dscto_pv = p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex
        dscto_dist = p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex

        # 🚀 LÓGICA DE COSTOS ESTRICTA
        merma_monto = costo_base * merma_pct
        costo_total = costo_base + costo_fab + merma_monto
        
        costo_calculo = costo_total
        if coyuntural > 0:
            if costo_total > coyuntural:
                # Alerta dinámica
                alerta = Alerta(fecha="ACTIVA", msg=f"⚠️ <b>{p.nombre}</b>: El costo total (con merma) superó al coyuntural. Se usó el Costo Total.", producto=p.nombre, tipo="ACTIVA")
                db.session.add(alerta)
            else:
                costo_calculo = coyuntural

        # 🚚 LÓGICA DE FLETES
        if p.proveedor == "CRAMER" or "CRAMER" in p.nombre.upper() or p.proveedor == "SACCO" or "SACCO" in p.nombre.upper():
            flete = 0.0
        else:
            flete = TARIFA_FLETE_DEFECTO * p.factor_moneda
            if "ACETICO" in p.nombre.upper() or "FOSFORICO" in p.nombre.upper():
                flete += (0.04 * p.factor_moneda)

        precio_lima = costo_calculo * (1 + margen)
        precio_prov = precio_lima + flete

        resultados.append({
            "nombre": p.nombre, "categoria": p.categoria, "marca": p.marca, "codigo": p.codigo,
            "empresa": p.empresa, "proveedor": p.proveedor,
            "merma_porcentaje": f"{round(merma_pct * 100, 2)}", 
            "merma_monto": round(merma_monto, 3),
            "margen": f"{round(margen * 100, 2)}", 
            "precio_lima": round(precio_lima, 2),
            "precio_provincia": round(precio_prov, 2),
            "presentacion": p.kg, "flete_status": "NO" if flete == 0 else "SI",
            "costo_oculto": costo_calculo, "flete_oculto": flete, 
            "costo_fab": round(costo_fab, 3), 
            "costo_base": round(costo_base, 3), 
            "costo_actual": round(costo_total, 3), 
            "costo_coyuntural": round(coyuntural, 3),
            "dscto_dist": f"{round(dscto_dist * 100, 2)}",
            "dscto_pv": f"{round(dscto_pv * 100, 2)}",
            "moneda_simbolo": p.moneda_simbolo, "moneda_texto": p.moneda_texto
        })

    db.session.commit()
    
    alertas_list = [{"fecha": a.fecha, "msg": a.msg, "producto": a.producto} for a in Alerta.query.order_by(Alerta.id.desc()).all()]
    
    # Sort: Quitar strings y ordenar por presentación
    resultados.sort(key=lambda x: (
        re.sub(r'\s*X?\s*\d+\.?\d*\s*(KG|G|L|LT|GALON|ML)\s*$', '', x['nombre'].upper()).strip(), -x['presentacion']
    ))

    ultima_act = Producto.query.order_by(Producto.fecha_act.desc()).first()
    fecha_act_str = ultima_act.fecha_act.strftime('%d/%m/%Y %H:%M') if ultima_act else "Sin datos"

    return jsonify({
        "productos": resultados,
        "alertas": alertas_list,
        "ultima_actualizacion": fecha_act_str
    })


# =========================================================
# 🛠️ RUTAS DE EDICIÓN Y CREACIÓN MANUAL
# =========================================================

@app.route('/api/crear-producto', methods=['POST'])
def crear_producto():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    
    nombre_nuevo = str(d['nombre_nuevo']).strip().upper()
    margen = float(d['margen']) / 100.0
    tipo = d.get('tipo', 'clon')
    cod = str(d.get('codigo', 'S/C')).strip() or 'S/C'
    
    try: d_dist = float(d.get('dscto_dist', 0)) / 100.0
    except: d_dist = 0.0
    
    try: d_pv = float(d.get('dscto_pv', 0)) / 100.0
    except: d_pv = 0.0

    try: merma = float(d.get('merma', 0)) / 100.0
    except: merma = 0.0
    
    prod = Producto.query.filter_by(nombre=nombre_nuevo).first()
    if not prod:
        prod = Producto(nombre=nombre_nuevo, es_manual=True)
        db.session.add(prod)
    
    if tipo == 'clon':
        base_prod = Producto.query.filter_by(nombre=d['base']).first()
        if base_prod:
            prod.costo_base_man = base_prod.costo_base_man if base_prod.costo_base_man is not None else base_prod.costo_base_ex
            prod.costo_fab_man = base_prod.costo_fab_man if base_prod.costo_fab_man is not None else base_prod.costo_fab_ex
            prod.empresa = base_prod.empresa
            prod.proveedor = base_prod.proveedor
            prod.moneda_simbolo = base_prod.moneda_simbolo
            prod.moneda_texto = base_prod.moneda_texto
            prod.factor_moneda = base_prod.factor_moneda
    else:
        prod.costo_base_man = float(d.get('costo', 0))
        prod.costo_fab_man = float(d.get('costo_fab', 0))
        prod.empresa = "MANUAL"
        ms, mt, fm = get_currency_info(nombre_nuevo, "MANUAL")
        prod.moneda_simbolo, prod.moneda_texto, prod.factor_moneda = ms, mt, fm

    prod.codigo = cod
    prod.margen_man = margen
    prod.merma_pct_man = merma
    prod.dscto_dist_man = d_dist
    prod.dscto_pv_man = d_pv
    prod.kg = detectar_info_basica(nombre_nuevo, cod)
    prod.oculto = False

    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/eliminar-producto', methods=['POST'])
def eliminar_producto():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    prod = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if prod:
        if prod.es_manual:
            db.session.delete(prod)
        else:
            prod.oculto = True
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-margen', methods=['POST'])
def editar_margen():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        prod.margen_man = float(d['margen']) / 100.0
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-merma', methods=['POST'])
def editar_merma():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        # Se guarda internamente como porcentaje (decimal)
        prod.merma_pct_man = float(d['merma']) / 100.0
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-real', methods=['POST'])
def editar_costo_real():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        val = float(d['costo'])
        prod.costo_base_man = val if val > 0 else None
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-fab', methods=['POST'])
def editar_costo_fab():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        val = float(d['costo'])
        prod.costo_fab_man = val if val >= 0 else None
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-coyuntural', methods=['POST'])
def editar_costo_coyuntural():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        val = float(d['costo'])
        prod.coyuntural_man = val if val > 0 else -1.0
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-dscto-dist', methods=['POST'])
def editar_dscto_dist():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        val_str = str(d.get('valor', '')).strip()
        if not val_str: prod.dscto_dist_man = None
        else: prod.dscto_dist_man = float(val_str) / 100.0
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-dscto', methods=['POST'])
def editar_dscto():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    d = request.json
    prod = Producto.query.filter_by(nombre=d['nombre']).first()
    if prod:
        val_str = str(d.get('valor', '')).strip()
        if not val_str: prod.dscto_pv_man = None
        else: prod.dscto_pv_man = float(val_str) / 100.0
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/verify-admin', methods=['POST'])
def verify_admin():
    d = request.json
    if d and d.get('token') == ADMIN_SECRET:
        return jsonify({"success": True})
    return jsonify({"error": "No autorizado"}), 403

# =========================================================
# 📥 EXPORTAR A EXCEL
# =========================================================
@app.route('/api/exportar', methods=['POST'])
def exportar_excel():
    if not is_admin_api(request): return jsonify({"error": "No autorizado"}), 403
    
    seleccionados = request.json.get('productos', [])
    if not seleccionados: return jsonify({"error": "No hay datos seleccionados"}), 400

    productos_db = Producto.query.filter(Producto.nombre.in_(seleccionados)).all()
    
    lista_exportar = []
    for p in productos_db:
        costo_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        costo_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        merma_pct = p.merma_pct_man
        coyuntural = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        if coyuntural < 0: coyuntural = 0.0
        dscto_pv = p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex
        dscto_dist = p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex

        merma_monto = costo_base * merma_pct
        costo_total = costo_base + costo_fab + merma_monto
        
        costo_calculo = costo_total
        if coyuntural > 0 and costo_total > coyuntural:
            costo_calculo = coyuntural
            
        if p.proveedor == "CRAMER" or "CRAMER" in p.nombre.upper() or p.proveedor == "SACCO" or "SACCO" in p.nombre.upper():
            flete = 0.0
        else:
            flete = TARIFA_FLETE_DEFECTO * p.factor_moneda
            if "ACETICO" in p.nombre.upper() or "FOSFORICO" in p.nombre.upper():
                flete += (0.04 * p.factor_moneda)

        precio_lima = costo_calculo * (1 + margen)
        precio_prov = precio_lima + flete

        lista_exportar.append({
            "Código": p.codigo,
            "Producto": p.nombre,
            "Empresa": p.empresa,
            "Categoría": p.categoria,
            "Presentación": f"{p.kg} {p.unidad_tipo}",
            "Moneda": p.moneda_texto,
            "Costo Real": round(costo_base, 3),
            "Costo de Fabricación": round(costo_fab, 3),
            "Merma (%)": f"{round(merma_pct*100, 2)}%",
            "Merma (Monto)": round(merma_monto, 3),
            "Costo Total": round(costo_total, 3),
            "Costo Coyuntural": round(coyuntural, 3),
            "Margen (%)": f"{round(margen*100, 2)}%",
            "Dscto Dist. Excl. (%)": f"{round(dscto_dist*100, 2)}%",
            "Dscto PV (%)": f"{round(dscto_pv*100, 2)}%",
            "Precio LIMA": round(precio_lima, 2),
            "Precio PROVINCIA": round(precio_prov, 2)
        })
        
    df = pd.DataFrame(lista_exportar)
    try:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Precios GLI')
        output.seek(0)
        
        fecha_str = datetime.now().strftime('%Y%m%d_%H%M')
        nombre_archivo = f"Precios_GLI_{fecha_str}.xlsx"
        
        return send_file(
            output, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=nombre_archivo, 
            as_attachment=True
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)