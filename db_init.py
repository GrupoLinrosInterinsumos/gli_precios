import os
import secrets

from app import app, db, User
from werkzeug.security import generate_password_hash

def inicializar_base_datos():
    # Entramos al contexto de la aplicación Flask
    with app.app_context():
        print("⏳ Conectando a la base de datos...")

        # 1. Crear todas las tablas definidas en app.py
        db.create_all()
        print("✅ Tablas creadas o verificadas correctamente.")

        # 2. Buscar si ya existe algún SuperAdmin
        admin_existente = User.query.filter_by(role='SuperAdmin').first()

        if not admin_existente:
            print("⏳ Creando usuario SuperAdmin por defecto...")
            # Correo y clave desde variables de entorno; si no hay clave, se genera una aleatoria
            correo = os.environ.get('ADMIN_INICIAL_EMAIL', 'practicante@gli.com')
            clave = os.environ.get('ADMIN_INICIAL_PASSWORD') or secrets.token_urlsafe(12)

            nuevo_admin = User(email=correo, password=generate_password_hash(clave), role='SuperAdmin')
            db.session.add(nuevo_admin)
            db.session.commit()

            print("🎉 ¡Éxito! SuperAdmin creado.")
            print("=======================================")
            print(f"📧 Correo: {correo}")
            print(f"🔑 Clave:  {clave}")
            print("=======================================")
            print("⚠️ Guarda esta clave: no se volverá a mostrar.")
        else:
            print("ℹ️ Todo en orden: Ya existe un SuperAdmin en el sistema.")

if __name__ == '__main__':
    inicializar_base_datos()
