import io

import pandas as pd
import pytest

from app import db, Producto, Alerta, HistorialCambio, CAMPOS_VENDEDOR

def productos(cliente):
    return {p['nombre']: p for p in cliente.get('/buscar').get_json()['productos']}

def exportar(cliente, ids):
    r = cliente.post('/api/exportar', json={'ids': ids})
    return pd.read_excel(io.BytesIO(r.data)).set_index('Nombre')

# ---------------- Reglas de negocio ----------------

def test_comprado_en_dolares(admin, crear):
    crear("ACIDO CITRICO X 25KG", empresa='ACME', costo_base_ex=2.0, costo_fab_ex=0.1, margen_ex=0.2, merma_pct_man=0.05)
    p = productos(admin)["ACIDO CITRICO X 25KG"]
    assert p['moneda_texto'] == 'USD'
    assert p['costo_actual'] == pytest.approx(2.2)          # 2 + 0.1 + 5% merma de 2
    assert p['precio_lima'] == pytest.approx(2.64)          # + 20% margen
    assert p['precio_provincia'] == pytest.approx(2.75)     # + flete estándar 0.11

def test_flete_exento_para_cramer_salvo_fragancia(admin, crear):
    crear("SABOR FRESA CRAMER X 1KG", costo_base_ex=10.0, margen_ex=0.2)
    crear("AROMA ROSA CRAMER X 1KG", costo_base_ex=10.0, margen_ex=0.2, categoria='FRAGANCIA')
    ps = productos(admin)
    assert ps["SABOR FRESA CRAMER X 1KG"]['flete'] == 0
    assert ps["AROMA ROSA CRAMER X 1KG"]['flete'] == pytest.approx(0.11)

def test_nativo_en_soles(admin, crear):
    crear("AGITADOR DE LECHE INOX", costo_base_ex=40.0, margen_ex=0.25)
    p = productos(admin)["AGITADOR DE LECHE INOX"]
    assert p['moneda_simbolo'] == 'S/' and p['es_pen_exception']
    assert p['precio_lima'] == pytest.approx(50.0)
    assert p['precio_provincia'] == pytest.approx(50.44)    # flete 0.11 x 4

def test_excepcion_clasica_se_muestra_por_4(admin, crear):
    crear("FOSFATO PARA JAMONES X 1KG", costo_base_ex=2.5, margen_ex=0.2)
    p = productos(admin)["FOSFATO PARA JAMONES X 1KG"]
    assert p['precio_lima'] == pytest.approx(12.0)          # 2.5 x 1.2 x 4
    assert p['precio_provincia'] == pytest.approx(12.44)

def test_fabricado_hereda_del_empaque_principal(admin, crear):
    crear("GOMA XANTANA X 25KG", costo_base_ex=5.0, coyuntural_ex=6.0)
    crear("GOMA XANTANA X 150KG", costo_base_ex=4.0, coyuntural_ex=4.5)   # >= 100 kg no es el principal
    crear("GOMA XANTANA X 1KG", tipo_origen='FABRICADO', costo_fab_ex=0.5, margen_ex=0.2)
    p = productos(admin)["GOMA XANTANA X 1KG"]
    assert p['costo_base'] == pytest.approx(5.0) and p['editable_costo'] is False
    assert p['costo_coyuntural'] == pytest.approx(6.5)      # 6 + 0.5 fab
    assert p['precio_lima'] == pytest.approx(7.8)

def test_esencia_hereda_del_empaque_de_1kg(admin, crear):
    crear("ESENCIA DE VAINILLA X 1KG", costo_base_ex=10.0)
    crear("ESENCIA DE VAINILLA X 5KG", costo_base_ex=8.0)
    crear("ESENCIA DE VAINILLA X 100G", tipo_origen='FABRICADO', margen_ex=0.2)
    assert productos(admin)["ESENCIA DE VAINILLA X 100G"]['costo_base'] == pytest.approx(10.0)

def test_comprado_sin_coyuntural_hereda_de_su_hermano(admin, crear):
    crear("LECITINA X 25KG", costo_base_ex=3.0, coyuntural_ex=3.5)
    crear("LECITINA X 5KG", costo_base_ex=3.2)
    assert productos(admin)["LECITINA X 5KG"]['costo_coyuntural'] == pytest.approx(3.5)

# ---------------- Arreglos ----------------

@pytest.mark.parametrize("coyuntural_manual", [None, -1.0, 7.0])
def test_excel_coincide_con_pantalla_en_fabricados(admin, crear, coyuntural_manual):
    crear("PECTINA X 25KG", costo_base_ex=5.0, coyuntural_ex=6.0)
    f = crear("PECTINA X 1KG", tipo_origen='FABRICADO', costo_fab_ex=0.5, margen_ex=0.2, coyuntural_man=coyuntural_manual)
    p = productos(admin)["PECTINA X 1KG"]
    fila = exportar(admin, [f.id]).loc["PECTINA X 1KG"]
    assert fila['Precio de venta'] == round(p['precio_lima'], 2)
    assert fila['Coyuntural'] == round(p['costo_coyuntural'], 2)
    assert fila['Precio PROVINCIA'] == round(p['precio_provincia'], 2)

