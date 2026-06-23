from datetime import datetime, timezone
from sqlalchemy import Integer, String, Boolean, Numeric, DateTime, Text, ForeignKey, CheckConstraint, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Campana(Base):
    __tablename__ = "Campana"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meta_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    recaudado_manual: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)

class Rifas(Base):
    __tablename__ = "Rifas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    premio: Mapped[str] = mapped_column(String(200), nullable=False)
    precio_ticket_bs: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    precio_ticket_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_numeros: Mapped[int] = mapped_column(Integer, default=1000)
    loteria_referencia: Mapped[str] = mapped_column(String(100), nullable=False)
    fecha_sorteo: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), default="Activa")

    tickets: Mapped[list["Tickets"]] = relationship("Tickets", back_populates="rifa")

class Aportantes(Base):
    __tablename__ = "Aportantes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # PII cifrada con Fernet (valores originales -> tokens "enc:v1:...")
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    cedula: Mapped[str] = mapped_column(String(255), nullable=True)
    telefono: Mapped[str] = mapped_column(String(255), nullable=True)
    mensaje_apoyo: Mapped[str] = mapped_column(Text, nullable=True)
    monto_reportado: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(String(10), nullable=False)
    metodo_pago: Mapped[str] = mapped_column(String(50), nullable=False)
    referencia: Mapped[str] = mapped_column(String(255), nullable=True)
    tipo_aporte: Mapped[str] = mapped_column(String(20), nullable=False)
    boletos_iniciales: Mapped[str] = mapped_column(String(500), nullable=True)
    fecha_aporte: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # HMAC determinista (en hex) para busquedas equality-only sin descifrar
    cedula_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    telefono_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    referencia_hash: Mapped[str] = mapped_column(String(64), nullable=True)

    tickets: Mapped[list["Tickets"]] = relationship("Tickets", back_populates="aportante")

    __table_args__ = (
        Index("ix_aportantes_cedula_hash", "cedula_hash"),
        Index("ix_aportantes_telefono_hash", "telefono_hash"),
        Index("ix_aportantes_referencia_hash", "referencia_hash"),
    )

class Tickets(Base):
    __tablename__ = "Tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rifa_id: Mapped[int] = mapped_column(Integer, ForeignKey("Rifas.id"), nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    aportante_id: Mapped[int] = mapped_column(Integer, ForeignKey("Aportantes.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="Disponible")
    reservado_en: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Cifrado (referencia del pago del cliente)
    referencia_pago: Mapped[str] = mapped_column(String(255), nullable=True)
    referencia_pago_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    monto_reportado: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)

    rifa: Mapped["Rifas"] = relationship("Rifas", back_populates="tickets")
    aportante: Mapped["Aportantes"] = relationship("Aportantes", back_populates="tickets")

    __table_args__ = (
        CheckConstraint('numero >= 0 AND numero <= 999', name='chk_numero_rango'),
        UniqueConstraint('rifa_id', 'numero', name='uq_rifa_numero'),
    )

class LotesConciliacion(Base):
    __tablename__ = "LotesConciliacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha_proceso: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    nombre_archivo: Mapped[str] = mapped_column(String(200), nullable=False)
    registros_procesados: Mapped[int] = mapped_column(Integer, default=0)
    pagos_aprobados: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    """
    Bitacora de accesos a datos sensibles y acciones administrativas.
    Cualquier descifrado, exportacion, confirmacion, etc. se registra aqui.
    """
    __tablename__ = "AuditLog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    usuario: Mapped[str] = mapped_column(String(100), nullable=True)  # 'admin' o 'anon' o 'system'
    ip: Mapped[str] = mapped_column(String(64), nullable=True)
    accion: Mapped[str] = mapped_column(String(50), nullable=False)   # VIEW, DECRYPT, EXPORT, CONFIRM, REVERSE, etc.
    recurso_tipo: Mapped[str] = mapped_column(String(50), nullable=True)  # Aportante, Tickets, etc.
    recurso_id: Mapped[str] = mapped_column(String(64), nullable=True)
    detalle: Mapped[str] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_auditlog_usuario_fecha", "usuario", "timestamp"),
        Index("ix_auditlog_recurso", "recurso_tipo", "recurso_id"),
    )
