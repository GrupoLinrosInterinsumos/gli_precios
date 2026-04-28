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

app = Flask(__name__, template_folder='templates')
CORS(app)

app.config['SECRET_KEY'] = 'GLI_SECURITY_KEY_FINAL_2026'
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
    role = db.Column(db.String(20), nullable=False) 

class Config(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True)
    valor = db.Column(db.Float, default=3.80)

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(250), unique=True, nullable=False)
    codigo = db.Column(db.String(100), default='S/C')
    categoria = db.Column(db.String(100), default='GENERAL')
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

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# =========================================================
# 📘 LÓGICA DE FLETE Y MONEDA
# =========================================================
def get_tc_actual():
    conf = Config.query.filter_by(clave='tipo_cambio').first()
    return conf.valor if conf else 3.80

TARIFA_FLETE_DEFECTO = 0.11

def detectar_proveedor_exacto(nombre_odoo, empresa_col=""):
    if "CRAMER" in str(nombre_odoo).upper(): return "CRAMER"
    if "SACCO" in str(nombre_odoo).upper(): return "SACCO"
    return str(empresa_col).strip().upper()

def get_currency_info(nombre, proveedor):
    nombre_clean = re.sub(r'\s+', '', nombre.upper())
    if "COLAGENOHIDROLIZADOGELNEX" in nombre_clean: return "S/", "PEN"
    
    es_usd = True
    if proveedor == "SACCO" or "SACCO" in nombre.upper():
        es_usd = False
        for exc in ["LYOTO", "LYOFASTAB", "LYOFASTY", "MIXPROFUXION"]:
            if exc in nombre_clean:
                es_usd = True
                break
    return ("$", "USD") if es_usd else ("S/", "PEN")

# =========================================================
# 🔐 RUTAS DE ACCESO, USUARIOS Y TC
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
        flash('Credenciales incorrectas')
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
    if current_user.role not in ['Admin', 'SuperAdmin', 'TC']: return redirect(url_for('home'))
    return render_template('index_admin.html')

@app.route('/vendedor')
@login_required
def vista_vendedor():
    return render_template('index_vendedor.html')

@app.route('/usuarios')
@login_required
def gestion_usuarios():
    if current_user.role != 'SuperAdmin': return redirect(url_for('home'))
    return render_template('superadmin.html', usuarios=User.query.all())