def test_vendedor_solo_recibe_precios_de_venta(vendedor, crear):
    crear("CUAJO LIQUIDO X 1L", costo_base_ex=5.0, nota='PRECIO ESPECIAL SOLO GERENCIA', nota_visible=False)
    crear("CUAJO POLVO X 1KG", costo_base_ex=5.0, nota='Stock limitado', nota_visible=True)
    crear("PRODUCTO OCULTO X 1KG", costo_base_ex=5.0, visible_ventas=False, margen_ex=0.0)
    j = vendedor.get('/buscar').get_json()
    ps = {p['nombre']: p for p in j['productos']}
    assert set(ps) == {"CUAJO LIQUIDO X 1L", "CUAJO POLVO X 1KG"}
    for p in ps.values():
        assert set(p) == set(CAMPOS_VENDEDOR)
        assert not any('costo' in k or 'margen' in k for k in p)
    assert ps["CUAJO LIQUIDO X 1L"]['nota'] == ''
    assert ps["CUAJO POLVO X 1KG"]['nota'] == 'Stock limitado'
    assert j['alertas'] == []

def test_vendedor_no_puede_exportar_ni_ver_historial(vendedor, crear):
    p = crear("SAL X 1KG", costo_base_ex=1.0)
    assert vendedor.post('/api/exportar', json={'ids': [p.id]}).status_code == 403
    assert vendedor.get(f'/api/historial/{p.id}').status_code == 403

def test_buscar_no_escribe_en_la_base(admin, crear):
    crear("AGITADOR DE LECHE INOX", costo_base_ex=40.0, margen_ex=0.0)   # dispara alerta de margen
    j = admin.get('/buscar').get_json()
    assert [a['msg'] for a in j['alertas']] == ["Margen Crítico (0.0%)"]
    db.session.expire_all()
    assert Alerta.query.count() == 0
    assert Producto.query.one().moneda_simbolo == '$'                    # antes se sobrescribía al buscar

def test_errores_no_exponen_detalles(admin):
    r = admin.post('/subir-maestro', data={'archivo': (io.BytesIO(b'no es excel'), 'roto.xlsx')})
    assert r.status_code == 500
    assert r.get_json() == {"error": "Ocurrió un error interno. Revisa los datos e intenta de nuevo."}

# ---------------- Edición por id e historial ----------------

def test_editar_coyuntural_propaga_y_queda_en_historial(admin, crear):
    principal = crear("LECITINA X 25KG", costo_base_ex=3.0, margen_ex=0.2)
    hermano = crear("LECITINA X 5KG", costo_base_ex=3.2, margen_ex=0.2)
    assert admin.post('/api/editar-costo-coyuntural', json={'id': principal.id, 'valor': '4'}).status_code == 200

    db.session.expire_all()
    assert db.session.get(Producto, hermano.id).coyuntural_man == 4.0
    acciones = {(c.producto_id, c.accion, c.campo) for c in HistorialCambio.query.all()}
    assert (principal.id, 'editar', 'coyuntural') in acciones
    assert (hermano.id, 'propagacion', 'coyuntural') in acciones
    h = admin.get(f'/api/historial/{hermano.id}').get_json()
    assert h['precios'][-1]['precio_lima'] == pytest.approx(4.8)
    assert h['cambios'][0]['usuario'] == 'admin@test.local'

def test_historial_de_precios_solo_registra_cambios(admin, crear):
    p = crear("SAL X 1KG", costo_base_ex=1.0, margen_ex=0.2)
    admin.post('/api/editar-margen', json={'id': p.id, 'valor': '30'})
    admin.post('/api/editar-margen', json={'id': p.id, 'valor': '30'})   # mismo valor: no agrega nada
    admin.post('/api/editar-margen', json={'id': p.id, 'valor': '40'})
    precios = admin.get(f'/api/historial/{p.id}').get_json()['precios']
    assert [r['precio_lima'] for r in precios] == pytest.approx([1.2, 1.3, 1.4])   # incluye el precio previo al primer cambio
    assert precios[0]['motivo'] == 'Registro inicial'
    assert precios[1]['motivo'] == 'Edición de margen en SAL X 1KG'

def test_renombrar_a_un_nombre_existente_da_error_claro(admin, crear):
    crear("SAL X 1KG", costo_base_ex=1.0)
    otro = crear("SAL X 5KG", costo_base_ex=1.0)
    r = admin.post('/api/crear-producto', json={'id': otro.id, 'nombre': 'SAL X 1KG'})
    assert r.status_code == 400 and 'Ya existe' in r.get_json()['error']

def test_editar_producto_por_id_conserva_su_historial(admin, crear):
    p = crear("SAL X 1KG", costo_base_ex=1.0, margen_ex=0.2)
    r = admin.post('/api/crear-producto', json={'id': p.id, 'nombre': 'SAL MARINA X 1KG', 'costo_base': '1', 'margen': '20'})
    assert r.get_json()['id'] == p.id
    cambios = admin.get(f'/api/historial/{p.id}').get_json()['cambios']
    assert any(c['campo'] == 'nombre' and c['despues'] == 'SAL MARINA X 1KG' for c in cambios)

def test_admin_en_vista_vendedor_ve_lo_mismo_que_un_vendedor(admin, vendedor, crear):
    crear("CUAJO LIQUIDO X 1L", costo_base_ex=5.0, nota='SOLO GERENCIA', nota_visible=False)
    crear("PRODUCTO OCULTO X 1KG", costo_base_ex=5.0, visible_ventas=False)
    assert admin.get('/buscar?como=vendedor').get_json() == vendedor.get('/buscar').get_json()
    html = admin.get('/vendedor').get_data(as_text=True)
    assert 'Vista previa' in html and 'como=vendedor' in html
    assert 'Vista previa' not in vendedor.get('/vendedor').get_data(as_text=True)
