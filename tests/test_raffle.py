import re
from sqlalchemy import select
from fastapi.testclient import TestClient
from main import app
from app.models.all_models import Tickets, Aportantes
from app.services import crypto

client = TestClient(app)

def test_public_reservation_randomness_and_formatting(db_session):
    # Simular una reserva de 5 boletos
    response = client.post("/public/reservar", data={
        "nombre": "Wilson Blanco",
        "cedula": "V20123456",
        "telefono": "04141234567",
        "monto_reportado": 2.50,
        "metodo_pago": "PagoMovil",
        "referencia": "987654",
        "cantidad": 5
    })
    
    # Assert
    assert response.status_code == 200
    
    # Obtener el aportante en la base de datos (se busca por el hash de busqueda de la cedula)
    cedula_hash = crypto.hash_busqueda("V20123456")
    aportante = db_session.execute(
        select(Aportantes).where(Aportantes.cedula_hash == cedula_hash)
    ).scalar_one_or_none()
    
    assert aportante is not None
    
    # Verificar cantidad
    tickets = db_session.execute(
        select(Tickets).where(Tickets.aportante_id == aportante.id)
    ).scalars().all()
    
    assert len(tickets) == 5
    
    # Verificar formato de 3 digitos en boletos_iniciales (ej: "021, 005, 092")
    boletos_iniciales = aportante.boletos_iniciales
    assert boletos_iniciales is not None
    numeros_lista = [n.strip() for n in boletos_iniciales.split(",")]
    
    for num_str in numeros_lista:
        assert re.match(r"^[0-9]{3}$", num_str) is not None
        
    # Verificar que no sean estrictamente consecutivos (aleatoriedad)
    numeros_ints = [int(n) for n in numeros_lista]
    numeros_ints.sort()
    
    es_lineal = True
    for i in range(len(numeros_ints) - 1):
        if numeros_ints[i+1] - numeros_ints[i] != 1:
            es_lineal = False
            break
            
    assert not es_lineal, f"Los números asignados {numeros_ints} son secuenciales lineales."


def test_reassign_tickets_releases_old_ones(db_session):
    # 1. Crear aportante manualmente
    nombre_c = crypto.cifrar("Juan Perez")
    aportante = Aportantes(
        nombre=nombre_c,
        monto_reportado=1.80,
        moneda="USD",
        metodo_pago="Zelle",
        tipo_aporte="Rifa",
        boletos_iniciales="015, 016, 017"
    )
    db_session.add(aportante)
    db_session.commit()
    
    # Reservarles manualmente los tickets 15, 16, 17
    for num in [15, 16, 17]:
        t = db_session.execute(select(Tickets).where(Tickets.numero == num)).scalar_one()
        t.estado = "Reservado"
        t.aportante_id = aportante.id
    db_session.commit()
    
    # Verificar que estan reservados
    tickets_originales = db_session.execute(
        select(Tickets).where(Tickets.aportante_id == aportante.id)
    ).scalars().all()
    assert len(tickets_originales) == 3
    
    # 2. Llamar al endpoint de reasignacion para asignarle 2 boletos al azar
    response = client.post(
        f"/admin/reasignar/aportante/{aportante.id}",
        data={
            "mode": "random",
            "cantidad": 2
        }
    )
    assert response.status_code == 200
    
    # 3. Verificar que los tickets viejos (15, 16, 17) estan libres (Disponible, aportante_id = None)
    for num in [15, 16, 17]:
        t = db_session.execute(select(Tickets).where(Tickets.numero == num)).scalar_one()
        assert t.estado == "Disponible"
        assert t.aportante_id is None
        
    # 4. Verificar que tiene 2 nuevos tickets asignados
    tickets_nuevos = db_session.execute(
        select(Tickets).where(Tickets.aportante_id == aportante.id)
    ).scalars().all()
    assert len(tickets_nuevos) == 2
    for t in tickets_nuevos:
        assert t.estado == "Reservado"


def test_admin_whatsapp_fallback_manual_link(db_session, mock_whatsapp):
    # Simular que el envio directo por OpenWA falla (retorna False)
    mock_whatsapp.enviar_texto.return_value = False
    
    # Crear aportante con telefono
    nombre_c = crypto.cifrar("Maria G.")
    telefono_c = crypto.cifrar("04129998877")
    aportante = Aportantes(
        nombre=nombre_c,
        telefono=telefono_c,
        monto_reportado=0.60,
        moneda="USD",
        metodo_pago="Zelle",
        tipo_aporte="Rifa"
    )
    db_session.add(aportante)
    db_session.commit()
    
    # Enviar mensaje personalizado
    response = client.post(
        f"/admin/whatsapp/aportante/{aportante.id}/enviar",
        data={
            "mensaje": "Hola Maria, estos son tus boletos"
        }
    )
    
    # Assert
    assert response.status_code == 200
    html_content = response.text
    
    # Verificar que contiene el mensaje de advertencia y el enlace de WhatsApp Web manual
    assert "No se pudo enviar automáticamente por OpenWA" in html_content
    assert "https://api.whatsapp.com/send" in html_content
    assert "584129998877" in html_content  # Telefono normalizado con codigo de pais 58
