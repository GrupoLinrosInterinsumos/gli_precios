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

os.environ['TZ'] = 'America/Lima'
try: time.tzset()
except AttributeError: pass 

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = 'GLI_SECURITY_MASTER_2026_FINAL'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///gli_database.sqlite')
if app.config['SQLALCHEMY_DATABASE_URI'] and app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# =========================================================
# 📊 MODELOS DE DATOS
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
    fecha_act = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context(): db.create_all()

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# =========================================================
# 🛠️ LÓGICA DE TC Y FLETES
# =========================================================
def get_tc_actual():
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    if not conf:
        conf = Config(clave='tipo_cambio', valor=3.80)
        db.session.add(conf); db.session.commit()
    return conf.valor

TARIFA_FLETE_DEFECTO = 0.08

def detectar_proveedor_exacto(nombre_odoo, empresa_col=""):
    if "CRAMER" in str(nombre_odoo).upper(): return "CRAMER"
    if "SACCO" in str(nombre_odoo).upper(): return "SACCO"
    return str(empresa_col).strip().upper()

def get_currency_info(nombre, proveedor):
    n_clean = re.sub(r'\s+', '', nombre.upper())
    if "COLAGENOHIDROLIZADOGELNEX" in n_clean: return "S/", "PEN"
    es_usd = True
    if proveedor == "SACCO" or "SACCO" in nombre.upper():
        es_usd = False
        for exc in ["LYOTO", "LYOFASTAB", "LYOFASTY", "MIXPROFUXION"]:
            if exc in n_clean: es_usd = True; break
    return ("$", "USD") if es_usd else ("S/", "PEN")

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
        return v / 100.0 if has_percent or v >= 10 else v
    except: return default

# =========================================================
# 🔐 RUTAS Y SEGURIDAD
# =========================================================
@app.route('/setup-admin')
def setup_admin():
    if not User.query.filter_by(role='SuperAdmin').first():
        db.session.add(User(email='admin@gli.com', password=generate_password_hash('admin123'), role='SuperAdmin'))
    get_tc_actual() # Fuerza la creación del TC
    db.session.commit()
    return "✅ OK"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('home'))
        flash('Credenciales incorrectas')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/')
def home():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    return redirect(url_for('vista_vendedor')) if current_user.role == 'Vendedor' else redirect(url_for('vista_admin'))

@app.route('/admin')
@login_required
def vista_admin():
    if current_user.role not in ['Admin', 'SuperAdmin', 'TC']: return redirect(url_for('home'))
    return render_template('index_admin.html')

@app.route('/vendedor')
@login_required
def vista_vendedor(): return render_template('index_vendedor.html')

# =========================================================
# ⚙️ GESTIÓN DE USUARIOS Y TC
# =========================================================
@app.route('/usuarios')
@login_required
def gestion_usuarios():
    if current_user.role != 'SuperAdmin': return redirect(url_for('home'))
    return render_template('superadmin.html', usuarios=User.query.all())

@app.route('/api/crear-usuario', methods=['POST'])
@login_required
def crear_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    d = request.json
    if User.query.filter_by(email=d['email']).first(): return jsonify({"error": "Existe"}), 400
    db.session.add(User(email=d['email'], password=generate_password_hash(d['password']), role=d['role']))
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
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
    if current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    u = User.query.get(id)
    if u and u.id != current_user.id:
        db.session.delete(u); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/update-tc', methods=['POST'])
