from pydantic import BaseModel, Field
from typing import Optional

class DonacionIn(BaseModel):
    nombre: str = Field(..., min_length=2)
    mensaje_apoyo: Optional[str] = None
    monto_reportado: float = Field(..., gt=0)
    metodo_pago: str
    referencia: str
    telefono: Optional[str] = None  # WhatsApp (opcional)

class ReservaTicketIn(BaseModel):
    nombre: str = Field(..., min_length=2)
    cedula: str
    telefono: str
    monto_reportado: float = Field(..., gt=0)
    metodo_pago: str
    referencia: str
    banco_emisor: str
    cantidad: int = Field(..., gt=0)
