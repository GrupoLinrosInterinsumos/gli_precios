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
try:
    time.tzset()
except AttributeError:
    pass 

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
    kg = db.Column(db.Float, default=1.0)
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
    
    oculto = db.Column(db.Boolean, default=False)
    fecha_act = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# =========================================================
# 🛠️ LÓGICA DE NEGOCIO Y TC
# =========================================================
def get_tc_actual():
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    return conf.valor if conf else 3.80

FLETE_ESTANDAR = 0.11

def get_currency_logic(nombre, proveedor):
    nombre_u = nombre.upper()
    if "COLAGENO" in nombre_u and "GELNEX" in nombre_u: return "S/", "PEN", get_tc_actual()
    if "SACCO" in proveedor or "SACCO" in nombre_u:
        for exc in ["LYOFAST AB", "LYOFAST Y", "MIX PROFUXION"]:
            if exc in nombre_u: return "$", "USD", 1.0
        return "S/", "PEN", get_tc_actual()
    return "$", "USD", 1.0

def calcular_kg(nombre):
    match = re.search(r'(\d+\.?\d*)\s*(KG|G|L|ML)', nombre.upper())
    if match:
        val = float(match.group(1))
        return val / 1000.0 if match.group(2) in ['G', 'ML'] else val
    return 1.0

# =========================================================
# 🔐 RUTAS DE ACCESO Y SEGURIDAD
# =========================================================
@app.route('/')
def home():
    if not current_user.is_authenticated: return redirect(url_for('login'))
    if current_user.role == 'Vendedor': return redirect(url_for('vista_vendedor'))
    return redirect(url_for('vista_admin'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('home'))
        flash('Correo o contraseña incorrectos')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/setup-admin')
def setup_admin():
    db.create_all()
    if not User.query.filter_by(role='SuperAdmin').first():
        db.session.add(User(email='admin@gli.com', password=generate_password_hash('admin123'), role='SuperAdmin'))
    if not Config.query.filter_by(clave='tipo_cambio').first():
        db.session.add(Config(clave='tipo_cambio', valor=3.80))
    db.session.commit()
    return "✅ Sistema inicializado."

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

@app.route('/api/get-tc')
@login_required
def get_tc():
    return jsonify({"tc": get_tc_actual()})

@app.route('/api/update-tc', methods=['POST'])
@login_required
def update_tc():
    if current_user.role != 'TC' and current_user.role != 'SuperAdmin': return jsonify({"error": "No"}), 403
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    conf.valor = float(request.json['tc'])
    db.session.commit()
    return jsonify({"success": True, "tc": conf.valor})

# =========================================================
# 🚀 VISTAS Y BÚSQUEDA
# =========================================================
@app.route('/admin')
@login_required
def vista_admin():
    if current_user.role == 'Vendedor': return redirect(url_for('vista_vendedor'))
    return render_template('index_admin.html')

@app.route('/buscar')
@login_required
def buscar():
    q = request.args.get('q', '').upper()
    prods = Producto.query.filter(Producto.oculto == False).all()
    tc_actual = get_tc_actual()
    res = []
    
    for p in prods:
        if q and q not in p.nombre.upper() and q not in str(p.codigo).upper(): continue
        
        c_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        c_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        coyun = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        
        merma_monto = c_base * p.merma_pct_man
        c_total = c_base + c_fab + merma_monto
        c_ref = coyun if (coyun > 0 and c_total <= coyun) else c_total
        
        flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc_actual if p.moneda_texto == 'USD' else 1.0))
        p_lima = c_ref * (1 + margen)
        p_prov = p_lima + flete
        
        res.append({
            "nombre": p.nombre, "codigo": p.codigo, "empresa": p.empresa,
            "costo_base": c_base, "costo_fab": c_fab,
            "merma_porcentaje": round(p.merma_pct_man * 100, 2), "merma_monto": merma_monto,
            "costo_actual": c_total, "costo_coyuntural": coyun,
            "margen": round(margen * 100, 2), "precio_lima": p_lima,
            "precio_provincia": p_prov, "moneda_simbolo": p.moneda_simbolo,
            "moneda_texto": p.moneda_texto, 
            "dscto_pv": round((p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex)*100, 2),
            "dscto_dist": round((p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex)*100, 2)
        })
    
    res.sort(key=lambda x: x['nombre'])
    return jsonify({"productos": res, "ultima_actualizacion": datetime.now().strftime("%d/%m/%Y"), "tc_actual": tc_actual})

# APIS de edición de celdas (Costo Real, Fab, Margen, etc) - Mantén tus funciones apiCall aquí
@app.route('/api/editar-margen', methods=['POST'])
@login_required
def editar_margen():
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.margen_man = float(request.json['margen']) / 100.0; db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-real', methods=['POST'])
@login_required
def editar_costo_real():
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.costo_base_man = float(request.json['costo']); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-fab', methods=['POST'])
@login_required
def editar_costo_fab():
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: 
        val = float(request.json['costo'])
        p.costo_fab_man = val if val >= 0 else None
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-costo-coyuntural', methods=['POST'])
@login_required
def editar_costo_coyuntural():
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.coyuntural_man = float(request.json['costo']); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/editar-merma', methods=['POST'])
@login_required
def editar_merma():
    p = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if p: p.merma_pct_man = float(request.json['merma']) / 100.0; db.session.commit()
    return jsonify({"success": True})

if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(debug=True)