@login_required
def update_tc():
    if current_user.role not in ['TC', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    try:
        conf = Config.query.filter_by(clave='tipo_cambio').first()
        if not conf:
            conf = Config(clave='tipo_cambio', valor=3.80)
            db.session.add(conf)
        conf.valor = float(request.json['tc'])
        db.session.commit()
        return jsonify({"success": True, "tc": conf.valor})
    except Exception as e: return jsonify({"error": str(e)}), 400

# =========================================================
# 🚀 DATOS: EXCEL, CREAR, EXPORTAR
# =========================================================
@app.route('/subir-maestro', methods=['POST'])
@login_required
def subir_maestro():
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    f = request.files.get('archivo')
    if not f: return jsonify({"error": "Sin archivo"}), 400
    try: df = pd.read_excel(f, header=None)
    except: return jsonify({"error": "Error al leer"}), 400
    
    header_idx = 0
    for idx, row in df.iterrows():
        rs = ' '.join(str(x).lower() for x in row.values if pd.notna(x))
        if 'nombre' in rs and 'costo' in rs: header_idx = idx; break
    f.seek(0)
    df = pd.read_excel(f, header=header_idx)
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    for _, row in df.iterrows():
        nombre = str(row.get('nombre', '')).strip().upper()
        if not nombre or nombre == 'NAN': continue
        
        c_base = robust_numeric(row.get('costo base', 0))
        c_fab = robust_numeric(row.get('costo de fabricación', 0))
        if c_base + c_fab <= 0.0001: continue
        
        emp = str(row.get('empresa', '')).strip().upper()
        p = Producto.query.filter_by(nombre=nombre).first()
        if not p:
            p = Producto(nombre=nombre); db.session.add(p)
            
        p.codigo = str(row.get('código', 'S/C'))
        p.empresa = emp
        p.proveedor = detectar_proveedor_exacto(nombre, emp)
        p.moneda_simbolo, p.moneda_texto = get_currency_info(nombre, p.proveedor)
        
        p.costo_base_ex = c_base
        p.costo_fab_ex = c_fab
        p.coyuntural_ex = robust_numeric(row.get('costo coyuntural', 0))
        p.margen_ex = parse_percentage(row.get('margen'), 0.20)
        p.dscto_pv_ex = parse_percentage(row.get('dscto pv'), 0.0)
        p.dscto_dist_ex = parse_percentage(row.get('dscto dist'), 0.0)
        p.fecha_act = datetime.utcnow()

    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/crear-producto', methods=['POST'])
@login_required
def crear_producto():
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    d = request.json
    nombre = d['nombre'].upper().strip()
    if Producto.query.filter_by(nombre=nombre).first(): return jsonify({"error": "Ya existe"}), 400
    
    p = Producto(nombre=nombre, codigo=d.get('codigo', 'S/C').upper(), empresa=d.get('empresa', '').upper(), es_manual=True)
    p.proveedor = detectar_proveedor_exacto(nombre, p.empresa)
    p.moneda_simbolo, p.moneda_texto = get_currency_info(nombre, p.proveedor)
    p.costo_base_man = float(d.get('costo_base', 0))
    p.costo_fab_man = float(d.get('costo_fab', 0))
    p.margen_man = float(d.get('margen', 20)) / 100.0
    
    db.session.add(p); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/exportar', methods=['POST'])
@login_required
def exportar_excel():
    nombres = request.json.get('productos', [])
    if not nombres: return jsonify({"error": "Vacío"}), 400
    prods = Producto.query.filter(Producto.nombre.in_(nombres)).all()
    data = []
    tc = get_tc_actual()
    for p in prods:
        cb = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        cf = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        mg = p.margen_man if p.margen_man is not None else p.margen_ex
        cy = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        merma = cb * p.merma_pct_man
        ct = cb + cf + merma
        cr = cy if (cy > 0 and ct <= cy) else ct
        
        flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
        pl = cr * (1 + mg)
        pp = pl + flete
        data.append({
            "Producto": p.nombre, "Código": p.codigo, "Empresa": p.empresa, "Moneda": p.moneda_texto,
            "Costo Real": cb, "Costo Fab": cf, "Merma (%)": p.merma_pct_man*100, "Costo Total": ct,
            "Coyuntural": cy, "Margen (%)": mg*100, "Precio LIMA": pl, "Precio PROVINCIA": pp
        })
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name='Precios_GLI.xlsx', as_attachment=True)

@app.route('/api/eliminar-producto', methods=['POST'])
@login_required
def eliminar_producto():
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p:
        if p.es_manual: db.session.delete(p)
        else: p.oculto = True
        db.session.commit()
    return jsonify({"success": True})

# --- EDICIÓN EN TABLA ---
@app.route('/api/editar-<tipo>', methods=['POST'])
@login_required
def editar_celdas(tipo):
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if not p: return jsonify({"error": "No existe"}), 404
    val = float(request.json.get('valor', request.json.get('costo', request.json.get('merma', request.json.get('margen', 0)))))
    if tipo == 'margen': p.margen_man = val / 100.0
    elif tipo == 'merma': p.merma_pct_man = val / 100.0
    elif tipo == 'costo-real': p.costo_base_man = val if val > 0 else None
    elif tipo == 'costo-fab': p.costo_fab_man = val if val >= 0 else None
    elif tipo == 'costo-coyuntural': p.coyuntural_man = val if val > 0 else -1.0
    elif tipo == 'dscto': p.dscto_pv_man = val / 100.0
    elif tipo == 'dscto-dist': p.dscto_dist_man = val / 100.0
    db.session.commit()
    return jsonify({"success": True})

@app.route('/buscar')
@login_required
def buscar():
    q = request.args.get('q', '').upper()
    tc = get_tc_actual()
    prods = Producto.query.filter(Producto.oculto == False).all()
    res = []
    
    for p in prods:
        if q and q not in p.nombre.upper() and q not in str(p.codigo).upper(): continue
        c_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        c_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        coyun = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        if coyun < 0: coyun = 0.0
        
        merma = c_base * p.merma_pct_man
        c_total = c_base + c_fab + merma
        c_ref = coyun if (coyun > 0 and c_total <= coyun) else c_total
        flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
        p_lima = c_ref * (1 + margen)
        
        res.append({
            "nombre": p.nombre, "codigo": p.codigo, "empresa": p.empresa, "costo_base": c_base, "costo_fab": c_fab,
            "merma_porcentaje": round(p.merma_pct_man * 100, 2), "merma_monto": merma, "costo_actual": c_total,
            "costo_coyuntural": coyun, "margen": round(margen * 100, 2), "precio_lima": p_lima, "precio_provincia": p_lima + flete,
            "moneda_simbolo": p.moneda_simbolo, "moneda_texto": p.moneda_texto, 
            "dscto_pv": round((p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex)*100, 2),
            "dscto_dist": round((p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex)*100, 2)
        })
    res.sort(key=lambda x: x['nombre'])
    return jsonify({"productos": res, "tc_actual": tc})

if __name__ == '__main__': app.run(debug=True)