# --- APIS DE PERSONAL Y TIPO DE CAMBIO ---
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
    # El usuario debe tener rol TC o SuperAdmin para guardar el nuevo Tipo de Cambio
    if current_user.role not in ['TC', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    try:
        conf = Config.query.filter_by(clave='tipo_cambio').first()
        conf.valor = float(request.json['tc'])
        db.session.commit()
        return jsonify({"success": True, "tc": conf.valor})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# =========================================================
# 🚀 GESTIÓN DE PRODUCTOS Y EXCEL (RUTAS RESTAURADAS)
# =========================================================
@app.route('/api/crear-producto', methods=['POST'])
@login_required
def crear_producto():
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No autorizado"}), 403
    d = request.json
    nombre_upper = d['nombre'].upper().strip()
    
    if Producto.query.filter_by(nombre=nombre_upper).first():
        return jsonify({"error": "El producto ya existe"}), 400
        
    p = Producto(
        nombre=nombre_upper,
        codigo=d.get('codigo', 'S/C').upper(),
        proveedor=detectar_proveedor_exacto(nombre_upper, d.get('empresa', '')),
        empresa=d.get('empresa', '').upper(),
        costo_base_man=float(d.get('costo_base', 0)),
        costo_fab_man=float(d.get('costo_fab', 0)),
        margen_man=float(d.get('margen', 20)) / 100.0,
        es_manual=True
    )
    sim, txt = get_currency_info(p.nombre, p.proveedor)
    p.moneda_simbolo, p.moneda_texto = sim, txt
    
    db.session.add(p); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/exportar', methods=['POST'])
@login_required
def exportar_excel():
    if current_user.role not in ['Admin', 'SuperAdmin', 'TC']: return jsonify({"error": "No"}), 403
    nombres = request.json.get('productos', [])
    if not nombres: return jsonify({"error": "No hay productos"}), 400
    
    prods = Producto.query.filter(Producto.nombre.in_(nombres)).all()
    data = []
    tc = get_tc_actual()
    
    for p in prods:
        c_base = p.costo_base_man if p.costo_base_man is not None else p.costo_base_ex
        c_fab = p.costo_fab_man if p.costo_fab_man is not None else p.costo_fab_ex
        margen = p.margen_man if p.margen_man is not None else p.margen_ex
        coyun = p.coyuntural_man if p.coyuntural_man is not None else p.coyuntural_ex
        
        merma = c_base * p.merma_pct_man
        c_total = c_base + c_fab + merma
        c_ref = coyun if (coyun > 0 and c_total <= coyun) else c_total
        
        flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
        p_lima = c_ref * (1 + margen)
        p_prov = p_lima + flete
        
        data.append({
            "Producto": p.nombre, "Código": p.codigo, "Empresa": p.empresa, "Proveedor": p.proveedor,
            "Moneda": p.moneda_texto, "Costo Base": c_base, "Costo Fab": c_fab,
            "Merma (%)": p.merma_pct_man * 100, "Costo Total": c_total,
            "Coyuntural": coyun, "Margen (%)": margen * 100, "Precio LIMA": p_lima, "Precio PROVINCIA": p_prov
        })
        
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Precios GLI')
    output.seek(0)
    return send_file(output, download_name='Precios_GLI.xlsx', as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/eliminar-producto', methods=['POST'])
@login_required
def eliminar_producto():
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
    prod = Producto.query.filter_by(nombre=request.json['nombre']).first()
    if prod:
        if prod.es_manual: db.session.delete(prod)
        else: prod.oculto = True
        db.session.commit()
    return jsonify({"success": True})

# --- APIS DE EDICIÓN RÁPIDA (CELDAS DE TABLA) ---
@app.route('/api/editar-<tipo>', methods=['POST'])
@login_required
def editar_celdas(tipo):
    if current_user.role not in ['Admin', 'SuperAdmin']: return jsonify({"error": "No"}), 403
    d = request.json
    p = Producto.query.filter_by(nombre=d['nombre']).first()
    if not p: return jsonify({"error": "No existe"}), 404
    
    val = float(d.get('valor', d.get('costo', d.get('merma', d.get('margen', 0)))))
    
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
        
        merma_monto = c_base * p.merma_pct_man
        c_total = c_base + c_fab + merma_monto
        c_ref = coyun if (coyun > 0 and c_total <= coyun) else c_total
        
        flete = 0.0 if p.proveedor in ["CRAMER", "SACCO"] else (FLETE_ESTANDAR * (tc if p.moneda_texto == 'USD' else 1.0))
        p_lima = c_ref * (1 + margen)
        p_prov = p_lima + flete
        
        res.append({
            "nombre": p.nombre, "codigo": p.codigo, "empresa": p.empresa,
            "costo_base": c_base, "costo_fab": c_fab, "merma_porcentaje": round(p.merma_pct_man * 100, 2),
            "merma_monto": merma_monto, "costo_actual": c_total, "costo_coyuntural": coyun,
            "margen": round(margen * 100, 2), "precio_lima": p_lima, "precio_provincia": p_prov,
            "moneda_simbolo": p.moneda_simbolo, "moneda_texto": p.moneda_texto, 
            "dscto_pv": round((p.dscto_pv_man if p.dscto_pv_man is not None else p.dscto_pv_ex)*100, 2),
            "dscto_dist": round((p.dscto_dist_man if p.dscto_dist_man is not None else p.dscto_dist_ex)*100, 2)
        })
    
    res.sort(key=lambda x: x['nombre'])
    return jsonify({"productos": res, "ultima_actualizacion": datetime.now().strftime("%d/%m/%Y %H:%M"), "tc_actual": tc})

if __name__ == '__main__':
    app.run(debug=True)
