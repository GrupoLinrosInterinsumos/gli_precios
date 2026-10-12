# GLI Precios

App Flask + PostgreSQL (Render) de Grupo Linros Interinsumos: costos reales/fabricación, mermas, márgenes,
fletes zonales (Lima vs. Arequipa/Trujillo), conversión USD/PEN con TC global, cotización para vendedores y
control de sincronización con Odoo (`en_odoo` = `SI`/`NO`). **Está en producción y la usa la empresa.**

## Estructura
- `app.py` — todo el backend: modelos (`User`, `Config`, `Producto`, `Alerta`, `HistorialCambio`, `HistorialPrecio`), reglas de cálculo, rutas y exportación Excel.
  Al importarse ejecuta `db.create_all()` y varios `ALTER TABLE` (migraciones "a mano").
- **Motor de cálculo único:** `construir_contexto(prods)` + `calcular_producto(p, ctx)`. Lo usan `/buscar`, `/api/exportar` y el historial.
  Cualquier cambio de precios va ahí, nunca duplicado en una ruta. `/buscar` es de solo lectura (no escribe en la BD).
- Vendedor/TC reciben solo `CAMPOS_VENDEDOR` (filtrado en el servidor, no en el HTML).
- Historial: `log_cambio`/`log_diferencias` registran quién cambió qué; `registrar_precios(motivo)` guarda una foto de precios
  de los productos cuyo precio cambió (incluye cascadas). Se llama tras cada operación de escritura.
- El frontend identifica productos por `id` (las APIs aceptan `nombre` solo por compatibilidad). La búsqueda filtra en el navegador.
- `templates/index_admin.html` — panel gerencial (Admin/SuperAdmin). `templates/index_vendedor.html` — vista comercial (Vendedor/TC).
- `templates/superadmin.html` — gestión de usuarios. `db_init.py` — crea tablas y SuperAdmin inicial.
- Roles: `SuperAdmin`, `Admin`, `TC`, `Vendedor`. Vendedores solo ven `visible_ventas = True` y nunca costos internos ni notas privadas.

## Reglas de negocio clave (no romper)
- Listas de excepción de moneda: `EXCEPCIONES_NATIVAS`, `EXCEPCIONES_SOLES`, `EXCEPCIONES_SACCO_USD`, `EXCEPCIONES_CLERICI_USD`; factor 4.0 en excepciones clásicas/nativas.
- `detectar_proveedor_exacto`: INTERINSUMOS, CRAMER, SACCO, CAGLIFICIO CLERICI, JM LUDAFA o marca declarada.
- `FABRICADO` hereda costos del `COMPRADO` padre por `get_core_name`; las ESENCIAS heredan del empaque de 1 kg.
- Editar el coyuntural del empaque principal (mayor peso < 100 kg) propaga a sus variantes hermanas.
- `FLETE_ESTANDAR = 0.11` USD para provincias; exentos CRAMER, SACCO, JM LUDAFA salvo categoría `FRAGANCIA`.

## Entorno local de pruebas
- Python venv en `.venv`. Sin `DATABASE_URL`, la app usa SQLite en `instance/gli_database.sqlite`.
- **Nunca** apuntar `DATABASE_URL` a producción en local: `app.py` hace `ALTER TABLE` al importarse.
- `.env` (no versionado) guarda `PROD_DATABASE_URL` (solo lectura) y el usuario local de pruebas.
- Refrescar datos locales desde producción: `.venv\Scripts\python scripts\sync_prod_to_local.py` (conexión read-only).
- Crear/actualizar SuperAdmin local: `.venv\Scripts\python scripts\crear_usuario_local.py`.
- Levantar: `.venv\Scripts\python -m flask --app app run --debug --port 5000` (o la config `gli-local` de `.claude/launch.json`).
- Pruebas: `.venv\Scripts\python -m pytest` (BD en memoria). Dependencias de desarrollo en `requirements-dev.txt`.
- Antes de tocar el cálculo: `.venv\Scripts\python scripts\verificar_precios.py --guardar`; después, sin argumentos, compara los 1.700+ productos
  contra esa foto y verifica que el Excel coincida con la pantalla.
- Ojo: en Postgres la URL necesita el driver psycopg2 explícito con SQLAlchemy 2.1+ (`postgresql+psycopg2://`).

## Despliegue
- Repo: github.com/GrupoLinrosInterinsumos/gli_precios, rama `main` (por confirmar si Render tiene auto-deploy activado: si es así, un push a `main` = cambio en producción).
- Trabajar en ramas y probar en local antes de mergear.
