from app import app, db, User
from werkzeug.security import generate_password_hash

def inicializar_base_datos():
    # Entramos al contexto de la aplicación Flask
    with app.app_context():
        print("⏳ Conectando a la base de datos...")
        
        # 1. Crear todas las tablas definidas en app.py (User, Producto, Alerta)
        db.create_all()
        print("✅ Tablas creadas o verificadas correctamente.")
        
        # 2. Buscar si ya existe algún SuperAdmin
        admin_existente = User.query.filter_by(role='SuperAdmin').first()
        
        if not admin_existente:
            print("⏳ Creando usuario SuperAdmin por defecto...")
            # Encriptar la contraseña por seguridad
            clave_encriptada = generate_password_hash('admin123')
            
            # Crear el objeto usuario
            nuevo_admin = User(
                email='practicante@gli.com', 
                password=clave_encriptada, 
                role='SuperAdmin'
            )
            
            # Guardarlo en la base de datos
            db.session.add(nuevo_admin)
            db.session.commit()
            
            print("🎉 ¡Éxito! SuperAdmin creado.")
            print("=======================================")
            print("📧 Correo: practicante2@gli.com")
            print("🔑 Clave:  admin123")
            print("=======================================")
            print("⚠️ Recuerda cambiar esta clave desde el panel.")
        else:
            print("ℹ️ Todo en orden: Ya existe un SuperAdmin en el sistema.")

if __name__ == '__main__':
    inicializar_base_datos()