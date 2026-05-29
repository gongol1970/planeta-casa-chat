import os
import re
import base64
import html as html_parser
from xml.etree import ElementTree as ET
import csv
import io
import uuid
import html as html_lib
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import uvicorn
import requests
from fastapi import FastAPI, APIRouter, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, HTMLResponse
from pydantic import BaseModel, Field
from supabase import create_client
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7


# ============================================================
# PLANETA CASA ERP ADMIN - STANDALONE
# ------------------------------------------------------------
# No toca app_v2.py.
# Ejecutar local:
#   py erp_admin.py
#
# Variables necesarias:
#   SUPABASE_URL
#   SUPABASE_SERVICE_ROLE_KEY
#   ADMIN_TOKEN
#
# Objetivo:
#   - ABM básico de combos por SKU/variante vendible.
#   - Validación estricta de SKUs contra inventory_items.
#   - Simulación/registro de venta con descuento de stock.
# ============================================================

APP_VERSION = "0.1.48-arca-logo-safe"
PORT = int(os.getenv("PORT", "8010"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Facturación / ARCA.
# V0 deja armado el circuito ERP + reporte + impresión A4.
# La emisión fiscal real por WSFE/CAE queda encapsulada para activar cuando estén
# certificados/credenciales/punto de venta definidos en Render.
ARCA_ENV = os.getenv("ARCA_ENV", "homologacion")
ARCA_PTO_VTA = int(os.getenv("ARCA_PTO_VTA", "6"))
ARCA_EMISOR_RAZON_SOCIAL = os.getenv("ARCA_EMISOR_RAZON_SOCIAL", "La Salvia Gonzalo Martin")
ARCA_EMISOR_CUIT = os.getenv("ARCA_EMISOR_CUIT", "23214771659")
ARCA_EMISOR_COND_IVA = os.getenv("ARCA_EMISOR_COND_IVA", "Responsable Inscripto")
ARCA_EMISOR_DOMICILIO = os.getenv("ARCA_EMISOR_DOMICILIO", "PUMACAHUA 86 4TO A, CABA, CP: 1406")
ARCA_EMISOR_IIBB = os.getenv("ARCA_EMISOR_IIBB", ARCA_EMISOR_CUIT)
ARCA_EMISOR_INICIO = os.getenv("ARCA_EMISOR_INICIO", "01/03/2017")
ARCA_EMISOR_EMAIL = os.getenv("ARCA_EMISOR_EMAIL", "glasalvia@outlook.com.ar")
ARCA_EMISOR_WEB = os.getenv("ARCA_EMISOR_WEB", "planetacasa.com.ar")
ARCA_LOGO_URL = os.getenv("ARCA_LOGO_URL", "./ERP.png")
ARCA_DEFAULT_CONCEPTO = os.getenv("ARCA_DEFAULT_CONCEPTO", "Productos")
ARCA_IVA_ALICUOTA = float(os.getenv("ARCA_IVA_ALICUOTA", "21"))

# Emisión real ARCA/WSFE.
# Por seguridad, aunque ARCA_ENV sea produccion, no se emite CAE si ARCA_EMIT_ENABLED no está en true.
ARCA_EMIT_ENABLED = os.getenv("ARCA_EMIT_ENABLED", "false").strip().lower() in ["1", "true", "yes", "si", "sí", "on"]
ARCA_AUTO_INVOICE_ENABLED = os.getenv("ARCA_AUTO_INVOICE_ENABLED", "false").strip().lower() in ["1", "true", "yes", "si", "sí", "on"]
ARCA_WSAA_SERVICE = os.getenv("ARCA_WSAA_SERVICE", "wsfe")
ARCA_CERT_PEM = os.getenv("ARCA_CERT_PEM", "")
ARCA_CERT_BASE64 = os.getenv("ARCA_CERT_BASE64", "")
ARCA_KEY_PEM = os.getenv("ARCA_KEY_PEM", "")
ARCA_KEY_BASE64 = os.getenv("ARCA_KEY_BASE64", "")
ARCA_CERT_FILE = os.getenv("ARCA_CERT_FILE", "")
ARCA_KEY_FILE = os.getenv("ARCA_KEY_FILE", "")
ARCA_WSAA_URL_HOMO = os.getenv("ARCA_WSAA_URL_HOMO", "https://wsaahomo.afip.gov.ar/ws/services/LoginCms")
ARCA_WSAA_URL_PROD = os.getenv("ARCA_WSAA_URL_PROD", "https://wsaa.afip.gov.ar/ws/services/LoginCms")
ARCA_WSFE_URL_HOMO = os.getenv("ARCA_WSFE_URL_HOMO", "https://wswhomo.afip.gov.ar/wsfev1/service.asmx")
ARCA_WSFE_URL_PROD = os.getenv("ARCA_WSFE_URL_PROD", "https://servicios1.afip.gov.ar/wsfev1/service.asmx")
ARCA_WS_TIMEOUT_SECONDS = int(os.getenv("ARCA_WS_TIMEOUT_SECONDS", "30"))

ARCA_WSAA_TA_CACHE: Dict[str, Any] = {}

TN_STORE_ID = os.getenv("TN_STORE_ID", "")
TN_USER_ID = os.getenv("TN_USER_ID", "")
TN_ORDERS_STORE_ID = TN_USER_ID or TN_STORE_ID
# Para actualización de productos/variantes TN.
# Separamos esta variable para no volver a mezclar IDs:
# - TN_ORDERS_STORE_ID: lectura/captura de órdenes.
# - TN_PRODUCTS_STORE_ID: push de stock/precio a productos/variantes.
# Si Render no define TN_PRODUCTS_STORE_ID, usamos TN_STORE_ID primero.
# Importante: TN_USER_ID sirve para órdenes; productos/variantes deben ir por store/product context.
TN_PRODUCTS_STORE_ID = os.getenv("TN_PRODUCTS_STORE_ID") or os.getenv("TN_SYNC_STORE_ID") or TN_STORE_ID or TN_USER_ID
TN_TOKEN = os.getenv("TN_TOKEN") or os.getenv("TN_ACCESS_TOKEN") or ""

ML_ACCESS_TOKEN = os.getenv("ML_ACCESS_TOKEN") or os.getenv("MERCADOLIBRE_ACCESS_TOKEN") or ""
ML_REFRESH_TOKEN = os.getenv("ML_REFRESH_TOKEN") or os.getenv("MERCADOLIBRE_REFRESH_TOKEN") or ""
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID") or os.getenv("ML_APP_ID") or os.getenv("MERCADOLIBRE_CLIENT_ID") or os.getenv("MERCADOLIBRE_APP_ID") or ""
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET") or os.getenv("ML_APP_SECRET") or os.getenv("MERCADOLIBRE_CLIENT_SECRET") or os.getenv("MERCADOLIBRE_APP_SECRET") or ""
ML_USER_ID = os.getenv("ML_USER_ID") or os.getenv("MERCADOLIBRE_USER_ID") or ""

# WhatsApp admin notifications.
# Usa las mismas variables que app_v2.py si el router corre dentro del bot.
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", os.getenv("WHATSAPP_API_VERSION", "v25.0"))
HUMAN_NOTIFY_PHONE = os.getenv("HUMAN_NOTIFY_PHONE", "").replace("+", "").strip()

# Poller automático TN.
TN_AUTO_POLL_ENABLED = os.getenv("TN_AUTO_POLL_ENABLED", "true").strip().lower() in ["1", "true", "yes", "si", "sí", "on"]
TN_AUTO_POLL_INTERVAL_SECONDS = int(os.getenv("TN_AUTO_POLL_INTERVAL_SECONDS", "300"))
TN_AUTO_POLL_LIMIT = int(os.getenv("TN_AUTO_POLL_LIMIT", "10"))
TN_AUTO_POLL_LOOKBACK_MINUTES = int(os.getenv("TN_AUTO_POLL_LOOKBACK_MINUTES", "180"))
TN_POLLER_STARTED_AT = datetime.now(timezone.utc)

# Poller automático ML.
# OJO: queda apagado por default. Activar en Render con ML_AUTO_POLL_ENABLED=true.
ML_AUTO_POLL_ENABLED = os.getenv("ML_AUTO_POLL_ENABLED", "false").strip().lower() in ["1", "true", "yes", "si", "sí", "on"]
ML_AUTO_POLL_INTERVAL_SECONDS = int(os.getenv("ML_AUTO_POLL_INTERVAL_SECONDS", "300"))
ML_AUTO_POLL_LIMIT = int(os.getenv("ML_AUTO_POLL_LIMIT", "5"))
ML_AUTO_POLL_SCAN_LIMIT = int(os.getenv("ML_AUTO_POLL_SCAN_LIMIT", "250"))
ML_AUTO_POLL_STARTED_AT = datetime.now(timezone.utc)

# Autosync operativo ERP.
# Cierra el circuito post EcommApp: los pollers TN/ML capturan ventas y generan sync_jobs;
# este worker procesa la cola sin depender de PowerShell/manual.
ERP_AUTO_SYNC_ENABLED = os.getenv("ERP_AUTO_SYNC_ENABLED", "true").strip().lower() in ["1", "true", "yes", "si", "sí", "on"]
ERP_AUTO_SYNC_INTERVAL_SECONDS = int(os.getenv("ERP_AUTO_SYNC_INTERVAL_SECONDS", "120"))
ERP_AUTO_SYNC_LIMIT = int(os.getenv("ERP_AUTO_SYNC_LIMIT", "50"))
ERP_AUTO_SYNC_START_DELAY_SECONDS = int(os.getenv("ERP_AUTO_SYNC_START_DELAY_SECONDS", "60"))
ERP_AUTO_SYNC_STARTED_AT = datetime.now(timezone.utc)

TN_AUTO_POLL_STATUS = {
    "started_at": TN_POLLER_STARTED_AT.isoformat(),
    "last_run_at": None,
    "last_ok": None,
    "last_error": None,
    "last_result": None,
    "runs": 0,
    "errors": 0,
}

ML_AUTO_POLL_STATUS = {
    "started_at": ML_AUTO_POLL_STARTED_AT.isoformat(),
    "last_run_at": None,
    "last_ok": None,
    "last_error": None,
    "last_result": None,
    "runs": 0,
    "errors": 0,
}

ERP_AUTO_SYNC_STATUS = {
    "enabled": ERP_AUTO_SYNC_ENABLED,
    "started_at": None,
    "last_run_at": None,
    "last_ok": None,
    "last_error": None,
    "last_result": None,
    "runs": 0,
    "errors": 0,
    "interval_seconds": ERP_AUTO_SYNC_INTERVAL_SECONDS,
    "limit": ERP_AUTO_SYNC_LIMIT,
    "start_delay_seconds": ERP_AUTO_SYNC_START_DELAY_SECONDS,
}


if not SUPABASE_URL or not SUPABASE_KEY:
    print("ADVERTENCIA: faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")

sb = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

router = APIRouter()

# App standalone para poder correr: py erp_admin.py
app = FastAPI(title="Planeta Casa ERP Admin Standalone", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELOS
# ============================================================

class BundleComponentIn(BaseModel):
    bundle_sku: str = Field(..., min_length=1)
    component_sku: str = Field(..., min_length=1)
    quantity: float = Field(..., gt=0)


class DeleteBundleComponentIn(BaseModel):
    bundle_sku: str = Field(..., min_length=1)
    component_sku: str = Field(..., min_length=1)


class BundleUpsertIn(BaseModel):
    sku: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    variant_name: Optional[str] = None
    category: Optional[str] = None
    stock: int = Field(0, ge=0)
    active: bool = True


class ManualSaleIn(BaseModel):
    channel: str = Field(..., min_length=1, description="ML, TN, MANUAL, TEST")
    external_order_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1)
    quantity: float = Field(1, gt=0)
    unit_price: Optional[float] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    dry_run: bool = True


class ManualSaleLineIn(BaseModel):
    sku: str = Field(..., min_length=1)
    quantity: float = Field(1, gt=0)
    unit_price: Optional[float] = None
    discount_pct: float = Field(0, ge=0, le=100)
    name: Optional[str] = None


class ManualSaleMultiIn(BaseModel):
    channel: str = Field("MANUAL", min_length=1)
    external_order_id: Optional[str] = None
    customer_name: str = Field(..., min_length=1)
    customer_phone: Optional[str] = None
    note: Optional[str] = None
    lines: List[ManualSaleLineIn] = Field(..., min_length=1)
    dry_run: bool = True


class StockSetIn(BaseModel):
    sku: str = Field(..., min_length=1)
    new_stock: int = Field(..., ge=0)
    channel: str = "ADMIN"
    notes: Optional[str] = None
    dry_run: bool = True


class ArcaCustomerOverrideIn(BaseModel):
    name: Optional[str] = None
    doc_type: Optional[str] = None
    doc_number: Optional[str] = None
    iva_condition: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None


class ArcaInvoiceDraftIn(BaseModel):
    customer: Optional[ArcaCustomerOverrideIn] = None
    concept: str = "Productos"
    invoice_date: Optional[str] = None
    force_type: Optional[str] = None  # A o B
    notes: Optional[str] = None



# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm_sku(s: str) -> str:
    return (s or "").strip()


def normalizar(txt: Any) -> str:
    txt = str(txt or "").lower()
    for a, b in {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u"
    }.items():
        txt = txt.replace(a, b)
    txt = re.sub(r"[^a-z0-9ñ_ -]", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def require_admin(token: Optional[str] = Query(default=None), x_admin_token: Optional[str] = Header(default=None)):
    supplied = token or x_admin_token or ""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado")
    if supplied != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token admin inválido")
    if not sb:
        raise HTTPException(status_code=500, detail="Supabase no configurado")


def q_inventory_base():
    return """
        id,
        sku,
        name,
        variant_name,
        category,
        stock,
        active,
        item_type,
        cost,
        updated_at
    """


def get_item_by_sku(sku: str) -> Optional[Dict[str, Any]]:
    sku = norm_sku(sku)
    data = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .eq("sku", sku)
        .limit(1)
        .execute()
        .data
        or []
    )
    return data[0] if data else None


def get_item_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    data = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .eq("id", item_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return data[0] if data else None


def get_bundle_components_by_bundle_id(bundle_item_id: int) -> List[Dict[str, Any]]:
    rows = (
        sb.table("bundle_components")
        .select("id,bundle_item_id,component_item_id,quantity,created_at")
        .eq("bundle_item_id", bundle_item_id)
        .order("id")
        .execute()
        .data
        or []
    )

    if not rows:
        return []

    comp_ids = [r["component_item_id"] for r in rows]
    comps = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .in_("id", comp_ids)
        .execute()
        .data
        or []
    )
    comp_by_id = {c["id"]: c for c in comps}

    out = []
    for r in rows:
        comp = comp_by_id.get(r["component_item_id"])
        out.append({
            **r,
            "component": comp,
            "component_sku": comp.get("sku") if comp else None,
            "component_name": comp.get("name") if comp else None,
            "component_stock": comp.get("stock") if comp else None,
        })
    return out


def get_bundle_components_by_sku(bundle_sku: str) -> Dict[str, Any]:
    bundle = get_item_by_sku(bundle_sku)
    if not bundle:
        raise HTTPException(status_code=404, detail=f"No existe el SKU combo: {bundle_sku}")

    components = get_bundle_components_by_bundle_id(bundle["id"])
    return {
        "bundle": bundle,
        "components": components,
        "is_bundle": len(components) > 0,
        "available_to_sell": calc_bundle_available_from_components(components),
    }


def calc_bundle_available_from_components(components: List[Dict[str, Any]]) -> Optional[int]:
    if not components:
        return None

    posibles = []
    for row in components:
        comp = row.get("component") or {}
        stock = comp.get("stock")
        qty = float(row.get("quantity") or 0)
        if stock is None or qty <= 0:
            return 0
        posibles.append(int(float(stock) // qty))

    return min(posibles) if posibles else None


def mark_as_bundle_if_needed(item_id: int):
    # Conservador: solo escribe item_type='bundle' si está vacío, single o product.
    item = get_item_by_id(item_id)
    current = normalizar(item.get("item_type")) if item else ""
    if current in ["", "single", "product", "producto", "productos"]:
        sb.table("inventory_items").update({
            "item_type": "bundle",
            "updated_at": now_iso(),
        }).eq("id", item_id).execute()


def create_stock_movement(
    sku: str,
    movement_type: str,
    channel: str,
    quantity: float,
    previous_stock: float,
    new_stock: float,
    reference_id: str,
    reference_type: str,
    notes: str,
):
    sb.table("stock_movements").insert({
        "sku": sku,
        "movement_type": movement_type,
        "channel": channel,
        "quantity": quantity,
        "previous_stock": previous_stock,
        "new_stock": new_stock,
        "reference_id": reference_id,
        "reference_type": reference_type,
        "notes": notes,
        "created_at": now_iso(),
    }).execute()


def build_sync_source_meta(
    channel: str,
    external_order_id: str,
    raw_payload: Optional[Dict[str, Any]] = None,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    erp_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Metadata de origen para rastrear de dónde salió cada sync_job."""
    raw = raw_payload or {}
    ch = norm_sku(channel).upper()
    external_id = norm_sku(external_order_id)

    if ch == "TN":
        visible = raw.get("tn_order_number") or external_id
        if visible and not str(visible).startswith("#"):
            visible = f"#{visible}"
        api_id = raw.get("tn_order_id") or external_id
        source_created = raw.get("tn_created_at") or raw.get("tn_updated_at") or now_iso()
        pack_id = None
        shipping_id = raw.get("tn_shipping_id") or raw.get("tn_shipping_status")
    elif ch == "ML":
        visible = raw.get("ml_order_id") or external_id
        api_id = raw.get("ml_order_id") or external_id
        source_created = raw.get("ml_date_created") or raw.get("ml_date_closed") or now_iso()
        pack_id = raw.get("ml_pack_id")
        shipping_id = raw.get("ml_shipping_id")
    else:
        visible = external_id
        api_id = erp_order_id or external_id
        source_created = now_iso()
        pack_id = None
        shipping_id = None

    return {
        "source_channel": ch,
        "source_order_id": external_id,
        "source_order_number": str(visible) if visible is not None else None,
        "source_order_api_id": str(api_id) if api_id is not None else None,
        "source_pack_id": str(pack_id) if pack_id is not None else None,
        "source_shipping_id": str(shipping_id) if shipping_id is not None else None,
        "source_customer_name": customer_name,
        "source_created_at": source_created,
        "source_meta": {
            "erp_order_id": erp_order_id,
            "customer_phone": customer_phone,
            "raw_source_keys": sorted(list(raw.keys()))[:50],
        },
    }


def _open_sync_job_for_listing(marketplace: str, listing_id: str, variant_id: Optional[str]) -> Optional[Dict[str, Any]]:
    q = (
        sb.table("sync_jobs")
        .select("*")
        .eq("marketplace", marketplace)
        .eq("listing_id", str(listing_id))
        .in_("status", ["pending", "failed_retry"])
        .limit(1)
    )
    if variant_id is None or str(variant_id) == "":
        try:
            q = q.is_("variant_id", "null")
        except Exception:
            q = q.eq("variant_id", None)
    else:
        q = q.eq("variant_id", str(variant_id))
    rows = q.execute().data or []
    return rows[0] if rows else None


def create_sync_jobs_for_sku(sku: str, target_stock: int, source_meta: Optional[Dict[str, Any]] = None):
    """
    Crea o actualiza jobs abiertos de sincronización.

    Regla nueva:
    - Si ya existe un job abierto para marketplace/listing/variant, NO falla por unique.
    - Actualiza target_stock al último stock master y refresca metadata de origen.
    - Si no existe, crea el job.
    """
    try:
        listings = (
            sb.table("marketplace_listings")
            .select("sku,marketplace,external_product_id,external_variant_id,external_full_id,price,status")
            .eq("sku", sku)
            .execute()
            .data
            or []
        )

        created = 0
        updated = 0
        skipped = 0
        errors = []

        for l in listings:
            try:
                marketplace = l.get("marketplace") or ""
                listing_id = l.get("external_product_id") or l.get("external_full_id")
                variant_id = l.get("external_variant_id")

                if not marketplace or not listing_id:
                    skipped += 1
                    continue

                now = now_iso()
                base_payload = {
                    "job_type": "stock_update",
                    "marketplace": marketplace,
                    "listing_id": str(listing_id),
                    "variant_id": str(variant_id) if variant_id is not None and str(variant_id) != "" else None,
                    "sku": sku,
                    "target_stock": int(target_stock),
                    "status": "pending",
                    "attempts": 0,
                    "max_attempts": 5,
                    "last_error": None,
                    "updated_at": now,
                    "next_retry_at": now,
                    "target_price": None,
                    "sync_stock": True,
                    "sync_price": False,
                }
                if source_meta:
                    base_payload.update({k: v for k, v in source_meta.items() if v is not None})

                existing = _open_sync_job_for_listing(marketplace, str(listing_id), base_payload["variant_id"])
                if existing:
                    sb.table("sync_jobs").update(base_payload).eq("id", existing["id"]).execute()
                    updated += 1
                else:
                    insert_payload = dict(base_payload)
                    insert_payload["created_at"] = now
                    sb.table("sync_jobs").insert(insert_payload).execute()
                    created += 1
            except Exception as e:
                skipped += 1
                errors.append(str(e))

        return {
            "ok": len(errors) == 0,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors[:5],
        }

    except Exception as e:
        return {
            "ok": False,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [str(e)],
        }


def bundles_that_use_component(component_item_id: int) -> List[Dict[str, Any]]:
    rows = (
        sb.table("bundle_components")
        .select("bundle_item_id")
        .eq("component_item_id", component_item_id)
        .execute()
        .data
        or []
    )
    bundle_ids = sorted({r["bundle_item_id"] for r in rows})
    if not bundle_ids:
        return []

    bundles = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .in_("id", bundle_ids)
        .execute()
        .data
        or []
    )
    return bundles


def recalc_and_sync_bundle(bundle_item: Dict[str, Any], dry_run: bool = False, source_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    comps = get_bundle_components_by_bundle_id(bundle_item["id"])
    available = calc_bundle_available_from_components(comps)
    if available is None:
        available = int(bundle_item.get("stock") or 0)

    old_stock = int(bundle_item.get("stock") or 0)
    result = {
        "sku": bundle_item.get("sku"),
        "old_stock": old_stock,
        "new_stock": int(available),
        "changed": old_stock != int(available),
        "dry_run": dry_run,
    }

    if not dry_run and result["changed"]:
        sb.table("inventory_items").update({
            "stock": int(available),
            "updated_at": now_iso(),
        }).eq("id", bundle_item["id"]).execute()

        create_stock_movement(
            sku=bundle_item["sku"],
            movement_type="bundle_recalc",
            channel="ERP",
            quantity=int(available) - old_stock,
            previous_stock=old_stock,
            new_stock=int(available),
            reference_id=str(bundle_item["id"]),
            reference_type="bundle_recalc",
            notes="Stock de combo recalculado por componentes",
        )
        result["sync_jobs"] = create_sync_jobs_for_sku(bundle_item["sku"], int(available), source_meta=source_meta)

    return result


def affected_bundle_recalcs_for_component(component_item_id: int, dry_run: bool = False) -> List[Dict[str, Any]]:
    results = []
    for bundle in bundles_that_use_component(component_item_id):
        results.append(recalc_and_sync_bundle(bundle, dry_run=dry_run))
    return results


def safe_insert_order(channel: str, external_order_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Usa el unique existente (channel, external_order_id).
    existing = (
        sb.table("orders")
        .select("*")
        .eq("channel", channel)
        .eq("external_order_id", external_order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return {"inserted": False, "order": existing[0]}

    order_id = str(uuid.uuid4())
    row = {
        "id": order_id,
        "channel": channel,
        "external_order_id": external_order_id,
        "status": "paid",
        "customer_name": payload.get("customer_name"),
        "customer_phone": payload.get("customer_phone"),
        "total": payload.get("total"),
        "raw_data": payload,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    inserted = sb.table("orders").insert(row).execute().data
    return {"inserted": True, "order": inserted[0] if inserted else row}


def decrement_item_stock(
    item: Dict[str, Any],
    qty_to_decrement: float,
    channel: str,
    reference_id: str,
    reference_type: str,
    notes: str,
    dry_run: bool,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    old_stock = int(item.get("stock") or 0)
    new_stock = old_stock - int(qty_to_decrement)

    result = {
        "sku": item["sku"],
        "name": item.get("name"),
        "qty_decrement": int(qty_to_decrement),
        "old_stock": old_stock,
        "new_stock": new_stock,
        "insufficient_stock": new_stock < 0,
        "dry_run": dry_run,
    }

    if not dry_run:
        sb.table("inventory_items").update({
            "stock": new_stock,
            "updated_at": now_iso(),
        }).eq("id", item["id"]).execute()

        create_stock_movement(
            sku=item["sku"],
            movement_type="sale",
            channel=channel,
            quantity=-abs(int(qty_to_decrement)),
            previous_stock=old_stock,
            new_stock=new_stock,
            reference_id=reference_id,
            reference_type=reference_type,
            notes=notes,
        )

        result["sync_jobs"] = create_sync_jobs_for_sku(item["sku"], new_stock, source_meta=source_meta)

    return result




# ============================================================
# WHATSAPP ADMIN / POLLER HELPERS
# ============================================================

def parse_tn_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        txt = str(value).strip()
        # TN devuelve +0000; fromisoformat prefiere +00:00.
        if re.search(r"[+-]\d{4}$", txt):
            txt = txt[:-5] + txt[-5:-2] + ":" + txt[-2:]
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def money_fmt(value: Any) -> str:
    try:
        n = float(str(value or 0).replace(",", "."))
        return ("$" + f"{n:,.0f}").replace(",", ".")
    except Exception:
        return str(value or "")


def mask_phone_for_debug(phone: str) -> str:
    p = str(phone or "")
    if len(p) <= 6:
        return "***"
    return p[:4] + "***" + p[-3:]


def send_whatsapp_admin(texto: str) -> Dict[str, Any]:
    """
    Aviso directo a Gonzalo por WhatsApp Cloud API.
    No registra en conversaciones del bot para no mezclar chats de clientes.
    """
    if not HUMAN_NOTIFY_PHONE:
        return {"ok": False, "error": "HUMAN_NOTIFY_PHONE no configurado"}
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"ok": False, "error": "WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID no configurado"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": HUMAN_NOTIFY_PHONE,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": (texto or "")[:4000],
        },
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:1000]}
        return {
            "ok": r.status_code < 300,
            "status_code": r.status_code,
            "to": HUMAN_NOTIFY_PHONE,
            "to_masked": mask_phone_for_debug(HUMAN_NOTIFY_PHONE),
            "phone_number_id": WHATSAPP_PHONE_NUMBER_ID,
            "graph_api_version": GRAPH_API_VERSION,
            "response": body,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "to": HUMAN_NOTIFY_PHONE,
            "to_masked": mask_phone_for_debug(HUMAN_NOTIFY_PHONE),
            "phone_number_id": WHATSAPP_PHONE_NUMBER_ID,
            "graph_api_version": GRAPH_API_VERSION,
        }


def order_exists(channel: str, external_order_id: str) -> bool:
    rows = (
        sb.table("orders")
        .select("id")
        .eq("channel", channel)
        .eq("external_order_id", str(external_order_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def get_order_row(channel: str, external_order_id: str) -> Optional[Dict[str, Any]]:
    rows = (
        sb.table("orders")
        .select("*")
        .eq("channel", channel)
        .eq("external_order_id", str(external_order_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def mark_order_whatsapp_notified(order_id: str, notify_result: Dict[str, Any]):
    if not order_id:
        return

    rows = (
        sb.table("orders")
        .select("raw_data")
        .eq("id", order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    raw = {}
    if rows:
        raw = rows[0].get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {"previous_raw_data": raw}

    raw["tn_whatsapp_notified_at"] = now_iso()
    raw["tn_whatsapp_notify_result"] = notify_result

    sb.table("orders").update({
        "raw_data": raw,
        "updated_at": now_iso(),
    }).eq("id", order_id).execute()


def order_whatsapp_notified(order_row: Dict[str, Any]) -> bool:
    raw = (order_row or {}).get("raw_data") or {}
    return isinstance(raw, dict) and bool(raw.get("tn_whatsapp_notified_at"))


def build_tn_sale_whatsapp_message(result: Dict[str, Any], tn_order: Dict[str, Any]) -> str:
    number = tn_order.get("number") or result.get("external_order_id")
    customer = ((result.get("order") or {}).get("customer_name") or tn_order.get("contact_name") or "Sin nombre")
    total = (result.get("order") or {}).get("total") or tn_order.get("total")
    payment = tn_order.get("payment_status") or ""
    shipping = tn_order.get("shipping_option") or tn_order.get("shipping_status") or ""
    status = tn_order.get("status") or ""

    lines = []
    for row in result.get("stock_applied") or []:
        lines.append(
            f"- {row.get('sku')}: {row.get('old_stock')} → {row.get('new_stock')} "
            f"({row.get('qty_decrement')})"
        )

    sold = []
    for line in result.get("sold_lines") or []:
        sold.append(f"- {line.get('sku')} x{line.get('quantity')}: {line.get('name') or ''}".strip())

    msg = [
        f"Nueva venta TN #{number}",
        "",
        f"Cliente: {customer}",
        f"Total: {money_fmt(total)}",
        f"Pago: {payment}",
        f"Estado TN: {status}",
        f"Envío/retiro: {shipping}",
    ]

    if sold:
        msg += ["", "Productos:"] + sold[:8]

    if lines:
        msg += ["", "Stock descontado:"] + lines[:12]

    msg += ["", "Ya está aplicada en el ERP."]

    return "\n".join(msg)


def tn_list_orders(page: int = 1, per_page: int = 10) -> List[Dict[str, Any]]:
    if not TN_ORDERS_STORE_ID or not TN_TOKEN:
        raise HTTPException(status_code=500, detail="Faltan TN_USER_ID/TN_STORE_ID o TN_TOKEN")

    url = f"https://api.tiendanube.com/v1/{TN_ORDERS_STORE_ID}/orders"
    try:
        r = requests.get(url, headers=tn_headers(), params={"page": page, "per_page": per_page}, timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con TN orders: {str(e)}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TN GET orders {r.status_code}: {r.text}")

    data = r.json()
    return data if isinstance(data, list) else []


def tn_order_is_paid(order: Dict[str, Any]) -> bool:
    return normalizar(order.get("payment_status")) == "paid"


def tn_order_is_recent_for_auto(order: Dict[str, Any], lookback_minutes: int) -> bool:
    """
    Blindaje anti-descuento histórico.
    Usa updated_at primero porque una orden pudo pagarse luego de creada.
    """
    dt = (
        parse_tn_datetime(order.get("updated_at"))
        or parse_tn_datetime(order.get("paid_at"))
        or parse_tn_datetime(order.get("created_at"))
    )
    if not dt:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(lookback_minutes or 180)))
    return dt >= cutoff


def tn_build_order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    internal_id = order.get("id")
    if not internal_id:
        raise HTTPException(status_code=400, detail="Orden TN sin id interno")

    lines = tn_extract_lines(order)
    customer = tn_extract_customer(order)

    total = order.get("total") or order.get("subtotal")
    try:
        total = float(str(total).replace(",", ".")) if total is not None else None
    except Exception:
        total = None

    return {
        "external_order_id": str(internal_id),
        "lines": lines,
        "customer_name": customer.get("name"),
        "customer_phone": customer.get("phone"),
        "total": total,
        "raw_payload": {
            "tn_order_id": str(internal_id),
            "tn_order_number": order.get("number"),
            "tn_status": order.get("status"),
            "tn_payment_status": order.get("payment_status"),
            "tn_shipping_status": order.get("shipping_status"),
            "tn_created_at": order.get("created_at"),
            "tn_updated_at": order.get("updated_at"),
            "tn_paid_at": order.get("paid_at"),
            "tn_raw": order,
        },
    }


def apply_tn_order_payload(order: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    built = tn_build_order_payload(order)
    return process_order_lines(
        channel="TN",
        external_order_id=built["external_order_id"],
        lines=built["lines"],
        raw_payload=built["raw_payload"],
        dry_run=dry_run,
        customer_name=built["customer_name"],
        customer_phone=built["customer_phone"],
        total=built["total"],
    )


def notify_tn_sale_once(result: Dict[str, Any], tn_order: Dict[str, Any]) -> Dict[str, Any]:
    order_row = result.get("order")
    if not order_row:
        return {"ok": False, "skipped": True, "reason": "Sin order row"}

    if order_whatsapp_notified(order_row):
        return {"ok": True, "skipped": True, "reason": "Ya notificada"}

    msg = build_tn_sale_whatsapp_message(result, tn_order)
    notify_result = send_whatsapp_admin(msg)

    # Marcamos como notificada aunque falle el envío, para evitar loop infinito de mensajes/errores.
    # Se puede reavisar manualmente con endpoint dedicado si hace falta.
    mark_order_whatsapp_notified(order_row.get("id"), notify_result)
    return notify_result


def poll_tn_orders_once(
    limit: int = 10,
    dry_run: bool = False,
    notify: bool = True,
    apply_existing: bool = False,
    auto_mode: bool = False,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 10), 50))
    orders_tn = tn_list_orders(page=1, per_page=limit)

    results = []
    applied = 0
    skipped = 0
    errors = 0

    for order in orders_tn:
        item_result = {
            "tn_order_id": order.get("id"),
            "tn_number": order.get("number"),
            "payment_status": order.get("payment_status"),
            "status": order.get("status"),
            "shipping_status": order.get("shipping_status"),
            "created_at": order.get("created_at"),
            "updated_at": order.get("updated_at"),
            "paid_at": order.get("paid_at"),
            "action": None,
        }

        try:
            if not tn_order_is_paid(order):
                item_result["action"] = "skipped_not_paid"
                skipped += 1
                results.append(item_result)
                continue

            if auto_mode and not tn_order_is_recent_for_auto(order, TN_AUTO_POLL_LOOKBACK_MINUTES):
                item_result["action"] = "skipped_old_for_auto"
                skipped += 1
                results.append(item_result)
                continue

            external_id = str(order.get("id"))
            if order_exists("TN", external_id) and not apply_existing:
                row = get_order_row("TN", external_id)
                item_result["action"] = "skipped_duplicate"
                item_result["erp_order_id"] = (row or {}).get("id")
                item_result["whatsapp_notified"] = order_whatsapp_notified(row or {})
                skipped += 1
                results.append(item_result)
                continue

            process_result = apply_tn_order_payload(order, dry_run=dry_run)
            item_result["process_result"] = process_result

            if process_result.get("duplicate"):
                item_result["action"] = "duplicate"
                skipped += 1
            elif dry_run:
                item_result["action"] = "dry_run_preview"
                skipped += 1
            else:
                item_result["action"] = "applied"
                applied += 1
                if notify:
                    item_result["whatsapp_notify"] = notify_tn_sale_once(process_result, order)

            results.append(item_result)

        except Exception as e:
            errors += 1
            item_result["action"] = "error"
            item_result["error"] = str(e)
            results.append(item_result)

    return {
        "ok": errors == 0,
        "dry_run": dry_run,
        "auto_mode": auto_mode,
        "limit": limit,
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "items": results,
    }


_poll_thread_started = False

def tn_auto_poller_loop():
    # Pequeña espera inicial: deja levantar FastAPI y evita golpear TN durante deploy.
    time.sleep(20)
    while True:
        try:
            if TN_AUTO_POLL_ENABLED and sb and TN_TOKEN and TN_ORDERS_STORE_ID:
                result = poll_tn_orders_once(
                    limit=TN_AUTO_POLL_LIMIT,
                    dry_run=False,
                    notify=True,
                    apply_existing=False,
                    auto_mode=True,
                )
                TN_AUTO_POLL_STATUS["runs"] += 1
                TN_AUTO_POLL_STATUS["last_run_at"] = now_iso()
                TN_AUTO_POLL_STATUS["last_result"] = {
                    "ok": result.get("ok"),
                    "applied": result.get("applied"),
                    "skipped": result.get("skipped"),
                    "errors": result.get("errors"),
                    "limit": result.get("limit"),
                    "auto_mode": result.get("auto_mode"),
                }
                TN_AUTO_POLL_STATUS["last_ok"] = bool(result.get("ok"))
                TN_AUTO_POLL_STATUS["last_error"] = None if result.get("ok") else f"errors={result.get('errors')}"
        except Exception as e:
            TN_AUTO_POLL_STATUS["runs"] += 1
            TN_AUTO_POLL_STATUS["errors"] += 1
            TN_AUTO_POLL_STATUS["last_run_at"] = now_iso()
            TN_AUTO_POLL_STATUS["last_ok"] = False
            TN_AUTO_POLL_STATUS["last_error"] = str(e)
            print("TN auto poll error:", str(e))
        time.sleep(max(60, int(TN_AUTO_POLL_INTERVAL_SECONDS or 300)))


def start_tn_auto_poller_once():
    global _poll_thread_started
    if _poll_thread_started:
        return
    _poll_thread_started = True
    t = threading.Thread(target=tn_auto_poller_loop, daemon=True)
    t.start()


_ml_poll_thread_started = False

def ml_auto_poller_loop():
    # Espera inicial mayor que TN para no golpear todo junto en deploy.
    time.sleep(35)
    while True:
        try:
            if ML_AUTO_POLL_ENABLED and sb and (ML_ACCESS_TOKEN or ML_REFRESH_TOKEN) and ML_USER_ID:
                result = poll_ml_orders_once(
                    limit=ML_AUTO_POLL_LIMIT,
                    dry_run=False,
                    notify=True,
                    apply_existing=False,
                )
                ML_AUTO_POLL_STATUS["runs"] += 1
                ML_AUTO_POLL_STATUS["last_run_at"] = now_iso()
                ML_AUTO_POLL_STATUS["last_result"] = {
                    "ok": result.get("ok"),
                    "applied": result.get("applied"),
                    "skipped": result.get("skipped"),
                    "errors": result.get("errors"),
                    "limit": result.get("limit"),
                    "ml_auto_poll_scan_limit": result.get("ml_auto_poll_scan_limit"),
                }
                ML_AUTO_POLL_STATUS["last_ok"] = bool(result.get("ok"))
                ML_AUTO_POLL_STATUS["last_error"] = None if result.get("ok") else f"errors={result.get('errors')}"
        except Exception as e:
            ML_AUTO_POLL_STATUS["runs"] += 1
            ML_AUTO_POLL_STATUS["errors"] += 1
            ML_AUTO_POLL_STATUS["last_run_at"] = now_iso()
            ML_AUTO_POLL_STATUS["last_ok"] = False
            ML_AUTO_POLL_STATUS["last_error"] = str(e)
            print("ML auto poll error:", str(e))
        time.sleep(max(60, int(ML_AUTO_POLL_INTERVAL_SECONDS or 300)))


def start_ml_auto_poller_once():
    global _ml_poll_thread_started
    if _ml_poll_thread_started:
        return
    _ml_poll_thread_started = True
    t = threading.Thread(target=ml_auto_poller_loop, daemon=True)
    t.start()



# ============================================================
# TIENDA NUBE - LECTURA Y PROCESO DE ÓRDENES
# ============================================================


def tn_headers():
    return {
        "Authentication": f"bearer {TN_TOKEN}",
        "User-Agent": "PlanetaCasaERP (gongol1970@gmail.com)",
        "Content-Type": "application/json",
    }


# ============================================================
# ERP SYNC WORKER - MIGRADO DESDE app_v2.py
# ============================================================

def validar_sync_worker_config():
    faltan = []
    if not sb:
        faltan.append("Supabase")
    if not TN_PRODUCTS_STORE_ID:
        faltan.append("TN_PRODUCTS_STORE_ID/TN_USER_ID/TN_STORE_ID")
    if not TN_TOKEN:
        faltan.append("TN_TOKEN")
    if faltan:
        raise Exception("Faltan variables sync worker: " + ", ".join(faltan))


def buscar_sync_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    rows = (
        sb.table("sync_jobs")
        .select("*")
        .in_("status", ["pending", "failed_retry"])
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
        .data
        or []
    )
    return rows


def actualizar_sync_job(job_id: str, data: Dict[str, Any]):
    payload = dict(data or {})
    payload["updated_at"] = now_iso()
    sb.table("sync_jobs").update(payload).eq("id", job_id).execute()


def actualizar_tn_desde_sync_job(job: Dict[str, Any]) -> bool:
    """
    Actualiza stock/precio en Tienda Nube desde sync_jobs.
    Vive en erp_admin.py para usar la misma configuración TN que el resto del ERP.
    """
    listing_id = job.get("listing_id") or job.get("external_product_id")
    variant_id = job.get("variant_id") or job.get("external_variant_id")

    if not listing_id:
        raise Exception("TN listing_id vacío")
    if not variant_id:
        raise Exception("TN variant_id vacío")

    sync_stock = bool(job.get("sync_stock", True))
    sync_price = bool(job.get("sync_price", False))

    payload = {}
    if sync_stock:
        payload["stock"] = int(job.get("target_stock") or 0)
    if sync_price:
        target_price = job.get("target_price")
        if target_price is not None:
            payload["price"] = str(target_price)

    if not payload:
        raise Exception("Job TN sin payload")

    url = f"https://api.tiendanube.com/v1/{TN_PRODUCTS_STORE_ID}/products/{listing_id}/variants/{variant_id}"
    r = requests.put(url, headers=tn_headers(), json=payload, timeout=45)

    if r.status_code not in (200, 201):
        raise Exception(
            f"TN ERROR {r.status_code}: {r.text} | "
            f"products_store_id_used={TN_PRODUCTS_STORE_ID}"
        )
    return True


def ml_get_marketplace_listing_for_sync_job(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Busca la fila ML de marketplace_listings asociada al sync_job.
    Sirve para recuperar raw_data.user_product_id sin depender siempre de GET /items.
    """
    listing_id = str(job.get("listing_id") or job.get("external_product_id") or "").strip()
    variant_id_raw = job.get("variant_id") or job.get("external_variant_id")
    variant_id = str(variant_id_raw).strip() if variant_id_raw not in [None, "", 0, "0"] else "0"
    sku = str(job.get("sku") or "").strip()

    if not listing_id:
        return None

    q = (
        sb.table("marketplace_listings")
        .select("*")
        .eq("marketplace", "ML")
        .eq("external_product_id", listing_id)
        .limit(5)
    )
    if sku:
        q = q.eq("sku", sku)

    rows = q.execute().data or []
    if not rows:
        return None

    # Preferimos variante exacta cuando existe.
    for r in rows:
        rv = str(r.get("external_variant_id") or "0").strip()
        if rv == variant_id:
            return r
    return rows[0]


def ml_deep_find_first(obj: Any, keys: List[str]) -> Optional[Any]:
    """Busca recursivamente la primera clave útil dentro de raw_data."""
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if v not in [None, "", 0, "0"]:
                return v
        for v in obj.values():
            found = ml_deep_find_first(v, keys)
            if found not in [None, "", 0, "0"]:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = ml_deep_find_first(v, keys)
            if found not in [None, "", 0, "0"]:
                return found
    return None


def ml_get_item_for_stock_sync(item_id: str) -> Dict[str, Any]:
    url = f"https://api.mercadolibre.com/items/{item_id}"
    r = ml_request("GET", url, timeout=45)
    if r.status_code != 200:
        raise Exception(f"ML GET item para stock {r.status_code}: {r.text[:1000]}")
    return r.json()


def ml_resolve_user_product_id_for_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resuelve user_product_id para actualizar stock propio por /user-products/{id}/stock.

    Descubrimiento confirmado:
    - item.available_quantity puede representar FULL/meli_facility.
    - user-products/{user_product_id}/stock separa locations:
      selling_address = stock propio editable
      meli_facility = stock FULL ML
    """
    listing_id = str(job.get("listing_id") or job.get("external_product_id") or "").strip()
    if not listing_id:
        raise Exception("ML listing_id vacío")

    listing_row = ml_get_marketplace_listing_for_sync_job(job)
    raw = (listing_row or {}).get("raw_data") or {}

    user_product_id = ml_deep_find_first(raw, ["user_product_id", "userProductId", "user_product"])
    inventory_id = ml_deep_find_first(raw, ["inventory_id", "inventoryId"])
    logistic_type = None

    item_json = None
    if not user_product_id:
        item_json = ml_get_item_for_stock_sync(listing_id)
        user_product_id = item_json.get("user_product_id")
        inventory_id = inventory_id or item_json.get("inventory_id")
        logistic_type = ((item_json.get("shipping") or {}).get("logistic_type"))
    else:
        # Igual intentamos leer el item para diagnosticar logistic_type; si falla no bloquea si ya tenemos UPID.
        try:
            item_json = ml_get_item_for_stock_sync(listing_id)
            inventory_id = inventory_id or item_json.get("inventory_id")
            logistic_type = ((item_json.get("shipping") or {}).get("logistic_type"))
        except Exception:
            item_json = None

    if not user_product_id:
        raise Exception("No pude resolver user_product_id para stock ML")

    return {
        "user_product_id": str(user_product_id),
        "inventory_id": str(inventory_id) if inventory_id else None,
        "logistic_type": logistic_type,
        "item": item_json,
        "listing_row_id": (listing_row or {}).get("id"),
    }


def ml_get_user_product_stock_response(user_product_id: str) -> requests.Response:
    """GET crudo de stock user-product. ML devuelve x-version en headers para escritura."""
    url = f"https://api.mercadolibre.com/user-products/{user_product_id}/stock"
    r = ml_request("GET", url, timeout=45)
    if r.status_code != 200:
        raise Exception(f"ML GET user-product stock {r.status_code}: {r.text[:1000]}")
    return r


def ml_get_user_product_stock(user_product_id: str) -> Dict[str, Any]:
    return ml_get_user_product_stock_response(user_product_id).json()


def ml_update_user_product_selling_address_stock(user_product_id: str, target_stock: int) -> Dict[str, Any]:
    """
    Actualiza SOLO el stock propio/selling_address.

    Descubrimiento confirmado 2026-05-26:
    1) GET /user-products/{user_product_id}/stock
    2) leer header x-version
    3) PUT /user-products/{user_product_id}/stock/type/selling_address
       Header: X-Version: <x-version>
       Payload: {"quantity": target_stock}
    4) respuesta OK esperada: 204.

    No se envía ni se toca meli_facility/FULL.
    """
    before_response = ml_get_user_product_stock_response(user_product_id)
    before = before_response.json()
    x_version = before_response.headers.get("x-version") or before_response.headers.get("X-Version")
    if not x_version:
        raise Exception(
            "ML ERROR user-product stock: GET /stock no devolvió header x-version; "
            f"user_product_id={user_product_id} headers={dict(before_response.headers)}"
        )

    locations = before.get("locations") or []
    selling_before = None
    meli_facility_before = None

    if isinstance(locations, list):
        for loc in locations:
            if not isinstance(loc, dict):
                continue
            if loc.get("type") == "selling_address":
                selling_before = loc.get("quantity")
            elif loc.get("type") == "meli_facility":
                meli_facility_before = loc.get("quantity")

    url = f"https://api.mercadolibre.com/user-products/{user_product_id}/stock/type/selling_address"
    payload = {"quantity": int(target_stock)}

    # ML exige lock optimista vía X-Version leído en el GET inmediatamente anterior.
    r = ml_request("PUT", url, json=payload, timeout=45, extra_headers={"X-Version": str(x_version)})

    if r.status_code not in (200, 201, 204):
        raise Exception(
            "ML ERROR user-product selling_address stock "
            f"PUT {r.status_code}: {r.text[:1000]} | "
            f"user_product_id={user_product_id} x_version={x_version} payload={payload}"
        )

    after = None
    try:
        after = ml_get_user_product_stock(user_product_id)
    except Exception as e:
        after = {"error_reading_after": str(e)}

    return {
        "ok": True,
        "user_product_id": user_product_id,
        "target_selling_address_stock": int(target_stock),
        "selling_address_before": selling_before,
        "meli_facility_before": meli_facility_before,
        "x_version_sent": str(x_version),
        "payload_sent": payload,
        "url": url,
        "response_status_code": r.status_code,
        "response_preview": ml_response_preview(r, max_chars=1500),
        "after": after,
        "note": "Se actualizó solo selling_address con X-Version; no se envió ni tocó meli_facility/FULL.",
    }


def actualizar_ml_desde_sync_job(job: Dict[str, Any]) -> bool:
    """
    Actualiza stock/precio en Mercado Libre desde sync_jobs.

    Cambio 0.1.31:
    - Stock ML se actualiza vía /user-products/{user_product_id}/stock sobre location selling_address.
    - No se toca meli_facility/FULL.
    - Precio sigue por /items/{id} o /items/{id}/variations/{variation_id}.
    """
    listing_id = str(job.get("listing_id") or job.get("external_product_id") or "").strip()
    variant_id_raw = job.get("variant_id") or job.get("external_variant_id")
    variant_id = str(variant_id_raw).strip() if variant_id_raw not in [None, "", 0, "0"] else ""

    if not listing_id:
        raise Exception("ML listing_id vacío")

    sync_stock = bool(job.get("sync_stock", True))
    sync_price = bool(job.get("sync_price", False))

    actions = []

    # 1) Stock: usar user-products stock para separar selling_address de meli_facility.
    if sync_stock:
        target_stock = int(job.get("target_stock") or 0)
        resolved = ml_resolve_user_product_id_for_job(job)
        stock_result = ml_update_user_product_selling_address_stock(resolved["user_product_id"], target_stock)
        actions.append({
            "action": "stock_selling_address_update",
            "listing_id": listing_id,
            "variant_id": variant_id or None,
            "resolved": {
                "user_product_id": resolved.get("user_product_id"),
                "inventory_id": resolved.get("inventory_id"),
                "logistic_type": resolved.get("logistic_type"),
                "listing_row_id": resolved.get("listing_row_id"),
            },
            "result": stock_result,
        })

    # 2) Precio: mantener endpoint clásico de item/variation.
    if sync_price:
        target_price = job.get("target_price")
        if target_price is not None:
            payload_price = {"price": float(target_price)}
            if variant_id:
                price_url = f"https://api.mercadolibre.com/items/{listing_id}/variations/{variant_id}"
            else:
                price_url = f"https://api.mercadolibre.com/items/{listing_id}"

            r_price = ml_request("PUT", price_url, json=payload_price, timeout=45)
            if r_price.status_code not in (200, 201):
                raise Exception(f"ML ERROR price update {r_price.status_code}: {r_price.text[:1000]}")
            actions.append({
                "action": "price_update",
                "url": price_url,
                "payload": payload_price,
                "status_code": r_price.status_code,
            })

    if not actions:
        raise Exception("Job ML sin payload")

    try:
        # Guardamos evidencia útil dentro del job si existe columna last_response; si no existe, no bloquea.
        actualizar_sync_job(job.get("id"), {"last_response": {"ml_actions": actions}})
    except Exception:
        pass

    return True

def procesar_sync_job(job: Dict[str, Any]) -> bool:
    job_id = job.get("id")
    marketplace = str(job.get("marketplace") or "").upper().strip()
    sku = job.get("sku")

    try:
        actualizar_sync_job(job_id, {"status": "processing"})

        if marketplace == "TN":
            actualizar_tn_desde_sync_job(job)
        elif marketplace == "ML":
            actualizar_ml_desde_sync_job(job)
        else:
            raise Exception(f"Marketplace no soportado aún por sync worker ERP: {marketplace}")

        actualizar_sync_job(job_id, {"status": "done", "last_error": None})
        print(f"SYNC OK {marketplace} | {sku} | stock={job.get('target_stock')} | price={job.get('target_price')}")
        return True

    except Exception as e:
        attempts = int(job.get("attempts") or 0) + 1
        max_attempts = int(job.get("max_attempts") or 5)
        status = "failed_retry" if attempts < max_attempts else "manual_review"
        actualizar_sync_job(job_id, {
            "status": status,
            "attempts": attempts,
            "last_error": str(e),
        })
        print(f"SYNC ERROR {marketplace} | {sku}: {e}")
        return False


def run_pending_sync_jobs(limit: int = 20) -> Dict[str, Any]:
    validar_sync_worker_config()
    jobs = buscar_sync_jobs(limit=limit)

    processed = 0
    ok = 0
    errors = 0
    items = []

    for job in jobs:
        processed += 1
        success = procesar_sync_job(job)
        if success:
            ok += 1
        else:
            errors += 1
        items.append({
            "id": job.get("id"),
            "marketplace": job.get("marketplace"),
            "sku": job.get("sku"),
            "target_stock": job.get("target_stock"),
            "target_price": job.get("target_price"),
            "ok": success,
        })

    return {
        "found": len(jobs),
        "processed": processed,
        "ok": ok,
        "errors": errors,
        "items": items,
        "tn_products_store_id_used": TN_PRODUCTS_STORE_ID,
    }


_erp_autosync_thread_started = False
_erp_autosync_lock = threading.Lock()


def erp_autosync_loop():
    """
    Worker operativo post EcommApp.

    Los pollers TN/ML capturan ventas y generan sync_jobs.
    Este loop procesa esos sync_jobs automáticamente para difundir stock/precio
    sin depender de ejecutar /erp/process_sync_jobs desde PowerShell.
    """
    ERP_AUTO_SYNC_STATUS["started_at"] = now_iso()
    time.sleep(max(0, int(ERP_AUTO_SYNC_START_DELAY_SECONDS or 60)))

    while True:
        try:
            if ERP_AUTO_SYNC_ENABLED and sb:
                acquired = _erp_autosync_lock.acquire(blocking=False)
                if not acquired:
                    ERP_AUTO_SYNC_STATUS["last_run_at"] = now_iso()
                    ERP_AUTO_SYNC_STATUS["last_ok"] = True
                    ERP_AUTO_SYNC_STATUS["last_error"] = "skipped: autosync ya estaba procesando"
                else:
                    try:
                        result = run_pending_sync_jobs(limit=ERP_AUTO_SYNC_LIMIT)
                        ERP_AUTO_SYNC_STATUS["runs"] += 1
                        ERP_AUTO_SYNC_STATUS["last_run_at"] = now_iso()
                        ERP_AUTO_SYNC_STATUS["last_result"] = result
                        ERP_AUTO_SYNC_STATUS["last_ok"] = bool(result.get("errors", 0) == 0)
                        ERP_AUTO_SYNC_STATUS["last_error"] = None if ERP_AUTO_SYNC_STATUS["last_ok"] else f"errors={result.get('errors')}"
                        print(
                            "ERP AUTOSYNC "
                            f"found={result.get('found')} processed={result.get('processed')} "
                            f"ok={result.get('ok')} errors={result.get('errors')}"
                        )
                    finally:
                        _erp_autosync_lock.release()
        except Exception as e:
            ERP_AUTO_SYNC_STATUS["runs"] += 1
            ERP_AUTO_SYNC_STATUS["errors"] += 1
            ERP_AUTO_SYNC_STATUS["last_run_at"] = now_iso()
            ERP_AUTO_SYNC_STATUS["last_ok"] = False
            ERP_AUTO_SYNC_STATUS["last_error"] = str(e)
            print("ERP AUTOSYNC ERROR:", str(e))

        time.sleep(max(30, int(ERP_AUTO_SYNC_INTERVAL_SECONDS or 120)))


def start_erp_autosync_once():
    global _erp_autosync_thread_started
    if _erp_autosync_thread_started:
        return
    _erp_autosync_thread_started = True

    if not ERP_AUTO_SYNC_ENABLED:
        print("ERP AUTOSYNC desactivado por ERP_AUTO_SYNC_ENABLED")
        return

    t = threading.Thread(target=erp_autosync_loop, daemon=True, name="planeta-casa-erp-autosync")
    t.start()
    print(
        "ERP AUTOSYNC iniciado "
        f"interval={ERP_AUTO_SYNC_INTERVAL_SECONDS}s "
        f"limit={ERP_AUTO_SYNC_LIMIT} "
        f"delay={ERP_AUTO_SYNC_START_DELAY_SECONDS}s"
    )


@router.get("/erp/auto/status")
def erp_auto_status_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return {
        "ok": True,
        "version": APP_VERSION,
        "autosync": dict(ERP_AUTO_SYNC_STATUS),
        "pollers": {
            "tn": {
                "enabled": TN_AUTO_POLL_ENABLED,
                "interval_seconds": TN_AUTO_POLL_INTERVAL_SECONDS,
                "limit": TN_AUTO_POLL_LIMIT,
                "lookback_minutes": TN_AUTO_POLL_LOOKBACK_MINUTES,
                "status": dict(TN_AUTO_POLL_STATUS),
            },
            "ml": {
                "enabled": ML_AUTO_POLL_ENABLED,
                "interval_seconds": ML_AUTO_POLL_INTERVAL_SECONDS,
                "limit": ML_AUTO_POLL_LIMIT,
                "scan_limit": ML_AUTO_POLL_SCAN_LIMIT,
                "status": dict(ML_AUTO_POLL_STATUS),
            },
        },
        "ids": {
            "tn_orders_store_id_used": TN_ORDERS_STORE_ID,
            "tn_products_store_id_used": TN_PRODUCTS_STORE_ID,
            "ml_user_id_set": bool(ML_USER_ID),
        },
        "configured": {
            "supabase": bool(sb),
            "tn_token": bool(TN_TOKEN),
            "ml_token_or_refresh": bool(ML_ACCESS_TOKEN or ML_REFRESH_TOKEN),
            "admin_token": bool(ADMIN_TOKEN),
            "whatsapp_admin": bool(HUMAN_NOTIFY_PHONE and WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
        },
    }


@router.post("/erp/process_sync_jobs", response_class=PlainTextResponse)
def process_sync_jobs_endpoint(
    limit: int = 20,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    try:
        result = run_pending_sync_jobs(limit=limit)
        return (
            "Procesamiento terminado.\n"
            f"Jobs encontrados: {result.get('found')}\n"
            f"Jobs procesados: {result.get('processed')}\n"
            f"OK: {result.get('ok')}\n"
            f"Con error: {result.get('errors')}\n"
            f"TN products_store_id usado: {result.get('tn_products_store_id_used')}\n"
        )
    except Exception as e:
        return f"ERROR procesando jobs desde erp_admin.py: {e}\n"




def parse_any_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        txt = str(value).strip()
        if not txt:
            return None
        if re.search(r"[+-]\d{4}$", txt):
            txt = txt[:-5] + txt[-5:-2] + ":" + txt[-2:]
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fmt_arg_datetime(value: Any) -> Optional[str]:
    dt = parse_any_datetime(value)
    if not dt:
        return None
    arg = dt.astimezone(timezone(timedelta(hours=-3)))
    return arg.strftime("%Y-%m-%d %H:%M:%S GMT-3")


def enrich_sync_job_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row or {})
    r["created_at_arg"] = fmt_arg_datetime(r.get("created_at"))
    r["updated_at_arg"] = fmt_arg_datetime(r.get("updated_at"))
    r["source_created_at_arg"] = fmt_arg_datetime(r.get("source_created_at"))
    if not r.get("source_channel"):
        r["source_channel"] = r.get("marketplace")
    if not r.get("source_order_number"):
        r["source_order_number"] = ""
    if not r.get("source_order_api_id"):
        r["source_order_api_id"] = ""
    return r

@router.get("/erp/sync_jobs")
def sync_jobs_endpoint(
    limit: int = 100,
    offset: int = 0,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    res = (
        sb.table("sync_jobs")
        .select("*", count="exact")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = res.data or []
    total = res.count if res.count is not None else len(rows)
    items = [enrich_sync_job_row(r) for r in rows]
    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < int(total or 0),
        "tn_products_store_id_used": TN_PRODUCTS_STORE_ID,
        "items": items,
    }


@router.get("/erp/sync_jobs/html", response_class=HTMLResponse)
def sync_jobs_html_endpoint(
    limit: int = 100,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    rows = (
        sb.table("sync_jobs")
        .select("*")
        .order("created_at", desc=True)
        .limit(max(1, min(int(limit or 100), 500)))
        .execute()
        .data
        or []
    )

    rows = [enrich_sync_job_row(r) for r in rows]

    def esc(v):
        return str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    trs = "".join(
        f"<tr>"
        f"<td>{esc(r.get('source_channel'))}</td>"
        f"<td>{esc(r.get('marketplace'))}</td>"
        f"<td>{esc(r.get('source_order_number'))}</td>"
        f"<td>{esc(r.get('source_order_api_id'))}</td>"
        f"<td>{esc(r.get('source_customer_name'))}</td>"
        f"<td>{esc(r.get('sku'))}</td>"
        f"<td>{esc(r.get('target_stock'))}</td>"
        f"<td>{esc(r.get('target_price'))}</td>"
        f"<td>{esc(r.get('status'))}</td>"
        f"<td>{esc(r.get('attempts'))}</td>"
        f"<td>{esc(r.get('source_shipping_id'))}</td>"
        f"<td>{esc(r.get('source_pack_id'))}</td>"
        f"<td>{esc(r.get('last_error'))}</td>"
        f"<td>{esc(r.get('created_at_arg') or r.get('created_at'))}</td>"
        f"</tr>"
        for r in rows
    )
    return f"""
    <html><head><meta charset='utf-8'><title>Sync jobs</title>
    <style>body{{font-family:Arial;margin:24px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px;font-size:13px;vertical-align:top}}th{{background:#f3efe4;text-align:left}}.muted{{color:#666}}</style>
    </head><body>
    <h2>Sync jobs</h2>
    <p class='muted'>Rutina activa en erp_admin.py. TN products_store_id usado: {esc(TN_PRODUCTS_STORE_ID)}. Horarios mostrados en Argentina GMT-3.</p>
    <form method='post' action='/erp/process_sync_jobs?token={esc(token or x_admin_token or "")}&limit=1'>
      <button type='submit'>Procesar 1 job</button>
    </form>
    <br>
    <table><thead><tr><th>Origen</th><th>Market</th><th>Orden visible</th><th>Orden API/interna</th><th>Cliente</th><th>SKU</th><th>Stock</th><th>Precio</th><th>Status</th><th>Attempts</th><th>Shipping</th><th>Pack</th><th>Error</th><th>Creado ARG</th></tr></thead><tbody>{trs}</tbody></table>
    </body></html>
    """


def tn_get_order(order_id: str) -> Dict[str, Any]:
    if not TN_ORDERS_STORE_ID or not TN_TOKEN:
        raise HTTPException(status_code=500, detail="Faltan TN_USER_ID/TN_STORE_ID o TN_TOKEN")

    url = f"https://api.tiendanube.com/v1/{TN_ORDERS_STORE_ID}/orders/{order_id}"

    try:
        r = requests.get(url, headers=tn_headers(), timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con TN: {str(e)}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TN GET order {r.status_code}: {r.text}")

    return r.json()


def tn_find_order_by_number(order_number: str) -> Dict[str, Any]:
    """
    Tienda Nube muestra un número visible tipo #656, pero la API /orders/{id}
    usa el ID interno. Este helper lista órdenes recientes y busca por number.
    """
    if not TN_ORDERS_STORE_ID or not TN_TOKEN:
        raise HTTPException(status_code=500, detail="Faltan TN_USER_ID/TN_STORE_ID o TN_TOKEN")

    target = str(order_number).strip().lstrip("#")
    url = f"https://api.tiendanube.com/v1/{TN_ORDERS_STORE_ID}/orders"

    # Traemos varias páginas recientes por si la orden está archivada/antigua.
    # TN suele paginar con page/per_page.
    last_error = None
    for page in range(1, 11):
        params = {
            "page": page,
            "per_page": 50,
        }

        try:
            r = requests.get(url, headers=tn_headers(), params=params, timeout=45)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error conectando con TN: {str(e)}")

        if r.status_code != 200:
            last_error = f"TN LIST orders {r.status_code}: {r.text}"
            break

        orders = r.json() or []
        if not orders:
            break

        for order in orders:
            visible_number = str(order.get("number") or "").strip().lstrip("#")
            order_id = str(order.get("id") or "").strip()

            if visible_number == target or order_id == target:
                # Si la lista ya trae todo, devolvemos eso. Si no, buscamos detalle por id.
                if order_id:
                    return tn_get_order(order_id)
                return order

    msg = f"No encontré orden TN con número visible #{target} en las últimas 500 órdenes"
    if last_error:
        msg += f". Último error: {last_error}"
    raise HTTPException(status_code=404, detail=msg)


def tn_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return value.get("es") or value.get("pt") or value.get("en") or next(iter(value.values()), "")
    return str(value)


def tn_extract_customer(order: Dict[str, Any]) -> Dict[str, Any]:
    customer = order.get("customer") or {}
    billing = order.get("billing_address") or {}
    shipping = order.get("shipping_address") or {}

    name = (
        customer.get("name")
        or order.get("contact_name")
        or billing.get("name")
        or shipping.get("name")
    )
    phone = (
        customer.get("phone")
        or order.get("contact_phone")
        or billing.get("phone")
        or shipping.get("phone")
    )

    return {
        "name": name,
        "phone": phone,
    }


def tn_extract_lines(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    products = order.get("products") or []
    lines = []

    for idx, p in enumerate(products, start=1):
        sku = (
            p.get("sku")
            or p.get("variant_sku")
            or (p.get("variant") or {}).get("sku")
            or p.get("barcode")
        )

        sku = norm_sku(sku)
        quantity = p.get("quantity") or p.get("qty") or 1
        price = p.get("price") or p.get("unit_price") or p.get("price_promotional")

        try:
            quantity = float(quantity)
        except Exception:
            quantity = 1.0

        try:
            unit_price = float(str(price).replace(",", ".")) if price is not None else None
        except Exception:
            unit_price = None

        lines.append({
            "line_index": idx,
            "sku": sku,
            "quantity": quantity,
            "unit_price": unit_price,
            "name": tn_text(p.get("name")),
            "raw": p,
        })

    return lines


def build_decrements_for_sold_item(sold_item: Dict[str, Any], quantity: float) -> Dict[str, Any]:
    components = get_bundle_components_by_bundle_id(sold_item["id"])
    is_bundle = bool(components)
    decrements = []

    if is_bundle:
        for row in components:
            comp = row.get("component")
            if not comp:
                raise HTTPException(status_code=400, detail=f"Componente inexistente en bundle_components id={row.get('id')}")
            qty = float(row["quantity"]) * float(quantity)
            decrements.append({
                "item": comp,
                "qty": qty,
                "notes": f"Venta combo {sold_item['sku']} x{quantity}. Componente {comp['sku']} x{qty}",
                "source_sku": sold_item["sku"],
            })
    else:
        decrements.append({
            "item": sold_item,
            "qty": quantity,
            "notes": f"Venta producto simple {sold_item['sku']} x{quantity}",
            "source_sku": sold_item["sku"],
        })

    return {
        "is_bundle": is_bundle,
        "decrements": decrements,
    }


def aggregate_decrements(decrements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped = {}

    for d in decrements:
        item = d["item"]
        item_id = item["id"]
        if item_id not in grouped:
            grouped[item_id] = {
                "item": item,
                "qty": 0,
                "notes": [],
                "source_skus": set(),
            }
        grouped[item_id]["qty"] += float(d.get("qty") or 0)
        grouped[item_id]["notes"].append(d.get("notes") or "")
        grouped[item_id]["source_skus"].add(d.get("source_sku") or "")

    out = []
    for g in grouped.values():
        g["source_skus"] = sorted([s for s in g["source_skus"] if s])
        g["notes"] = " | ".join([n for n in g["notes"] if n])
        out.append(g)

    out.sort(key=lambda x: x["item"].get("sku") or "")
    return out


def process_order_lines(
    channel: str,
    external_order_id: str,
    lines: List[Dict[str, Any]],
    raw_payload: Dict[str, Any],
    dry_run: bool = True,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    total: Optional[float] = None,
) -> Dict[str, Any]:
    channel = norm_sku(channel).upper()
    external_order_id = norm_sku(external_order_id)

    if not lines:
        raise HTTPException(status_code=400, detail="La orden no tiene productos/líneas")

    existing = (
        sb.table("orders")
        .select("*")
        .eq("channel", channel)
        .eq("external_order_id", external_order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "message": "La orden ya existía. No se descuenta stock otra vez.",
            "order": existing[0],
        }

    sold_lines = []
    all_decrements = []

    for line in lines:
        sku = norm_sku(line.get("sku"))
        if not sku:
            raise HTTPException(status_code=400, detail=f"Línea sin SKU en orden {external_order_id}")

        sold_item = get_item_by_sku(sku)
        if not sold_item:
            raise HTTPException(status_code=404, detail=f"No existe SKU vendido en inventory_items: {sku}")

        qty = float(line.get("quantity") or 1)
        if qty <= 0:
            raise HTTPException(status_code=400, detail=f"Cantidad inválida para SKU {sku}: {qty}")

        built = build_decrements_for_sold_item(sold_item, qty)

        sold_lines.append({
            "sku": sku,
            "quantity": qty,
            "unit_price": line.get("unit_price"),
            "name": line.get("name") or sold_item.get("name"),
            "inventory_item_id": sold_item["id"],
            "is_bundle": built["is_bundle"],
        })
        all_decrements.extend(built["decrements"])

    aggregated = aggregate_decrements(all_decrements)

    # Validación previa total antes de escribir nada.
    for d in aggregated:
        item = d.get("item")
        qty = int(d.get("qty") or 0)
        if not item or not item.get("id") or not item.get("sku"):
            raise HTTPException(status_code=400, detail="Venta cancelada: componente inválido o inexistente")
        if qty <= 0:
            raise HTTPException(status_code=400, detail=f"Venta cancelada: cantidad inválida para {item.get('sku')}")

    preview = []
    for d in aggregated:
        item = d["item"]
        old_stock = int(item.get("stock") or 0)
        qty = int(d["qty"])
        preview.append({
            "sku": item["sku"],
            "name": item.get("name"),
            "qty_decrement": qty,
            "old_stock": old_stock,
            "new_stock": old_stock - qty,
            "insufficient_stock": old_stock - qty < 0,
            "source_skus": d.get("source_skus") or [],
        })

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "duplicate": False,
            "channel": channel,
            "external_order_id": external_order_id,
            "sold_lines": sold_lines,
            "stock_preview": preview,
        }

    order_payload = dict(raw_payload or {})
    order_payload.update({
        "source": "erp_admin_external_order",
        "sold_lines": sold_lines,
    })

    order_result = safe_insert_order(channel, external_order_id, {
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "total": total,
        **order_payload,
    })

    if not order_result["inserted"]:
        return {
            "ok": True,
            "duplicate": True,
            "message": "La orden ya existía. No se descuenta stock otra vez.",
            "order": order_result["order"],
        }

    order = order_result["order"]
    order_id = order["id"]

    source_meta = build_sync_source_meta(
        channel=channel,
        external_order_id=external_order_id,
        raw_payload=raw_payload,
        customer_name=customer_name,
        customer_phone=customer_phone,
        erp_order_id=str(order_id),
    )

    for line in sold_lines:
        sb.table("order_items").insert({
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "inventory_item_id": line["inventory_item_id"],
            "sku": line["sku"],
            "quantity": line["quantity"],
            "unit_price": line.get("unit_price"),
            "created_at": now_iso(),
        }).execute()

    applied = []
    affected_bundle_ids = set()

    for d in aggregated:
        item = d["item"]
        qty = int(d["qty"])
        res = decrement_item_stock(
            item=item,
            qty_to_decrement=qty,
            channel=channel,
            reference_id=str(order_id),
            reference_type="order",
            notes=d["notes"],
            dry_run=False,
            source_meta=source_meta,
        )
        applied.append(res)

        for bundle in bundles_that_use_component(item["id"]):
            affected_bundle_ids.add(bundle["id"])

    for line in sold_lines:
        if line.get("is_bundle"):
            affected_bundle_ids.add(line["inventory_item_id"])

    affected_bundle_results = []
    if affected_bundle_ids:
        bundles_to_recalc = (
            sb.table("inventory_items")
            .select(q_inventory_base())
            .in_("id", list(affected_bundle_ids))
            .execute()
            .data
            or []
        )
        for bundle in sorted(bundles_to_recalc, key=lambda b: b.get("sku") or ""):
            affected_bundle_results.append(recalc_and_sync_bundle(bundle, dry_run=False, source_meta=source_meta))

    return {
        "ok": True,
        "dry_run": False,
        "duplicate": False,
        "order": order,
        "channel": channel,
        "external_order_id": external_order_id,
        "sold_lines": sold_lines,
        "stock_applied": applied,
        "affected_bundles": affected_bundle_results,
    }


@router.get("/admin/tn/orders/{order_id}/preview")
def preview_tn_order(
    order_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = tn_get_order(order_id)
    lines = tn_extract_lines(order)
    customer = tn_extract_customer(order)

    total = order.get("total") or order.get("subtotal")
    try:
        total = float(str(total).replace(",", ".")) if total is not None else None
    except Exception:
        total = None

    result = process_order_lines(
        channel="TN",
        external_order_id=str(order_id),
        lines=lines,
        raw_payload={
            "tn_order_id": str(order_id),
            "tn_order_number": order.get("number"),
            "tn_status": order.get("status"),
            "tn_payment_status": order.get("payment_status"),
            "tn_shipping_status": order.get("shipping_status"),
            "tn_raw": order,
        },
        dry_run=True,
        customer_name=customer.get("name"),
        customer_phone=customer.get("phone"),
        total=total,
    )

    result["tn_order"] = {
        "id": order.get("id"),
        "number": order.get("number"),
        "status": order.get("status"),
        "payment_status": order.get("payment_status"),
        "shipping_status": order.get("shipping_status"),
        "customer_name": customer.get("name"),
        "customer_phone": customer.get("phone"),
        "total": total,
    }
    return result


@router.post("/admin/tn/orders/{order_id}/apply")
def apply_tn_order(
    order_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = tn_get_order(order_id)
    lines = tn_extract_lines(order)
    customer = tn_extract_customer(order)

    total = order.get("total") or order.get("subtotal")
    try:
        total = float(str(total).replace(",", ".")) if total is not None else None
    except Exception:
        total = None

    return process_order_lines(
        channel="TN",
        external_order_id=str(order_id),
        lines=lines,
        raw_payload={
            "tn_order_id": str(order_id),
            "tn_order_number": order.get("number"),
            "tn_status": order.get("status"),
            "tn_payment_status": order.get("payment_status"),
            "tn_shipping_status": order.get("shipping_status"),
            "tn_raw": order,
        },
        dry_run=False,
        customer_name=customer.get("name"),
        customer_phone=customer.get("phone"),
        total=total,
    )



@router.get("/admin/tn/orders/by-number/{order_number}/preview")
def preview_tn_order_by_number(
    order_number: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = tn_find_order_by_number(order_number)
    internal_id = order.get("id")
    if not internal_id:
        raise HTTPException(status_code=404, detail=f"Encontré #{order_number}, pero no tiene id interno TN")

    # Reusa el endpoint lógico, pero sin hacer HTTP extra si ya tenemos el detalle.
    lines = tn_extract_lines(order)
    customer = tn_extract_customer(order)

    total = order.get("total") or order.get("subtotal")
    try:
        total = float(str(total).replace(",", ".")) if total is not None else None
    except Exception:
        total = None

    result = process_order_lines(
        channel="TN",
        external_order_id=str(internal_id),
        lines=lines,
        raw_payload={
            "tn_order_id": str(internal_id),
            "tn_order_number": order.get("number"),
            "tn_status": order.get("status"),
            "tn_payment_status": order.get("payment_status"),
            "tn_shipping_status": order.get("shipping_status"),
            "tn_raw": order,
        },
        dry_run=True,
        customer_name=customer.get("name"),
        customer_phone=customer.get("phone"),
        total=total,
    )

    result["tn_order"] = {
        "id": order.get("id"),
        "number": order.get("number"),
        "status": order.get("status"),
        "payment_status": order.get("payment_status"),
        "shipping_status": order.get("shipping_status"),
        "customer_name": customer.get("name"),
        "customer_phone": customer.get("phone"),
        "total": total,
    }
    return result


@router.post("/admin/tn/orders/by-number/{order_number}/apply")
def apply_tn_order_by_number(
    order_number: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = tn_find_order_by_number(order_number)
    internal_id = order.get("id")
    if not internal_id:
        raise HTTPException(status_code=404, detail=f"Encontré #{order_number}, pero no tiene id interno TN")

    lines = tn_extract_lines(order)
    customer = tn_extract_customer(order)

    total = order.get("total") or order.get("subtotal")
    try:
        total = float(str(total).replace(",", ".")) if total is not None else None
    except Exception:
        total = None

    return process_order_lines(
        channel="TN",
        external_order_id=str(internal_id),
        lines=lines,
        raw_payload={
            "tn_order_id": str(internal_id),
            "tn_order_number": order.get("number"),
            "tn_status": order.get("status"),
            "tn_payment_status": order.get("payment_status"),
            "tn_shipping_status": order.get("shipping_status"),
            "tn_raw": order,
        },
        dry_run=False,
        customer_name=customer.get("name"),
        customer_phone=customer.get("phone"),
        total=total,
    )


@router.get("/admin/tn/debug/config")
def debug_tn_config(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    masked = None
    if TN_TOKEN:
        masked = {
            "length": len(TN_TOKEN),
            "start": TN_TOKEN[:4],
            "end": TN_TOKEN[-4:],
        }
    return {
        "ok": True,
        "tn_store_id_set": bool(TN_STORE_ID),
        "tn_store_id_value": TN_STORE_ID,
        "tn_user_id_set": bool(TN_USER_ID),
        "tn_user_id_value": TN_USER_ID,
        "tn_orders_store_id_used": TN_ORDERS_STORE_ID,
        "tn_token": masked,
    }


@router.get("/admin/tn/debug/store")
def debug_tn_store(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    if not TN_ORDERS_STORE_ID or not TN_TOKEN:
        raise HTTPException(status_code=500, detail="Faltan TN_USER_ID/TN_STORE_ID o TN_TOKEN")

    url = f"https://api.tiendanube.com/v1/{TN_ORDERS_STORE_ID}/store"
    try:
        r = requests.get(url, headers=tn_headers(), timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con TN store: {str(e)}")

    try:
        data = r.json()
    except Exception:
        data = r.text

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail={
            "status_code": r.status_code,
            "response": data,
            "orders_store_id_used": TN_ORDERS_STORE_ID,
        })

    return {
        "ok": True,
        "orders_store_id_used": TN_ORDERS_STORE_ID,
        "store": data,
    }


@router.get("/admin/tn/debug/orders")
def debug_tn_orders(
    page: int = 1,
    per_page: int = 5,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    if not TN_ORDERS_STORE_ID or not TN_TOKEN:
        raise HTTPException(status_code=500, detail="Faltan TN_USER_ID/TN_STORE_ID o TN_TOKEN")

    url = f"https://api.tiendanube.com/v1/{TN_ORDERS_STORE_ID}/orders"
    try:
        r = requests.get(url, headers=tn_headers(), params={"page": page, "per_page": per_page}, timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con TN orders: {str(e)}")

    try:
        data = r.json()
    except Exception:
        data = r.text

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail={
            "status_code": r.status_code,
            "response": data,
            "orders_store_id_used": TN_ORDERS_STORE_ID,
        })

    return {
        "ok": True,
        "orders_store_id_used": TN_ORDERS_STORE_ID,
        "page": page,
        "per_page": per_page,
        "total_returned": len(data) if isinstance(data, list) else None,
        "items": data,
    }


@router.post("/admin/tn/orders/poll")
def poll_tn_orders_endpoint(
    limit: int = 10,
    dry_run: bool = False,
    notify: bool = True,
    apply_existing: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return poll_tn_orders_once(
        limit=limit,
        dry_run=dry_run,
        notify=notify,
        apply_existing=apply_existing,
        auto_mode=False,
    )


def is_test_order_row(row: Dict[str, Any]) -> bool:
    """Filtra pruebas/manuales accidentales de las pantallas de ventas reales."""
    raw = row.get("raw_data") or {}
    txt_parts = [
        row.get("external_order_id"),
        row.get("customer_name"),
        row.get("customer_phone"),
        row.get("status"),
    ]
    if isinstance(raw, dict):
        txt_parts += [
            raw.get("source"),
            raw.get("note"),
            raw.get("tn_order_number"),
            raw.get("ml_order_id"),
        ]
    txt = normalizar(" ".join([str(x or "") for x in txt_parts]))
    bad_tokens = [
        "test",
        "prueba",
        "dry run",
        "dry_run",
        "manual test",
        "dummy",
    ]
    return any(t in txt for t in bad_tokens)


def fetch_recent_order_rows(channel: str, limit: int, offset: int, exclude_tests: bool = True) -> Dict[str, Any]:
    """
    Devuelve filas de orders ya filtradas, con paginación lógica.
    Para esta etapa trae una ventana amplia y pagina en Python: más simple y evita
    que una venta ML partida por pack/envío rompa el paginado visual.
    """
    hard_cap = 5000
    rows = (
        sb.table("orders")
        .select("*")
        .eq("channel", channel)
        .order("created_at", desc=True)
        .limit(hard_cap)
        .execute()
        .data
        or []
    )
    if exclude_tests:
        rows = [r for r in rows if not is_test_order_row(r)]
    return {
        "rows": rows,
        "hard_cap": hard_cap,
    }


def row_raw_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("raw_data") or {}
    return raw if isinstance(raw, dict) else {}


def recent_order_search_blob(item: Dict[str, Any]) -> str:
    parts = []
    def add(v):
        if v is None:
            return
        if isinstance(v, (list, tuple, set)):
            for x in v:
                add(x)
        elif isinstance(v, dict):
            for x in v.values():
                add(x)
        else:
            parts.append(str(v))
    add(item.get("id"))
    add(item.get("channel"))
    add(item.get("external_order_id"))
    add(item.get("group_label"))
    add(item.get("sale_number"))
    add(item.get("ml_sale_number"))
    add(item.get("ml_pack_id"))
    add(item.get("ml_shipping_id"))
    add(item.get("tn_order_number"))
    add(item.get("customer_name"))
    add(item.get("customer_phone"))
    add(item.get("status"))
    add(item.get("ml_status"))
    add(item.get("tn_status"))
    add(item.get("tn_payment_status"))
    add(item.get("shipping_option"))
    add(item.get("tn_shipping_status"))
    add(item.get("child_order_ids"))
    for line in item.get("sold_lines") or []:
        add(line.get("sku"))
        add(line.get("name"))
        add(line.get("title"))
        add(line.get("product_name"))
    for mov in item.get("stock_movements") or []:
        add(mov.get("sku"))
        add(mov.get("notes"))
    return normalizar(" ".join(parts))


def filter_recent_items(items: List[Dict[str, Any]], q: Optional[str]) -> List[Dict[str, Any]]:
    query = normalizar(q or "")
    if not query:
        return items
    terms = [t for t in query.split() if t]
    if not terms:
        return items
    out = []
    for item in items:
        blob = recent_order_search_blob(item)
        if all(t in blob for t in terms):
            out.append(item)
    return out


_ML_SHIPMENT_CACHE: Dict[str, Dict[str, Any]] = {}


def ml_get_shipment_info_safe(shipping_id: Any) -> Dict[str, Any]:
    sid = str(shipping_id or "").strip()
    if not sid:
        return {}
    if sid in _ML_SHIPMENT_CACHE:
        return _ML_SHIPMENT_CACHE[sid]
    try:
        r = ml_request("GET", f"https://api.mercadolibre.com/shipments/{sid}", timeout=30)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                _ML_SHIPMENT_CACHE[sid] = data
                return data
        _ML_SHIPMENT_CACHE[sid] = {"_error": f"ML shipment {r.status_code}: {r.text[:250]}"}
        return _ML_SHIPMENT_CACHE[sid]
    except Exception as e:
        _ML_SHIPMENT_CACHE[sid] = {"_error": str(e)[:250]}
        return _ML_SHIPMENT_CACHE[sid]


def ml_deep_collect_values(obj: Any, keys: List[str], max_values: int = 30) -> List[Any]:
    """Recolecta valores candidatos dentro de respuestas crudas ML/shipment."""
    found = []
    wanted = {str(k).lower() for k in keys}

    def walk(x: Any):
        if len(found) >= max_values:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in wanted and v not in [None, "", 0, "0"]:
                    found.append(v)
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found


def ml_clean_id_candidate(value: Any) -> Optional[str]:
    if value in [None, "", 0, "0"]:
        return None
    txt = str(value).strip().replace("#", "")
    if not txt:
        return None
    # Preferimos ids numéricos largos de ML; descartamos textos de estado/modo.
    digits = re.sub(r"\D+", "", txt)
    if len(digits) >= 8:
        return digits
    return txt if len(txt) >= 8 else None


def ml_detect_sale_number_from_row(row: Dict[str, Any]) -> Optional[str]:
    raw = row_raw_dict(row)
    ml_raw = raw.get("ml_raw") or {}
    if not isinstance(ml_raw, dict):
        ml_raw = {}
    candidates = [
        raw.get("ml_sale_number"),
        raw.get("ml_pack_id"),
        ml_raw.get("pack_id"),
        ml_raw.get("sale_id"),
        ml_raw.get("purchase_id"),
    ]
    candidates += ml_deep_collect_values(raw, ["pack_id", "sale_id", "purchase_id", "sale_number", "purchase_number"])
    for p in ml_raw.get("payments") or []:
        if isinstance(p, dict):
            # En algunas respuestas ML el número visible queda como pack/compra, no como order_id API.
            candidates += [p.get("pack_id"), p.get("sale_id"), p.get("purchase_id"), p.get("operation_id")]
    for c in candidates:
        cleaned = ml_clean_id_candidate(c)
        if cleaned:
            return cleaned
    return None


def ml_detect_sale_number_from_shipment(shipment: Dict[str, Any], child_order_ids: Optional[List[str]] = None) -> Optional[str]:
    if not isinstance(shipment, dict) or not shipment:
        return None
    child_ids = {str(x) for x in (child_order_ids or []) if x is not None}
    candidates = []
    candidates += [
        shipment.get("pack_id"),
        shipment.get("sale_id"),
        shipment.get("purchase_id"),
        shipment.get("order_pack_id"),
        shipment.get("order_id"),
        shipment.get("external_reference"),
    ]
    candidates += ml_deep_collect_values(
        shipment,
        ["pack_id", "sale_id", "purchase_id", "order_pack_id", "sale_number", "purchase_number", "external_reference", "order_id"],
    )
    for c in candidates:
        cleaned = ml_clean_id_candidate(c)
        if cleaned and cleaned not in child_ids:
            return cleaned
    return None


def get_stock_lines_for_order_id(order_id: str) -> List[Dict[str, Any]]:
    movs = (
        sb.table("stock_movements")
        .select("*")
        .eq("reference_id", order_id)
        .order("created_at", desc=False)
        .limit(100)
        .execute()
        .data
        or []
    )
    return [{
        "sku": m.get("sku"),
        "quantity": m.get("quantity"),
        "previous_stock": m.get("previous_stock"),
        "new_stock": m.get("new_stock"),
        "notes": m.get("notes"),
    } for m in movs]


def ml_group_key_for_recent_row(row: Dict[str, Any]) -> str:
    raw = row_raw_dict(row)
    ml_raw = raw.get("ml_raw") or {}
    if not isinstance(ml_raw, dict):
        ml_raw = {}
    sale_number = ml_detect_sale_number_from_row(row)
    if sale_number:
        return f"sale:{sale_number}"
    shipping_id = raw.get("ml_shipping_id")
    if not shipping_id:
        shipping = ml_raw.get("shipping") or {}
        if isinstance(shipping, dict):
            shipping_id = shipping.get("id")
    pack_id = raw.get("ml_pack_id") or ml_raw.get("pack_id")
    if pack_id:
        return f"pack:{pack_id}"
    if shipping_id:
        return f"shipping:{shipping_id}"
    return f"order:{row.get('external_order_id') or row.get('id')}"


def ml_shipping_labels_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row_raw_dict(row)
    ml_raw = raw.get("ml_raw") or {}
    shipping = (ml_raw.get("shipping") or {}) if isinstance(ml_raw, dict) else {}
    if not isinstance(shipping, dict):
        shipping = {}
    shipping_id = raw.get("ml_shipping_id") or shipping.get("id")
    shipment = ml_get_shipment_info_safe(shipping_id) if shipping_id else {}
    shipping_option = shipment.get("shipping_option") if isinstance(shipment, dict) else {}
    if not isinstance(shipping_option, dict):
        shipping_option = {}

    logistic_type = (
        shipment.get("logistic_type")
        or shipping_option.get("logistic_type")
        or shipping.get("logistic_type")
        or raw.get("ml_shipping_logistic_type")
    )
    mode = shipment.get("mode") or shipping.get("mode") or raw.get("ml_shipping_mode")
    tags = shipment.get("tags") or shipping.get("tags") or raw.get("ml_shipping_tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    sale_number_from_shipment = ml_detect_sale_number_from_shipment(shipment)
    return {
        "ml_shipping_id": shipping_id,
        "ml_shipping_logistic_type": logistic_type,
        "ml_shipping_mode": mode,
        "ml_shipping_tags": tags,
        "ml_shipping_status": shipment.get("status") or shipping.get("status"),
        "ml_shipment_error": shipment.get("_error"),
        "ml_sale_number_from_shipment": sale_number_from_shipment,
    }


def build_recent_ml_items(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    order_keys: List[str] = []
    for row in rows:
        raw = row_raw_dict(row)
        ml_raw = raw.get("ml_raw") or {}
        key = ml_group_key_for_recent_row(row)
        if key not in groups:
            labels = ml_shipping_labels_from_row(row)
            prefix, value = key.split(":", 1) if ":" in key else ("order", key)
            sale_number = ml_detect_sale_number_from_row(row)
            if prefix in ["sale", "pack"]:
                sale_number = value
                group_label = f"#{value}"
            elif prefix == "shipping":
                group_label = f"Envío ML {value}"
            else:
                group_label = f"#{row.get('external_order_id') or ''}"
            groups[key] = {
                "id": row.get("id"),
                "channel": "ML",
                "group_key": key,
                "group_label": group_label,
                "sale_number": sale_number,
                "ml_sale_number": sale_number,
                "ml_pack_id": raw.get("ml_pack_id") or (ml_raw.get("pack_id") if isinstance(ml_raw, dict) else None),
                "external_order_id": row.get("external_order_id"),
                "child_order_ids": [],
                "created_at": row.get("created_at"),
                "display_created_at": raw.get("ml_date_created") or raw.get("ml_date_closed") or (ml_raw.get("date_created") if isinstance(ml_raw, dict) else None) or row.get("created_at"),
                "customer_name": row.get("customer_name"),
                "customer_phone": row.get("customer_phone"),
                "total": 0,
                "status": row.get("status"),
                "ml_status": raw.get("ml_status") or (ml_raw.get("status") if isinstance(ml_raw, dict) else None),
                "ml_date_created": raw.get("ml_date_created") or (ml_raw.get("date_created") if isinstance(ml_raw, dict) else None),
                "whatsapp_notified": bool(raw.get("ml_whatsapp_notified_at")),
                "whatsapp_notified_at": raw.get("ml_whatsapp_notified_at"),
                "sold_lines": [],
                "stock_movements": [],
                **labels,
            }
            if labels.get("ml_sale_number_from_shipment") and not groups[key].get("sale_number"):
                groups[key]["sale_number"] = labels.get("ml_sale_number_from_shipment")
                groups[key]["ml_sale_number"] = labels.get("ml_sale_number_from_shipment")
                groups[key]["group_label"] = f"#{labels.get('ml_sale_number_from_shipment')}"
            order_keys.append(key)
        g = groups[key]
        oid = str(row.get("external_order_id") or "").strip()
        if oid and oid not in g["child_order_ids"]:
            g["child_order_ids"].append(oid)
        if g.get("ml_shipping_id") and (not g.get("sale_number") or str(g.get("group_label") or "").startswith("Envío ML")):
            shipment = ml_get_shipment_info_safe(g.get("ml_shipping_id"))
            sale_from_shipment = ml_detect_sale_number_from_shipment(shipment, g.get("child_order_ids") or [])
            if sale_from_shipment:
                g["sale_number"] = sale_from_shipment
                g["ml_sale_number"] = sale_from_shipment
                g["group_label"] = f"#{sale_from_shipment}"
        try:
            g["total"] = float(g.get("total") or 0) + float(row.get("total") or 0)
        except Exception:
            pass
        if row.get("created_at") and (not g.get("created_at") or str(row.get("created_at")) < str(g.get("created_at"))):
            g["created_at"] = row.get("created_at")
        if not g.get("customer_name") and row.get("customer_name"):
            g["customer_name"] = row.get("customer_name")
        if not g.get("customer_phone") and row.get("customer_phone"):
            g["customer_phone"] = row.get("customer_phone")
        row_sale_number = ml_detect_sale_number_from_row(row)
        if row_sale_number and not g.get("sale_number"):
            g["sale_number"] = row_sale_number
            g["ml_sale_number"] = row_sale_number
            if str(g.get("group_key") or "").startswith("shipping:"):
                g["group_label"] = f"#{row_sale_number}"
        for line in raw.get("sold_lines") or []:
            g["sold_lines"].append(line)
        g["stock_movements"].extend(get_stock_lines_for_order_id(row.get("id")))
    return [groups[k] for k in order_keys]


def build_recent_tn_items(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for row in rows:
        raw = row_raw_dict(row)
        tn_raw = raw.get("tn_raw") or {}
        if not isinstance(tn_raw, dict):
            tn_raw = {}
        shipping_option = raw.get("tn_shipping_option") or tn_raw.get("shipping_option")
        if isinstance(shipping_option, dict):
            shipping_option = shipping_option.get("name") or shipping_option.get("code") or str(shipping_option)
        items.append({
            "id": row.get("id"),
            "channel": "TN",
            "group_key": f"order:{row.get('external_order_id') or row.get('id')}",
            "external_order_id": row.get("external_order_id"),
            "tn_order_number": raw.get("tn_order_number") or tn_raw.get("number"),
            "created_at": row.get("created_at"),
            "display_created_at": raw.get("tn_created_at") or raw.get("tn_paid_at") or raw.get("tn_updated_at") or tn_raw.get("created_at") or row.get("created_at"),
            "customer_name": row.get("customer_name"),
            "customer_phone": row.get("customer_phone"),
            "total": row.get("total"),
            "status": row.get("status"),
            "tn_status": raw.get("tn_status") or tn_raw.get("status"),
            "tn_payment_status": raw.get("tn_payment_status") or tn_raw.get("payment_status"),
            "tn_shipping_status": raw.get("tn_shipping_status") or tn_raw.get("shipping_status"),
            "shipping_option": shipping_option,
            "whatsapp_notified": bool(raw.get("tn_whatsapp_notified_at")),
            "whatsapp_notified_at": raw.get("tn_whatsapp_notified_at"),
            "sold_lines": raw.get("sold_lines") or [],
            "stock_movements": get_stock_lines_for_order_id(row.get("id")),
        })
    def _tn_sort_value(item):
        v = item.get("tn_order_number") or item.get("external_order_id") or "0"
        digits = re.sub(r"\D+", "", str(v))
        try:
            return int(digits or 0)
        except Exception:
            return 0
    items.sort(key=_tn_sort_value, reverse=True)
    return items


def paginate_items(items: List[Dict[str, Any]], limit: int, offset: int) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 20), 50))
    offset = max(0, int(offset or 0))
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "items": page,
    }


@router.get("/admin/tn/orders/recent")
def recent_tn_sales(
    limit: int = 20,
    offset: int = 0,
    exclude_tests: bool = True,
    q: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    fetched = fetch_recent_order_rows("TN", limit=limit, offset=offset, exclude_tests=exclude_tests)
    items = build_recent_tn_items(fetched["rows"])
    items = filter_recent_items(items, q)
    out = paginate_items(items, limit=limit, offset=offset)
    out["exclude_tests"] = exclude_tests
    return out


@router.post("/admin/tn/orders/{order_id}/notify")
def notify_tn_order_endpoint(
    order_id: str,
    force: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    row = get_order_row("TN", order_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No existe orden TN aplicada: {order_id}")

    raw = row.get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {}
    tn_order = raw.get("tn_raw") or {"id": order_id, "number": raw.get("tn_order_number")}

    if order_whatsapp_notified(row) and not force:
        return {"ok": True, "skipped": True, "reason": "Ya estaba notificada"}

    fake_result = {
        "order": row,
        "external_order_id": order_id,
        "sold_lines": raw.get("sold_lines") or [],
        "stock_applied": [],
    }

    # Para el reaviso, reconstruimos stock aplicado desde movimientos.
    movs = (
        sb.table("stock_movements")
        .select("*")
        .eq("reference_id", row.get("id"))
        .order("created_at", desc=False)
        .limit(50)
        .execute()
        .data
        or []
    )
    fake_result["stock_applied"] = [{
        "sku": m.get("sku"),
        "old_stock": m.get("previous_stock"),
        "new_stock": m.get("new_stock"),
        "qty_decrement": abs(int(m.get("quantity") or 0)),
    } for m in movs]

    msg = build_tn_sale_whatsapp_message(fake_result, tn_order)
    notify_result = send_whatsapp_admin(msg)
    if notify_result.get("ok"):
        mark_order_whatsapp_notified(row.get("id"), notify_result)

    return {"ok": bool(notify_result.get("ok")), "forced": force, "notify_result": notify_result}


# ============================================================
# MERCADO LIBRE - LECTURA Y PROCESO DE ÓRDENES
# ============================================================

def ml_headers():
    return {
        "Authorization": f"Bearer {ML_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def ml_can_refresh() -> bool:
    return bool(ML_REFRESH_TOKEN and ML_CLIENT_ID and ML_CLIENT_SECRET)


def ml_validate_config(require_user: bool = True):
    missing = []
    if not ML_ACCESS_TOKEN and not ml_can_refresh():
        missing.append("ML_ACCESS_TOKEN o ML_REFRESH_TOKEN+ML_CLIENT_ID+ML_CLIENT_SECRET")
    if require_user and not ML_USER_ID:
        missing.append("ML_USER_ID")
    if missing:
        raise HTTPException(status_code=500, detail="Faltan variables ML: " + ", ".join(missing))


def ml_refresh_access_token() -> Dict[str, Any]:
    """
    Refresca ML_ACCESS_TOKEN en memoria usando ML_REFRESH_TOKEN.
    No puede escribir variables de Render. Si ML devuelve un refresh_token nuevo,
    se usa en esta instancia hasta el próximo redeploy/restart.
    """
    global ML_ACCESS_TOKEN, ML_REFRESH_TOKEN

    if not ML_REFRESH_TOKEN:
        raise HTTPException(status_code=500, detail="ML_REFRESH_TOKEN no configurado")
    if not ML_CLIENT_ID:
        raise HTTPException(status_code=500, detail="ML_CLIENT_ID/ML_APP_ID no configurado")
    if not ML_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="ML_CLIENT_SECRET/ML_APP_SECRET no configurado")

    url = "https://api.mercadolibre.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": ML_REFRESH_TOKEN,
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
    }

    try:
        r = requests.post(url, data=payload, headers=headers, timeout=45)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error refrescando token ML: {str(e)}")

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:2000]}

    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail={
            "message": "ML refresh token falló",
            "status_code": r.status_code,
            "response": body,
            "runtime": {
                "client_id_set": bool(ML_CLIENT_ID),
                "client_secret_set": bool(ML_CLIENT_SECRET),
                "refresh_token_set": bool(ML_REFRESH_TOKEN),
            },
        })

    access = body.get("access_token")
    if not access:
        raise HTTPException(status_code=502, detail={"message": "ML no devolvió access_token", "response": body})

    ML_ACCESS_TOKEN = access
    if body.get("refresh_token"):
        ML_REFRESH_TOKEN = body.get("refresh_token")

    return {
        "ok": True,
        "access_token": mask_secret(ML_ACCESS_TOKEN),
        "refresh_token": mask_secret(ML_REFRESH_TOKEN),
        "expires_in": body.get("expires_in"),
        "scope": body.get("scope"),
        "user_id": body.get("user_id"),
        "note": "Token actualizado en memoria de esta instancia Render. Si ML rotó refresh_token, actualizar variable en Render cuando corresponda.",
    }


def ml_request(
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    timeout: int = 45,
    extra_headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    ml_validate_config(require_user=False)
    method = (method or "GET").upper().strip()

    def build_headers() -> Dict[str, str]:
        headers = ml_headers()
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def do_request() -> requests.Response:
        return requests.request(method, url, headers=build_headers(), params=params or {}, json=json, data=data, timeout=timeout)

    try:
        r = do_request()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con ML: {str(e)}")

    if r.status_code == 401 and ml_can_refresh():
        ml_refresh_access_token()
        try:
            r = do_request()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error conectando con ML luego de refresh: {str(e)}")

    return r


def ml_get_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 45) -> Any:
    r = ml_request("GET", url, params=params or {}, timeout=timeout)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ML GET {r.status_code}: {r.text}")
    return r.json()


def ml_response_preview(r: requests.Response, max_chars: int = 5000) -> Dict[str, Any]:
    """
    Devuelve una vista segura/corta de una respuesta ML para debug.
    Incluye headers de respuesta no sensibles porque ML usa X-Version para stock.
    """
    content_type = r.headers.get("content-type", "")
    body_text = r.text or ""
    parsed: Any = None
    is_json = False

    safe_headers: Dict[str, str] = {}
    for hk, hv in (r.headers or {}).items():
        lk = str(hk).lower()
        if lk in {"authorization", "cookie", "set-cookie", "x-meli-session-id"}:
            continue
        # Guardamos headers útiles para debugging/versionado, sin secretos.
        if (
            "version" in lk
            or lk in {"etag", "last-modified", "date", "content-type", "x-request-id", "x-correlation-id"}
            or lk.startswith("x-")
        ):
            safe_headers[str(hk)] = str(hv)[:500]

    base = {
        "status_code": r.status_code,
        "ok": 200 <= r.status_code < 300,
        "content_type": content_type,
        "headers": safe_headers,
    }

    if "json" in content_type.lower() or body_text.strip().startswith(("{", "[")):
        try:
            parsed = r.json()
            is_json = True
        except Exception:
            parsed = None

    if is_json:
        base["json"] = parsed
        return base

    base["text"] = body_text[:max_chars]
    base["truncated"] = len(body_text) > max_chars
    return base


def ml_debug_get(path_or_url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 45) -> Dict[str, Any]:
    """
    GET ML de diagnóstico: no falla si ML devuelve 4xx/5xx.
    Sirve para descubrir endpoints reales de stock sin romper el flujo.
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = "https://api.mercadolibre.com" + path_or_url

    try:
        r = ml_request("GET", url, params=params or {}, timeout=timeout)
        out = ml_response_preview(r)
        out["url"] = url
        if params:
            out["params"] = params
        return out
    except HTTPException as e:
        return {
            "url": url,
            "params": params or {},
            "ok": False,
            "exception": "HTTPException",
            "detail": e.detail,
        }
    except Exception as e:
        return {
            "url": url,
            "params": params or {},
            "ok": False,
            "exception": type(e).__name__,
            "detail": str(e),
        }


def ml_debug_write(
    path_or_url: str,
    method: str,
    payload: Dict[str, Any],
    timeout: int = 45,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Request ML de diagnóstico para escritura no-op.
    No falla ante 4xx/5xx: devuelve status/body para descubrir ruta/método real.
    Permite probar headers extra como X-Version sin alterar el flujo general.
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = "https://api.mercadolibre.com" + path_or_url

    method_clean = method.upper().strip()
    headers_extra = dict(extra_headers or {})

    try:
        if headers_extra:
            headers = ml_headers()
            headers.update(headers_extra)
            try:
                r = requests.request(method_clean, url, headers=headers, json=payload, timeout=timeout)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Error conectando con ML: {str(e)}")

            if r.status_code == 401 and ml_can_refresh():
                ml_refresh_access_token()
                headers = ml_headers()
                headers.update(headers_extra)
                try:
                    r = requests.request(method_clean, url, headers=headers, json=payload, timeout=timeout)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"Error conectando con ML luego de refresh: {str(e)}")
        else:
            r = ml_request(method_clean, url, json=payload, timeout=timeout)

        out = ml_response_preview(r, max_chars=2500)
        out["url"] = url
        out["method"] = method_clean
        out["payload"] = payload
        out["extra_headers_sent"] = headers_extra
        return out
    except HTTPException as e:
        return {
            "url": url,
            "method": method_clean,
            "payload": payload,
            "extra_headers_sent": headers_extra,
            "ok": False,
            "exception": "HTTPException",
            "detail": e.detail,
        }
    except Exception as e:
        return {
            "url": url,
            "method": method_clean,
            "payload": payload,
            "extra_headers_sent": headers_extra,
            "ok": False,
            "exception": type(e).__name__,
            "detail": str(e),
        }


def ml_extract_location_quantity(stock_json: Dict[str, Any], location_type: str = "selling_address") -> Optional[int]:
    """Devuelve quantity de una location en /user-products/{id}/stock."""
    if not isinstance(stock_json, dict):
        return None
    for loc in stock_json.get("locations") or []:
        if isinstance(loc, dict) and loc.get("type") == location_type:
            try:
                return int(loc.get("quantity"))
            except Exception:
                return None
    return None


def build_ml_stock_write_candidates_debug(
    item_id: str,
    user_product_id: Optional[str] = None,
    quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Prueba candidatos de escritura de stock ML con cantidad actual.

    Es un test no-op: por defecto usa el quantity actual de selling_address,
    para que si alguna ruta funciona no cambie stock operativo.
    """
    item_id = str(item_id or "").strip()
    user_product_id = str(user_product_id or "").strip() or None
    if not item_id and not user_product_id:
        raise HTTPException(status_code=400, detail="Falta item_id o user_product_id")

    item_res = None
    item_json = {}
    if item_id:
        item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            user_product_id = user_product_id or item_json.get("user_product_id")

    if not user_product_id:
        raise HTTPException(status_code=400, detail="No pude resolver user_product_id")

    stock_before_res = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    stock_before_json = stock_before_res.get("json") or {}
    current_qty = ml_extract_location_quantity(stock_before_json, "selling_address")
    meli_qty = ml_extract_location_quantity(stock_before_json, "meli_facility")

    if quantity is None:
        if current_qty is None:
            raise HTTPException(status_code=400, detail="No pude leer quantity actual de selling_address")
        quantity_to_send = int(current_qty)
    else:
        quantity_to_send = int(quantity)

    payloads = {
        "quantity_only": {"quantity": quantity_to_send},
        "type_quantity": {"type": "selling_address", "quantity": quantity_to_send},
        "locations_array": {"locations": [{"type": "selling_address", "quantity": quantity_to_send}]},
        "available_quantity": {"available_quantity": quantity_to_send},
    }

    paths = {
        "up_stock_type_selling_address": f"/user-products/{user_product_id}/stock/type/selling_address",
        "up_stock_locations_selling_address": f"/user-products/{user_product_id}/stock/locations/selling_address",
        "up_stock_selling_address": f"/user-products/{user_product_id}/stock/selling_address",
        "up_locations_selling_address_stock": f"/user-products/{user_product_id}/locations/selling_address/stock",
        "up_stock_locations": f"/user-products/{user_product_id}/stock/locations",
    }

    methods = ["PUT", "PATCH", "POST"]
    results = []

    for path_name, path in paths.items():
        for payload_name, payload in payloads.items():
            for method in methods:
                res = ml_debug_write(path, method=method, payload=payload, timeout=60)
                results.append({
                    "path_name": path_name,
                    "payload_name": payload_name,
                    "method": method,
                    "ok": res.get("ok"),
                    "status_code": res.get("status_code"),
                    "url": res.get("url"),
                    "payload": payload,
                    "response": res,
                })

                # Si alguno funciona, frenamos para minimizar escrituras repetidas.
                if res.get("ok"):
                    stock_after = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
                    return {
                        "ok": True,
                        "version": APP_VERSION,
                        "mode": "noop_write_candidate_test",
                        "item_id": item_id,
                        "user_product_id": user_product_id,
                        "quantity_sent": quantity_to_send,
                        "selling_address_before": current_qty,
                        "meli_facility_before": meli_qty,
                        "stock_before": stock_before_res,
                        "first_success": results[-1],
                        "attempts": results,
                        "stock_after": stock_after,
                        "note": "Se frenó en el primer candidato OK. La cantidad enviada fue la misma que la actual para no cambiar stock.",
                    }

    stock_after = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    return {
        "ok": False,
        "version": APP_VERSION,
        "mode": "noop_write_candidate_test",
        "item_id": item_id,
        "user_product_id": user_product_id,
        "quantity_sent": quantity_to_send,
        "selling_address_before": current_qty,
        "meli_facility_before": meli_qty,
        "stock_before": stock_before_res,
        "attempts": results,
        "stock_after": stock_after,
        "note": "Ningún candidato devolvió 2xx. La cantidad enviada fue la misma que la actual para no cambiar stock.",
    }



def build_ml_stock_xversion_candidates_debug(
    item_id: str,
    user_product_id: Optional[str] = None,
    quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Segundo test no-op: MercadoLibre respondió "Missing X-Version header"
    para PUT /user-products/{id}/stock/type/selling_address.
    Probamos valores posibles de X-Version con la cantidad actual.
    """
    item_id = str(item_id or "").strip()
    user_product_id = str(user_product_id or "").strip() or None
    if not item_id and not user_product_id:
        raise HTTPException(status_code=400, detail="Falta item_id o user_product_id")

    if item_id:
        item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            user_product_id = user_product_id or item_json.get("user_product_id")

    if not user_product_id:
        raise HTTPException(status_code=400, detail="No pude resolver user_product_id")

    stock_before_res = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    stock_before_json = stock_before_res.get("json") or {}
    current_qty = ml_extract_location_quantity(stock_before_json, "selling_address")
    meli_qty = ml_extract_location_quantity(stock_before_json, "meli_facility")

    if quantity is None:
        if current_qty is None:
            raise HTTPException(status_code=400, detail="No pude leer quantity actual de selling_address")
        quantity_to_send = int(current_qty)
    else:
        quantity_to_send = int(quantity)

    path = f"/user-products/{user_product_id}/stock/type/selling_address"

    payloads = {
        "quantity_only": {"quantity": quantity_to_send},
        "type_quantity": {"type": "selling_address", "quantity": quantity_to_send},
        "locations_array": {"locations": [{"type": "selling_address", "quantity": quantity_to_send}]},
        "available_quantity": {"available_quantity": quantity_to_send},
    }

    # Integraly muestra columna "Stock (x-version)" y variable xVersion.
    # Probamos valores chicos y v2, porque en el DLL también aparece app_version=v2.
    x_versions = [
        "1",
        "2",
        "3",
        "v1",
        "v2",
        "2023-01-01",
        "2023-08-01",
        "2023-10-01",
    ]

    attempts = []
    for xver in x_versions:
        for payload_name, payload in payloads.items():
            res = ml_debug_write(
                path,
                method="PUT",
                payload=payload,
                timeout=60,
                extra_headers={"X-Version": xver},
            )
            attempts.append({
                "x_version": xver,
                "payload_name": payload_name,
                "method": "PUT",
                "ok": res.get("ok"),
                "status_code": res.get("status_code"),
                "url": res.get("url"),
                "payload": payload,
                "response": res,
            })

            if res.get("ok"):
                stock_after = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
                return {
                    "ok": True,
                    "version": APP_VERSION,
                    "mode": "noop_xversion_candidate_test",
                    "item_id": item_id,
                    "user_product_id": user_product_id,
                    "quantity_sent": quantity_to_send,
                    "selling_address_before": current_qty,
                    "meli_facility_before": meli_qty,
                    "stock_before": stock_before_res,
                    "first_success": attempts[-1],
                    "attempts": attempts,
                    "stock_after": stock_after,
                    "note": "Se frenó en el primer candidato OK. La cantidad enviada fue la misma que la actual para no cambiar stock.",
                }

    stock_after = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    return {
        "ok": False,
        "version": APP_VERSION,
        "mode": "noop_xversion_candidate_test",
        "item_id": item_id,
        "user_product_id": user_product_id,
        "quantity_sent": quantity_to_send,
        "selling_address_before": current_qty,
        "meli_facility_before": meli_qty,
        "stock_before": stock_before_res,
        "attempts": attempts,
        "stock_after": stock_after,
        "note": "Ningún X-Version probado devolvió 2xx. La cantidad enviada fue la misma que la actual para no cambiar stock.",
    }


@router.post("/admin/ml/debug/stock-write-xversion-candidates")
def admin_ml_debug_stock_write_xversion_candidates_endpoint(
    item_id: Optional[str] = Query(default=None),
    user_product_id: Optional[str] = Query(default=None),
    quantity: Optional[int] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)
    return build_ml_stock_xversion_candidates_debug(
        item_id=item_id or "",
        user_product_id=user_product_id,
        quantity=quantity,
    )


@router.post("/admin/ml/debug/stock-write-xversion-candidates-by-sku")
def admin_ml_debug_stock_write_xversion_candidates_by_sku_endpoint(
    sku: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)

    rows = ml_find_listing_rows_for_sku(sku, limit=20)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No encontré listing ML para SKU {sku}")

    row = rows[0]
    item_id = str(row.get("external_product_id") or row.get("external_full_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail=f"Listing ML sin item_id para SKU {sku}")

    raw = row.get("raw_data") or {}
    raw_item = raw.get("item") if isinstance(raw, dict) else None
    if not isinstance(raw_item, dict):
        raw_item = {}

    user_product_id = raw_item.get("user_product_id")
    if not user_product_id:
        item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            user_product_id = item_json.get("user_product_id")

    if not user_product_id:
        raise HTTPException(status_code=400, detail=f"No pude resolver user_product_id para SKU {sku}")

    return {
        "ok": True,
        "version": APP_VERSION,
        "sku": sku,
        "listing_row_id": row.get("id"),
        "external_product_id": item_id,
        "external_variant_id": row.get("external_variant_id"),
        "test": build_ml_stock_xversion_candidates_debug(
            item_id=item_id,
            user_product_id=user_product_id,
            quantity=None,
        ),
    }



def ml_extract_stock_debug_candidates(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrae del raw item los identificadores que podrían servir para rutas de stock ML:
    item id, user_product_id, inventory_id, logistic_type y variaciones.
    """
    if not isinstance(item, dict):
        item = {}

    shipping = item.get("shipping") or {}
    variations = item.get("variations") or []
    variation_candidates = []
    if isinstance(variations, list):
        for v in variations:
            if not isinstance(v, dict):
                continue
            variation_candidates.append({
                "id": v.get("id"),
                "available_quantity": v.get("available_quantity"),
                "inventory_id": v.get("inventory_id"),
                "attributes": v.get("attributes"),
                "seller_custom_field": v.get("seller_custom_field"),
            })

    return {
        "item_id": item.get("id"),
        "user_product_id": item.get("user_product_id"),
        "inventory_id": item.get("inventory_id"),
        "available_quantity": item.get("available_quantity"),
        "initial_quantity": item.get("initial_quantity"),
        "sold_quantity": item.get("sold_quantity"),
        "catalog_listing": item.get("catalog_listing"),
        "catalog_product_id": item.get("catalog_product_id"),
        "shipping": {
            "mode": shipping.get("mode"),
            "logistic_type": shipping.get("logistic_type"),
            "tags": shipping.get("tags"),
            "store_pick_up": shipping.get("store_pick_up"),
            "local_pick_up": shipping.get("local_pick_up"),
            "free_shipping": shipping.get("free_shipping"),
        },
        "variations": variation_candidates,
    }


def ml_find_listing_rows_for_sku(sku: str, limit: int = 20) -> List[Dict[str, Any]]:
    sku = norm_sku(sku)
    if not sku:
        return []

    rows = (
        sb.table("marketplace_listings")
        .select("*")
        .eq("marketplace", "ML")
        .eq("sku", sku)
        .limit(max(1, min(int(limit or 20), 100)))
        .execute()
        .data
        or []
    )
    return rows


def build_ml_stock_debug_for_item(item_id: str, user_product_id: Optional[str] = None, inventory_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Prueba rutas candidatas de stock ML desde Render.

    Pistas encontradas en Integraly:
    - /items/{id}/stock
    - /items/{id}/stock/type/selling_address
    - /items/{id}/stock/type/seller_warehouse
    - /stores/search?tags=stock_location
    - inventory_id, network_node_id, locations, available_quantity
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Falta item_id")

    results: Dict[str, Any] = {
        "item_id": item_id,
        "user_product_id": user_product_id,
        "inventory_id": inventory_id,
        "note": "Debug read-only. No hace PUT ni cambia stock.",
        "candidates": {},
    }

    # Item base: sirve para comparar available_quantity, logistic_type e inventory_id.
    item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
    results["candidates"]["item"] = item_res

    # Si el item base respondió JSON, completamos IDs faltantes.
    try:
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            extracted = ml_extract_stock_debug_candidates(item_json)
            results["item_extracted"] = extracted
            user_product_id = user_product_id or extracted.get("user_product_id")
            inventory_id = inventory_id or extracted.get("inventory_id")
            results["user_product_id"] = user_product_id
            results["inventory_id"] = inventory_id
    except Exception:
        pass

    # Rutas halladas en DLL Integraly.
    results["candidates"]["item_stock"] = ml_debug_get(f"/items/{item_id}/stock", timeout=60)
    results["candidates"]["item_stock_selling_address"] = ml_debug_get(f"/items/{item_id}/stock/type/selling_address", timeout=60)
    results["candidates"]["item_stock_seller_warehouse"] = ml_debug_get(f"/items/{item_id}/stock/type/seller_warehouse", timeout=60)

    # Búsqueda de depósitos/locations. Integraly usa tags=stock_location.
    results["candidates"]["stores_stock_location"] = ml_debug_get("/stores/search", params={"tags": "stock_location"}, timeout=60)

    # Rutas candidatas adicionales. Pueden devolver 404/403, pero eso también informa.
    if user_product_id:
        results["candidates"]["user_product"] = ml_debug_get(f"/user-products/{user_product_id}", timeout=60)
        results["candidates"]["user_product_stock"] = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)

    if inventory_id:
        results["candidates"]["inventory"] = ml_debug_get(f"/inventories/{inventory_id}", timeout=60)
        results["candidates"]["inventory_stock"] = ml_debug_get(f"/inventories/{inventory_id}/stock", timeout=60)

    return results


@router.get("/admin/ml/debug/item-stock")
def admin_ml_debug_item_stock_endpoint(
    item_id: str,
    user_product_id: Optional[str] = Query(default=None),
    inventory_id: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)
    return build_ml_stock_debug_for_item(
        item_id=item_id,
        user_product_id=user_product_id,
        inventory_id=inventory_id,
    )


@router.post("/admin/ml/debug/stock-write-candidates")
def admin_ml_debug_stock_write_candidates_endpoint(
    item_id: Optional[str] = Query(default=None),
    user_product_id: Optional[str] = Query(default=None),
    quantity: Optional[int] = Query(default=None, description="Omitir para usar quantity actual selling_address"),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)
    return build_ml_stock_write_candidates_debug(
        item_id=item_id or "",
        user_product_id=user_product_id,
        quantity=quantity,
    )


@router.post("/admin/ml/debug/stock-write-candidates-by-sku")
def admin_ml_debug_stock_write_candidates_by_sku_endpoint(
    sku: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)

    rows = ml_find_listing_rows_for_sku(norm_sku(sku), limit=5)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No encontré listing ML para SKU {sku}")

    row = rows[0]
    raw = row.get("raw_data") or {}
    raw_item = raw.get("item") if isinstance(raw, dict) else {}
    if not isinstance(raw_item, dict):
        raw_item = {}

    item_id = str(row.get("external_product_id") or raw_item.get("id") or "").strip()
    user_product_id = raw_item.get("user_product_id")

    return {
        "ok": True,
        "version": APP_VERSION,
        "sku": norm_sku(sku),
        "listing_row_id": row.get("id"),
        "external_product_id": row.get("external_product_id"),
        "external_variant_id": row.get("external_variant_id"),
        "test": build_ml_stock_write_candidates_debug(
            item_id=item_id,
            user_product_id=user_product_id,
            quantity=None,
        ),
    }


@router.get("/admin/ml/debug/stock-by-sku")
def admin_ml_debug_stock_by_sku_endpoint(
    sku: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)

    sku_norm = norm_sku(sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="Falta sku")

    rows = ml_find_listing_rows_for_sku(sku_norm, limit=20)
    items = []

    for row in rows:
        raw = row.get("raw_data") or {}
        if not isinstance(raw, dict):
            raw = {}

        raw_item = raw.get("item") or {}
        if not isinstance(raw_item, dict):
            raw_item = {}

        item_id = (
            row.get("external_product_id")
            or row.get("listing_id")
            or raw_item.get("id")
            or ""
        )
        item_id = str(item_id or "").strip()
        if not item_id:
            items.append({
                "listing_row_id": row.get("id"),
                "sku": row.get("sku"),
                "error": "Fila ML sin external_product_id/item_id",
                "row": row,
            })
            continue

        user_product_id = raw_item.get("user_product_id")
        inventory_id = raw_item.get("inventory_id")

        items.append({
            "listing_row_id": row.get("id"),
            "sku": row.get("sku"),
            "title": row.get("title"),
            "external_product_id": row.get("external_product_id"),
            "external_variant_id": row.get("external_variant_id"),
            "listing_stock": row.get("stock"),
            "listing_available_quantity": row.get("available_quantity"),
            "raw_extracted": ml_extract_stock_debug_candidates(raw_item),
            "debug": build_ml_stock_debug_for_item(
                item_id=item_id,
                user_product_id=user_product_id,
                inventory_id=inventory_id,
            ),
        })

    return {
        "ok": True,
        "version": APP_VERSION,
        "sku": sku_norm,
        "rows_found": len(rows),
        "items": items,
    }


def ml_list_orders(limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Lee órdenes ML del vendedor. Usamos /orders/search con seller y orden descendente.
    No filtramos por estado acá para poder diagnosticar qué devuelve ML.
    """
    limit = max(1, min(int(limit or 10), 50))
    offset = max(0, int(offset or 0))
    data = ml_get_json(
        "https://api.mercadolibre.com/orders/search",
        params={
            "seller": str(ML_USER_ID),
            "sort": "date_desc",
            "limit": limit,
            "offset": offset,
        },
        timeout=60,
    )
    return data.get("results") or []


def ml_get_order(order_id: str) -> Dict[str, Any]:
    order_id = str(order_id or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="Falta order_id ML")
    return ml_get_json(f"https://api.mercadolibre.com/orders/{order_id}", timeout=60)


def ml_order_is_paid(order: Dict[str, Any]) -> bool:
    status = normalizar(order.get("status"))
    if status in ["paid", "confirmed"]:
        return True
    payments = order.get("payments") or []
    for p in payments:
        if normalizar(p.get("status")) in ["approved", "paid"]:
            return True
    return False


def ml_lookup_sku_from_listing(item_id: Any, variation_id: Any = None) -> Optional[str]:
    item_id = str(item_id or "").strip()
    if not item_id:
        return None

    variant = str(variation_id) if variation_id not in [None, "", 0, "0"] else "0"

    # 1) Match exacto item + variante.
    rows = (
        sb.table("marketplace_listings")
        .select("sku,external_product_id,external_variant_id,external_full_id,title")
        .eq("marketplace", "ML")
        .eq("external_product_id", item_id)
        .eq("external_variant_id", variant)
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows and rows[0].get("sku"):
        return norm_sku(rows[0].get("sku"))

    # 2) Publicación simple sin variante: puede haber quedado como external_variant_id=0.
    if variant == "0":
        rows = (
            sb.table("marketplace_listings")
            .select("sku,external_product_id,external_variant_id,external_full_id,title")
            .eq("marketplace", "ML")
            .eq("external_product_id", item_id)
            .limit(2)
            .execute()
            .data
            or []
        )
        if len(rows) == 1 and rows[0].get("sku"):
            return norm_sku(rows[0].get("sku"))

    return None


def ml_extract_sku_from_order_item(order_item: Dict[str, Any]) -> Optional[str]:
    item = order_item.get("item") or {}

    candidates = [
        order_item.get("seller_sku"),
        order_item.get("seller_custom_field"),
        item.get("seller_sku"),
        item.get("seller_custom_field"),
        item.get("sku"),
    ]

    for c in candidates:
        c = norm_sku(c)
        if c:
            return c

    item_id = item.get("id") or order_item.get("item_id")
    variation_id = item.get("variation_id") or order_item.get("variation_id")
    return ml_lookup_sku_from_listing(item_id, variation_id)


def ml_extract_lines(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    order_items = order.get("order_items") or []
    lines = []

    for idx, oi in enumerate(order_items):
        item = oi.get("item") or {}
        sku = ml_extract_sku_from_order_item(oi)
        qty = oi.get("quantity") or 1
        price = oi.get("unit_price") or oi.get("full_unit_price") or oi.get("sale_fee") or item.get("price")
        name = item.get("title") or oi.get("title") or ""

        if not sku:
            item_id = item.get("id") or oi.get("item_id")
            variation_id = item.get("variation_id") or oi.get("variation_id")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Línea ML sin SKU mapeable en índice {idx}. "
                    f"item_id={item_id}, variation_id={variation_id}. "
                    "Primero sincronizá publicaciones ML a marketplace_listings o cargá seller_sku."
                ),
            )

        lines.append({
            "sku": sku,
            "quantity": qty,
            "unit_price": price,
            "name": name,
            "ml_item_id": item.get("id") or oi.get("item_id"),
            "ml_variation_id": item.get("variation_id") or oi.get("variation_id"),
        })

    return lines


def ml_extract_customer(order: Dict[str, Any]) -> Dict[str, Any]:
    buyer = order.get("buyer") or {}
    name = " ".join([str(buyer.get("first_name") or "").strip(), str(buyer.get("last_name") or "").strip()]).strip()
    if not name:
        name = buyer.get("nickname") or str(buyer.get("id") or "") or None
    phone = None
    phone_data = buyer.get("phone") or {}
    if isinstance(phone_data, dict):
        phone = phone_data.get("number") or phone_data.get("area_code")
    return {"name": name, "phone": phone}


def ml_order_total(order: Dict[str, Any]) -> Optional[float]:
    for key in ["total_amount", "paid_amount", "order_amount"]:
        if order.get(key) is not None:
            try:
                return float(str(order.get(key)).replace(",", "."))
            except Exception:
                pass
    return None


def ml_build_order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="Orden ML sin id")
    customer = ml_extract_customer(order)
    return {
        "external_order_id": order_id,
        "lines": ml_extract_lines(order),
        "customer_name": customer.get("name"),
        "customer_phone": customer.get("phone"),
        "total": ml_order_total(order),
        "raw_payload": {
            "ml_order_id": order_id,
            "ml_status": order.get("status"),
            "ml_date_created": order.get("date_created"),
            "ml_date_closed": order.get("date_closed"),
            "ml_shipping_id": (order.get("shipping") or {}).get("id"),
            "ml_pack_id": order.get("pack_id"),
            "ml_raw": order,
        },
    }


def apply_ml_order_payload(order: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    built = ml_build_order_payload(order)
    return process_order_lines(
        channel="ML",
        external_order_id=built["external_order_id"],
        lines=built["lines"],
        raw_payload=built["raw_payload"],
        dry_run=dry_run,
        customer_name=built["customer_name"],
        customer_phone=built["customer_phone"],
        total=built["total"],
    )


def ml_order_shipping_id(order: Dict[str, Any]) -> Optional[str]:
    shipping = order.get("shipping") or {}
    sid = shipping.get("id") if isinstance(shipping, dict) else None
    return str(sid) if sid is not None and str(sid).strip() else None


def ml_find_orders_by_shipping_id(shipping_id: str, scan_limit: int = 250) -> List[Dict[str, Any]]:
    """
    Busca órdenes ML asociadas a un mismo shipping_id.

    ML suele separar un paquete/envío en varias órdenes. Primero intentamos filtro directo
    por shipping.id y después hacemos fallback escaneando últimas órdenes del vendedor.
    """
    ml_validate_config(require_user=True)
    wanted = str(shipping_id or "").strip()
    if not wanted:
        raise HTTPException(status_code=400, detail="Falta shipping_id ML")

    found: Dict[str, Dict[str, Any]] = {}

    # 1) Intento directo. Si ML no acepta el filtro en alguna cuenta/contexto, no rompemos.
    try:
        data = ml_get_json(
            "https://api.mercadolibre.com/orders/search",
            params={
                "seller": str(ML_USER_ID),
                "shipping.id": wanted,
                "sort": "date_desc",
                "limit": 50,
                "offset": 0,
            },
            timeout=60,
        )
        for order in data.get("results") or []:
            if ml_order_shipping_id(order) == wanted:
                oid = str(order.get("id") or "").strip()
                if oid:
                    try:
                        found[oid] = ml_get_order(oid)
                    except Exception:
                        found[oid] = order
    except Exception:
        pass

    # 2) Fallback robusto: escaneamos recientes y filtramos por shipping_id.
    scan_limit = max(1, min(int(scan_limit or 250), 1000))
    per_page = 50
    for offset in range(0, scan_limit, per_page):
        batch = ml_list_orders(limit=min(per_page, scan_limit - offset), offset=offset)
        if not batch:
            break
        for order in batch:
            if ml_order_shipping_id(order) == wanted:
                oid = str(order.get("id") or "").strip()
                if oid and oid not in found:
                    try:
                        found[oid] = ml_get_order(oid)
                    except Exception:
                        found[oid] = order

    return sorted(found.values(), key=lambda o: str(o.get("id") or ""))


def ml_find_orders_by_pack_id(pack_id: str, scan_limit: int = 250) -> List[Dict[str, Any]]:
    """
    Busca todas las órdenes ML que pertenecen a un pack_id.
    En la UI de ML el pack_id es la compra visible agrupada; por API llegan varios order_id.
    """
    ml_validate_config(require_user=True)
    wanted = str(pack_id or "").strip()
    if not wanted:
        raise HTTPException(status_code=400, detail="Falta pack_id ML")

    found: Dict[str, Dict[str, Any]] = {}

    # Intento directo. En algunas cuentas ML acepta pack_id como filtro; si no, fallback.
    try:
        data = ml_get_json(
            "https://api.mercadolibre.com/orders/search",
            params={
                "seller": str(ML_USER_ID),
                "pack_id": wanted,
                "sort": "date_desc",
                "limit": 50,
                "offset": 0,
            },
            timeout=60,
        )
        for order in data.get("results") or []:
            if str(order.get("pack_id") or "").strip() == wanted:
                oid = str(order.get("id") or "").strip()
                if oid:
                    try:
                        found[oid] = ml_get_order(oid)
                    except Exception:
                        found[oid] = order
    except Exception:
        pass

    scan_limit = max(1, min(int(scan_limit or 250), 1000))
    per_page = 50
    for offset in range(0, scan_limit, per_page):
        batch = ml_list_orders(limit=min(per_page, scan_limit - offset), offset=offset)
        if not batch:
            break
        for order in batch:
            if str(order.get("pack_id") or "").strip() == wanted:
                oid = str(order.get("id") or "").strip()
                if oid and oid not in found:
                    try:
                        found[oid] = ml_get_order(oid)
                    except Exception:
                        found[oid] = order

    return sorted(found.values(), key=lambda o: str(o.get("id") or ""))


def ml_reconcile_orders_preview_data(
    orders: List[Dict[str, Any]],
    *,
    group_type: str,
    group_id: str,
) -> Dict[str, Any]:
    items = []
    already_applied = 0
    missing = 0
    errors = 0
    total_ml_amount = 0.0

    for order in orders:
        oid = str(order.get("id") or "").strip()
        exists = order_exists("ML", oid)
        customer = ml_extract_customer(order)
        order_total = ml_order_total(order)
        if order_total is not None:
            total_ml_amount += float(order_total)

        item = {
            "ml_order_id": oid,
            "status": order.get("status"),
            "paid": ml_order_is_paid(order),
            "date_created": order.get("date_created"),
            "date_closed": order.get("date_closed"),
            "shipping_id": ml_order_shipping_id(order),
            "pack_id": order.get("pack_id"),
            "customer_name": customer.get("name"),
            "total": order_total,
            "already_applied": exists,
            "action": None,
            "sold_lines": [],
            "stock_preview": [],
            "error": None,
        }

        try:
            built = ml_build_order_payload(order)
            item["sold_lines"] = built.get("lines") or []
            if exists:
                item["action"] = "already_applied"
                already_applied += 1
            elif not ml_order_is_paid(order):
                item["action"] = "skipped_not_paid"
                missing += 1
            else:
                preview = process_order_lines(
                    channel="ML",
                    external_order_id=built["external_order_id"],
                    lines=built["lines"],
                    raw_payload=built["raw_payload"],
                    dry_run=True,
                    customer_name=built["customer_name"],
                    customer_phone=built["customer_phone"],
                    total=built["total"],
                )
                item["action"] = "missing_ready_to_apply"
                item["stock_preview"] = preview.get("stock_preview") or []
                item["preview"] = preview
                missing += 1
        except Exception as e:
            item["action"] = "error"
            item["error"] = str(e)
            errors += 1

        items.append(item)

    return {
        "ok": errors == 0,
        "dry_run": True,
        "group_type": group_type,
        "group_id": str(group_id),
        "shipping_id": str(group_id) if group_type == "shipping" else None,
        "pack_id": str(group_id) if group_type == "pack" else None,
        "orders_found": len(orders),
        "already_applied": already_applied,
        "missing_or_skipped": missing,
        "errors": errors,
        "total_ml_amount_found": round(total_ml_amount, 2),
        "items": items,
    }


def ml_reconcile_shipping_preview_data(shipping_id: str, scan_limit: int = 250) -> Dict[str, Any]:
    orders = ml_find_orders_by_shipping_id(shipping_id, scan_limit=scan_limit)
    return ml_reconcile_orders_preview_data(orders, group_type="shipping", group_id=str(shipping_id))


def ml_reconcile_pack_preview_data(pack_id: str, scan_limit: int = 250) -> Dict[str, Any]:
    orders = ml_find_orders_by_pack_id(pack_id, scan_limit=scan_limit)
    return ml_reconcile_orders_preview_data(orders, group_type="pack", group_id=str(pack_id))


def apply_ml_reconciliation_orders(
    orders: List[Dict[str, Any]],
    *,
    group_type: str,
    group_id: str,
    notify: bool = False,
    block_on_errors: bool = True,
) -> Dict[str, Any]:
    """Aplica un grupo ML solo si el preview no tiene errores, para evitar parciales."""
    preview = ml_reconcile_orders_preview_data(orders, group_type=group_type, group_id=str(group_id))
    if block_on_errors and preview.get("errors", 0) > 0:
        return {
            "ok": False,
            "dry_run": False,
            "blocked": True,
            "reason": "preview_has_errors_no_partial_apply",
            "group_type": group_type,
            "group_id": str(group_id),
            "orders_found": preview.get("orders_found"),
            "already_applied": preview.get("already_applied"),
            "missing_or_skipped": preview.get("missing_or_skipped"),
            "errors": preview.get("errors"),
            "preview": preview,
        }

    items = []
    applied = 0
    skipped = 0
    errors = 0

    for order in orders:
        oid = str(order.get("id") or "").strip()
        item = {
            "ml_order_id": oid,
            "shipping_id": ml_order_shipping_id(order),
            "pack_id": order.get("pack_id"),
            "date_created": order.get("date_created"),
            "action": None,
        }
        try:
            if order_exists("ML", oid):
                row = get_order_row("ML", oid)
                item["action"] = "skipped_already_applied"
                item["erp_order_id"] = (row or {}).get("id")
                skipped += 1
            elif not ml_order_is_paid(order):
                item["action"] = "skipped_not_paid"
                skipped += 1
            else:
                result = apply_ml_order_payload(order, dry_run=False)
                item["action"] = "applied"
                item["process_result"] = result
                applied += 1
                if notify and result.get("ok") and not result.get("duplicate"):
                    item["whatsapp_notify"] = notify_ml_sale_once(result, order)
        except Exception as e:
            item["action"] = "error"
            item["error"] = str(e)
            errors += 1
        items.append(item)

    return {
        "ok": errors == 0,
        "dry_run": False,
        "blocked": False,
        "group_type": group_type,
        "group_id": str(group_id),
        "orders_found": len(orders),
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "items": items,
    }


def apply_ml_shipping_reconciliation(shipping_id: str, scan_limit: int = 250, notify: bool = False, block_on_errors: bool = True) -> Dict[str, Any]:
    orders = ml_find_orders_by_shipping_id(shipping_id, scan_limit=scan_limit)
    result = apply_ml_reconciliation_orders(
        orders,
        group_type="shipping",
        group_id=str(shipping_id),
        notify=notify,
        block_on_errors=block_on_errors,
    )
    result["shipping_id"] = str(shipping_id)
    return result


def apply_ml_pack_reconciliation(pack_id: str, scan_limit: int = 250, notify: bool = False, block_on_errors: bool = True) -> Dict[str, Any]:
    orders = ml_find_orders_by_pack_id(pack_id, scan_limit=scan_limit)
    result = apply_ml_reconciliation_orders(
        orders,
        group_type="pack",
        group_id=str(pack_id),
        notify=notify,
        block_on_errors=block_on_errors,
    )
    result["pack_id"] = str(pack_id)
    return result


def order_whatsapp_notified_ml(order_row: Dict[str, Any]) -> bool:
    raw = (order_row or {}).get("raw_data") or {}
    return isinstance(raw, dict) and bool(raw.get("ml_whatsapp_notified_at"))


def mark_order_whatsapp_notified_ml(order_id: str, notify_result: Dict[str, Any]):
    if not order_id:
        return
    rows = (
        sb.table("orders")
        .select("raw_data")
        .eq("id", order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    raw = rows[0].get("raw_data") if rows else {}
    if not isinstance(raw, dict):
        raw = {"previous_raw_data": raw}
    raw["ml_whatsapp_notified_at"] = now_iso()
    raw["ml_whatsapp_notify_result"] = notify_result
    sb.table("orders").update({"raw_data": raw, "updated_at": now_iso()}).eq("id", order_id).execute()


def build_ml_sale_whatsapp_message(result: Dict[str, Any], ml_order: Dict[str, Any]) -> str:
    order_id = ml_order.get("id") or result.get("external_order_id")
    customer = ((result.get("order") or {}).get("customer_name") or (ml_order.get("buyer") or {}).get("nickname") or "Sin nombre")
    total = (result.get("order") or {}).get("total") or ml_order_total(ml_order)
    status = ml_order.get("status") or ""
    shipping_id = (ml_order.get("shipping") or {}).get("id") or ""

    sold = []
    for line in result.get("sold_lines") or []:
        sold.append(f"- {line.get('sku')} x{line.get('quantity')}: {line.get('name') or ''}".strip())

    stock = []
    for row in result.get("stock_applied") or []:
        stock.append(
            f"- {row.get('sku')}: {row.get('old_stock')} → {row.get('new_stock')} "
            f"({row.get('qty_decrement')})"
        )

    msg = [
        f"Nueva venta ML #{order_id}",
        "",
        f"Cliente: {customer}",
        f"Total: {money_fmt(total)}",
        f"Estado ML: {status}",
    ]
    if shipping_id:
        msg.append(f"Envío ML: {shipping_id}")
    if sold:
        msg += ["", "Productos:"] + sold[:8]
    if stock:
        msg += ["", "Stock descontado:"] + stock[:12]
    msg += ["", "Ya está aplicada en el ERP."]
    return "\n".join(msg)


def notify_ml_sale_once(result: Dict[str, Any], ml_order: Dict[str, Any]) -> Dict[str, Any]:
    order_row = result.get("order")
    if not order_row:
        return {"ok": False, "skipped": True, "reason": "Sin order row"}
    if order_whatsapp_notified_ml(order_row):
        return {"ok": True, "skipped": True, "reason": "Ya notificada"}
    msg = build_ml_sale_whatsapp_message(result, ml_order)
    notify_result = send_whatsapp_admin(msg)
    if notify_result.get("ok"):
        mark_order_whatsapp_notified_ml(order_row.get("id"), notify_result)
    return notify_result


def poll_ml_orders_once(limit: int = 10, dry_run: bool = False, notify: bool = True, apply_existing: bool = False) -> Dict[str, Any]:
    """
    Poll ML pack-aware:
    - Si pack_id es null, procesa order_id individual.
    - Si pack_id existe, no aplica la orden suelta: reconcilia el paquete/envío completo.
    - Si el paquete tiene errores de SKU/combo, no aplica parcial y queda para revisión manual.
    """
    limit = max(1, min(int(limit or 10), 50))
    orders = ml_list_orders(limit=limit, offset=0)
    results = []
    applied = 0
    skipped = 0
    errors = 0
    seen_groups = set()

    for order in orders:
        pack_id = order.get("pack_id")
        shipping_id = ml_order_shipping_id(order)
        item_result = {
            "ml_order_id": order.get("id"),
            "status": order.get("status"),
            "date_created": order.get("date_created"),
            "date_closed": order.get("date_closed"),
            "shipping_id": shipping_id,
            "pack_id": pack_id,
            "action": None,
        }
        try:
            if not ml_order_is_paid(order):
                item_result["action"] = "skipped_not_paid"
                skipped += 1
                results.append(item_result)
                continue

            # Regla real ML: si hay pack_id, la venta visible es el pack; los order_id son líneas separadas.
            if pack_id:
                group_key = ("pack", str(pack_id))
                if group_key in seen_groups:
                    item_result["action"] = "skipped_pack_already_seen_in_this_poll"
                    skipped += 1
                    results.append(item_result)
                    continue
                seen_groups.add(group_key)

                if shipping_id:
                    if dry_run:
                        group_result = ml_reconcile_shipping_preview_data(shipping_id, scan_limit=ML_AUTO_POLL_SCAN_LIMIT)
                    else:
                        group_result = apply_ml_shipping_reconciliation(
                            shipping_id,
                            scan_limit=ML_AUTO_POLL_SCAN_LIMIT,
                            notify=notify,
                            block_on_errors=True,
                        )
                    group_ref = shipping_id
                    group_type = "shipping"
                else:
                    if dry_run:
                        group_result = ml_reconcile_pack_preview_data(str(pack_id), scan_limit=ML_AUTO_POLL_SCAN_LIMIT)
                    else:
                        group_result = apply_ml_pack_reconciliation(
                            str(pack_id),
                            scan_limit=ML_AUTO_POLL_SCAN_LIMIT,
                            notify=notify,
                            block_on_errors=True,
                        )
                    group_ref = str(pack_id)
                    group_type = "pack"

                item_result["action"] = "pack_preview" if dry_run else ("pack_applied" if group_result.get("ok") else "pack_manual_review")
                item_result["group_type"] = group_type
                item_result["group_id"] = group_ref
                item_result["group_result"] = group_result

                if dry_run:
                    skipped += 1
                elif group_result.get("ok"):
                    applied += int(group_result.get("applied") or 0)
                    skipped += int(group_result.get("skipped") or 0)
                else:
                    errors += 1
                results.append(item_result)
                continue

            # Venta single: order_id normal.
            external_id = str(order.get("id"))
            if order_exists("ML", external_id) and not apply_existing:
                row = get_order_row("ML", external_id)
                item_result["action"] = "skipped_duplicate"
                item_result["erp_order_id"] = (row or {}).get("id")
                item_result["whatsapp_notified"] = order_whatsapp_notified_ml(row or {})
                skipped += 1
                results.append(item_result)
                continue

            process_result = apply_ml_order_payload(order, dry_run=dry_run)
            item_result["process_result"] = process_result

            if process_result.get("duplicate"):
                item_result["action"] = "duplicate"
                skipped += 1
            elif dry_run:
                item_result["action"] = "dry_run_preview"
                skipped += 1
            else:
                item_result["action"] = "applied"
                applied += 1
                if notify:
                    item_result["whatsapp_notify"] = notify_ml_sale_once(process_result, order)
            results.append(item_result)
        except Exception as e:
            errors += 1
            item_result["action"] = "error"
            item_result["error"] = str(e)
            results.append(item_result)

    return {
        "ok": errors == 0,
        "dry_run": dry_run,
        "limit": limit,
        "ml_auto_poll_scan_limit": ML_AUTO_POLL_SCAN_LIMIT,
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "items": results,
    }


def ml_listing_available_quantity(item: Dict[str, Any]) -> Optional[int]:
    for key in ["available_quantity", "initial_quantity"]:
        if item.get(key) is not None:
            try:
                return int(item.get(key))
            except Exception:
                pass
    return None


def ml_listing_status(item: Dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    sub_status = item.get("sub_status") or []
    if status:
        return status
    if sub_status:
        return ",".join([str(x) for x in sub_status])
    return "unknown"


def ml_extract_seller_sku_from_attributes(attrs: Any) -> Optional[str]:
    """
    Extrae SOLO el SKU actual de ML desde attributes.SELLER_SKU/SKU.

    Nota importante:
      seller_custom_field NO se usa como SKU porque puede contener códigos
      históricos/legacy, por ejemplo BN11219-A12, mientras el SKU real visible
      en ML está en SELLER_SKU como BN11219.
    """
    if not isinstance(attrs, list):
        return None

    # Prioridad estricta: SELLER_SKU primero.
    for wanted in ("SELLER_SKU", "SKU"):
        for a in attrs:
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or "").upper()
            if aid != wanted:
                continue
            value = a.get("value_name") or a.get("value_id")
            value = norm_sku(value)
            if value:
                return value

    return None


def ml_extract_sku_with_source_from_item_or_variation(item: Dict[str, Any], variation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Devuelve el SKU actual y la fuente.

    Regla Planeta Casa / ML:
      - El SKU real/current es SELLER_SKU.
      - seller_custom_field queda descartado como fuente de SKU porque puede
        contener valores históricos y confunde el sincronismo.
      - Si mañana seller_custom_field sirve para otra cosa, se reevalúa aparte.
    """
    variation = variation or {}

    candidates = [
        ("variation.attributes.SELLER_SKU", ml_extract_seller_sku_from_attributes(variation.get("attributes"))),
        ("variation.attribute_combinations.SELLER_SKU", ml_extract_seller_sku_from_attributes(variation.get("attribute_combinations"))),
        ("item.attributes.SELLER_SKU", ml_extract_seller_sku_from_attributes(item.get("attributes"))),
        ("variation.seller_sku", variation.get("seller_sku")),
        ("variation.sku", variation.get("sku")),
        ("item.seller_sku", item.get("seller_sku")),
        ("item.sku", item.get("sku")),
    ]

    for source, value in candidates:
        sku = norm_sku(value)
        if sku:
            return {"sku": sku, "sku_source": source}

    return {"sku": None, "sku_source": None}


def ml_extract_sku_from_item_or_variation(item: Dict[str, Any], variation: Optional[Dict[str, Any]] = None) -> Optional[str]:
    return ml_extract_sku_with_source_from_item_or_variation(item, variation).get("sku")


def ml_upsert_marketplace_listing(item: Dict[str, Any], variation: Optional[Dict[str, Any]] = None, dry_run: bool = False, include_raw_data: bool = False) -> Dict[str, Any]:
    """
    Guarda publicaciones ML en marketplace_listings sin colapsar por SKU.

    Clave lógica estable:
      marketplace + external_product_id + external_variant_id

    Importante para Planeta Casa:
      - El SKU de ML es mutable: puede cambiar por proveedor/prefijo.
      - La publicación/variante es la identidad estable.
      - El SKU actual se toma de SELLER_SKU y pisa el valor anterior.
      - seller_custom_field se descarta como fuente de SKU porque puede ser histórico.
    """
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        return {"ok": False, "action": "skipped", "reason": "item sin id"}

    variation = variation or None
    variation_id = "0"
    title = item.get("title") or ""
    price = item.get("price")
    available_quantity = ml_listing_available_quantity(item)
    status = ml_listing_status(item)
    listing_type = item.get("listing_type_id") or item.get("listing_type") or "standard"
    permalink = item.get("permalink")

    if variation:
        variation_id = str(variation.get("id") or "0")
        attrs = variation.get("attribute_combinations") or []
        attr_txt = " / ".join([str(a.get("value_name") or a.get("value_id") or "") for a in attrs if isinstance(a, dict)])
        title = f"{title} / {attr_txt}".strip(" / ") if attr_txt else f"{title} / {variation_id}"
        price = variation.get("price") if variation.get("price") is not None else price
        available_quantity = ml_listing_available_quantity(variation)

    sku_info = ml_extract_sku_with_source_from_item_or_variation(item, variation)
    sku = sku_info.get("sku")
    sku_source = sku_info.get("sku_source")
    if not sku:
        return {"ok": False, "action": "skipped_no_seller_sku", "item_id": item_id, "variation_id": variation_id, "reason": "sin SELLER_SKU actual"}

    inv = get_item_by_sku(sku)
    inventory_item_id = inv.get("id") if inv else None

    # Si el SKU actual no existe en inventory_items, no insertamos huérfanos:
    # marketplace_listings.inventory_item_id es NOT NULL a propósito.
    if not inv:
        return {
            "ok": False,
            "action": "skipped_unmapped_seller_sku",
            "sku": sku,
            "sku_source": sku_source,
            "inventory_item_found": False,
            "item_id": item_id,
            "variation_id": variation_id,
            "reason": "SELLER_SKU no existe en inventory_items",
        }

    existing = (
        sb.table("marketplace_listings")
        .select("id,sku")
        .eq("marketplace", "ML")
        .eq("external_product_id", item_id)
        .eq("external_variant_id", variation_id)
        .limit(1)
        .execute()
        .data
        or []
    )

    payload = {
        "inventory_item_id": inventory_item_id,
        "sku": sku,
        "marketplace": "ML",
        "listing_type": str(listing_type or "standard"),
        "external_product_id": item_id,
        "external_variant_id": variation_id,
        "external_full_id": item_id,
        "title": title,
        "price": price,
        "stock": available_quantity,
        "available_quantity": available_quantity,
        "status": status,
        "url": permalink,
        "permalink": permalink,
        "raw_data": {
            "item": item,
            "variation": variation,
            "inventory_item_found": bool(inv),
            "sku_source": sku_source,
            "sync_key": {
                "marketplace": "ML",
                "external_product_id": item_id,
                "external_variant_id": variation_id,
                "sku": sku,
            },
        },
        "last_sync_at": now_iso(),
        "updated_at": now_iso(),
    }

    action_base = "update" if existing else "insert"
    mapped_suffix = "mapped" if inv else "unmapped"

    if dry_run:
        payload_report = dict(payload)
        if not include_raw_data:
            payload_report.pop("raw_data", None)

        report = {
            "ok": True,
            "action": f"dry_run_{action_base}_{mapped_suffix}",
            "sku": sku,
            "sku_source": sku_source,
            "inventory_item_found": bool(inv),
            "item_id": item_id,
            "variation_id": variation_id,
            "price": price,
            "available_quantity": available_quantity,
            "permalink": permalink,
            "status": status,
            "listing_type": str(listing_type or "standard"),
            "payload": payload_report,
        }
        if include_raw_data:
            report["raw_data"] = payload.get("raw_data")
        return report

    try:
        if existing:
            sb.table("marketplace_listings").update(payload).eq("id", existing[0]["id"]).execute()
            return {
                "ok": True,
                "action": f"updated_{mapped_suffix}",
                "sku": sku,
                "sku_source": sku_source,
                "inventory_item_found": bool(inv),
                "item_id": item_id,
                "variation_id": variation_id,
            }

        payload["created_at"] = now_iso()
        sb.table("marketplace_listings").insert(payload).execute()
        return {
            "ok": True,
            "action": f"inserted_{mapped_suffix}",
            "sku": sku,
            "inventory_item_found": bool(inv),
            "item_id": item_id,
            "variation_id": variation_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "action": "error_upsert_marketplace_listing",
            "sku": sku,
            "sku_source": sku_source,
            "inventory_item_found": bool(inv),
            "item_id": item_id,
            "variation_id": variation_id,
            "error": str(e),
        }


def ml_get_seller_item_ids(limit: int = 200) -> List[str]:
    limit = max(1, min(int(limit or 200), 1000))
    collected: List[str] = []
    offset = 0
    page_size = min(50, limit)

    while len(collected) < limit:
        data = ml_get_json(
            f"https://api.mercadolibre.com/users/{ML_USER_ID}/items/search",
            params={"limit": page_size, "offset": offset},
            timeout=60,
        )
        results = data.get("results") or []
        collected.extend([str(x) for x in results if x])
        if not results or len(collected) >= limit:
            break
        offset += len(results)

    return collected[:limit]


def ml_get_item_detail(item_id: str) -> Dict[str, Any]:
    return ml_get_json(f"https://api.mercadolibre.com/items/{item_id}", timeout=60)


def ml_get_item_raw_debug(item_id: str) -> Dict[str, Any]:
    """Devuelve el raw principal de /items/{item_id} sin interpretar."""
    item_id = str(item_id or "").strip()
    url = f"https://api.mercadolibre.com/items/{item_id}"
    r = ml_request("GET", url, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"raw_text": r.text}
    return {
        "ok": r.status_code == 200,
        "status_code": r.status_code,
        "url": url,
        "body": body,
    }


def ml_get_variation_raw_debug(item_id: str, variation_id: str) -> Dict[str, Any]:
    """
    Devuelve el raw de /items/{item_id}/variations/{variation_id} sin interpretar.

    Lo usamos para ubicar dónde ML guarda el SELLER_SKU real cuando la
    publicación tiene variaciones y /items/{item_id} no trae el SKU en el
    bloque básico de variations.
    """
    item_id = str(item_id or "").strip()
    variation_id = str(variation_id or "").strip()
    url = f"https://api.mercadolibre.com/items/{item_id}/variations/{variation_id}"
    r = ml_request("GET", url, timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"raw_text": r.text}
    return {
        "ok": r.status_code == 200,
        "status_code": r.status_code,
        "url": url,
        "body": body,
    }


def ml_get_variation_detail(item_id: str, variation_id: str) -> Optional[Dict[str, Any]]:
    """
    Lee el endpoint específico de variación y devuelve el body usable.

    En MercadoLibre, algunas publicaciones con variaciones NO traen SELLER_SKU
    dentro del bloque básico item["variations"]. El SKU actual aparece en:

        GET /items/{item_id}/variations/{variation_id}
        body.attributes[].id == "SELLER_SKU"

    Esta función se usa en el sync para no marcar como "sin SKU" variaciones
    que sí lo tienen en el endpoint específico.
    """
    raw = ml_get_variation_raw_debug(item_id, variation_id)
    body = raw.get("body")
    if raw.get("ok") and isinstance(body, dict):
        return body
    return None


def ml_enrich_variation_for_sync(item: Dict[str, Any], variation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Devuelve una variación enriquecida para sync.

    Prioridad:
      - Traer datos completos de /items/{item}/variations/{variation}
      - Conservar id, price y available_quantity del bloque básico si faltan
      - Conservar attribute_combinations del bloque básico si el endpoint no las trae
    """
    variation = variation or {}
    item_id = str(item.get("id") or "").strip()
    variation_id = str(variation.get("id") or "").strip()
    if not item_id or not variation_id:
        return variation

    detail = ml_get_variation_detail(item_id, variation_id)
    if not isinstance(detail, dict):
        return variation

    merged = dict(detail)
    # Asegurar id correcto aunque ML cambie formato
    merged["id"] = merged.get("id") or variation.get("id")

    for key in ("price", "available_quantity"):
        if merged.get(key) is None and variation.get(key) is not None:
            merged[key] = variation.get(key)

    if not merged.get("attribute_combinations") and variation.get("attribute_combinations"):
        merged["attribute_combinations"] = variation.get("attribute_combinations")

    # Dejar una pista de diagnóstico en raw_data cuando se guarde.
    merged["_sync_variation_source"] = "variation_endpoint"
    return merged


def ml_sku_like_attributes(attrs: Any) -> List[Dict[str, Any]]:
    """Devuelve todos los atributos que podrían contener SKU/código vendedor."""
    out: List[Dict[str, Any]] = []
    if not isinstance(attrs, list):
        return out

    for a in attrs:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "")
        aname = str(a.get("name") or "")
        hay = f"{aid} {aname}".lower()
        if "sku" not in hay and "custom" not in hay and "código" not in hay and "codigo" not in hay:
            continue

        values = []
        for v in a.get("values") or []:
            if isinstance(v, dict):
                values.append({
                    "id": v.get("id"),
                    "name": v.get("name"),
                    "struct": v.get("struct"),
                })

        out.append({
            "id": aid,
            "name": aname,
            "value_id": a.get("value_id"),
            "value_name": a.get("value_name"),
            "value_type": a.get("value_type"),
            "values": values,
            "normalized_value": norm_sku(a.get("value_name") or a.get("value_id")),
        })
    return out


def ml_add_sku_candidate(candidates: List[Dict[str, Any]], source: str, value: Any) -> None:
    raw = None if value is None else str(value)
    normalized = norm_sku(value)
    if not raw and not normalized:
        return
    inv = get_item_by_sku(normalized) if normalized else None
    candidates.append({
        "source": source,
        "raw_value": raw,
        "normalized_sku": normalized,
        "inventory_item_found": bool(inv),
        "inventory_item_id": inv.get("id") if inv else None,
        "inventory_item_name": inv.get("name") if inv else None,
    })


def ml_build_sku_debug_for_item(item: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    item_id = str(item.get("id") or "")
    item_level_candidates: List[Dict[str, Any]] = []

    ml_add_sku_candidate(item_level_candidates, "item.seller_custom_field", item.get("seller_custom_field"))
    ml_add_sku_candidate(item_level_candidates, "item.seller_sku", item.get("seller_sku"))
    ml_add_sku_candidate(item_level_candidates, "item.sku", item.get("sku"))

    for a in ml_sku_like_attributes(item.get("attributes")):
        ml_add_sku_candidate(item_level_candidates, f"item.attributes.{a.get('id')}", a.get("value_name") or a.get("value_id"))

    variations_out: List[Dict[str, Any]] = []
    variations = item.get("variations") or []
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        var_candidates: List[Dict[str, Any]] = []
        ml_add_sku_candidate(var_candidates, "variation.seller_custom_field", variation.get("seller_custom_field"))
        ml_add_sku_candidate(var_candidates, "variation.seller_sku", variation.get("seller_sku"))
        ml_add_sku_candidate(var_candidates, "variation.sku", variation.get("sku"))

        for a in ml_sku_like_attributes(variation.get("attributes")):
            ml_add_sku_candidate(var_candidates, f"variation.attributes.{a.get('id')}", a.get("value_name") or a.get("value_id"))
        for a in ml_sku_like_attributes(variation.get("attribute_combinations")):
            ml_add_sku_candidate(var_candidates, f"variation.attribute_combinations.{a.get('id')}", a.get("value_name") or a.get("value_id"))

        chosen_info = ml_extract_sku_with_source_from_item_or_variation(item, variation)
        chosen = chosen_info.get("sku")
        inv = get_item_by_sku(chosen) if chosen else None
        variations_out.append({
            "variation_id": str(variation.get("id") or "0"),
            "available_quantity": variation.get("available_quantity"),
            "price": variation.get("price"),
            "chosen_by_current_extractor": chosen,
            "chosen_sku_source": chosen_info.get("sku_source"),
            "chosen_inventory_item_found": bool(inv),
            "chosen_inventory_item_id": inv.get("id") if inv else None,
            "sku_candidates": var_candidates,
            "sku_like_attributes": {
                "attributes": ml_sku_like_attributes(variation.get("attributes")),
                "attribute_combinations": ml_sku_like_attributes(variation.get("attribute_combinations")),
            },
            "raw_variation": variation if include_raw else None,
        })

    chosen_item_info = ml_extract_sku_with_source_from_item_or_variation(item, None)
    chosen_item_only = chosen_item_info.get("sku")
    inv_item_only = get_item_by_sku(chosen_item_only) if chosen_item_only else None

    result = {
        "ok": True,
        "item_id": item_id,
        "title": item.get("title"),
        "status": item.get("status"),
        "seller_id": item.get("seller_id"),
        "permalink": item.get("permalink"),
        "inventory_id": item.get("inventory_id"),
        "user_product_id": item.get("user_product_id"),
        "catalog_product_id": item.get("catalog_product_id"),
        "seller_custom_field": item.get("seller_custom_field"),
        "seller_sku": item.get("seller_sku"),
        "sku": item.get("sku"),
        "chosen_by_current_extractor_item_only": chosen_item_only,
        "chosen_sku_source_item_only": chosen_item_info.get("sku_source"),
        "chosen_inventory_item_found_item_only": bool(inv_item_only),
        "chosen_inventory_item_id_item_only": inv_item_only.get("id") if inv_item_only else None,
        "sku_candidates_item_level": item_level_candidates,
        "sku_like_attributes_item_level": ml_sku_like_attributes(item.get("attributes")),
        "variations_count": len(variations),
        "variations": variations_out,
        "note": "El extractor actual usa SELLER_SKU como verdad y descarta seller_custom_field como SKU porque puede contener códigos históricos.",
    }
    if include_raw:
        result["raw_item"] = item
    return result


def ml_find_variation_in_item(item: Dict[str, Any], variation_id: str) -> Optional[Dict[str, Any]]:
    variation_id = str(variation_id or "").strip()
    for v in item.get("variations") or []:
        if isinstance(v, dict) and str(v.get("id") or "") == variation_id:
            return v
    return None


def ml_collect_sku_candidates_for_variation_context(
    item: Dict[str, Any],
    variation: Optional[Dict[str, Any]],
    prefix: str,
) -> List[Dict[str, Any]]:
    """Lista candidatos SKU en un objeto de variación, sin decidir por el sync."""
    candidates: List[Dict[str, Any]] = []
    variation = variation or {}
    ml_add_sku_candidate(candidates, f"{prefix}.seller_custom_field", variation.get("seller_custom_field"))
    ml_add_sku_candidate(candidates, f"{prefix}.seller_sku", variation.get("seller_sku"))
    ml_add_sku_candidate(candidates, f"{prefix}.sku", variation.get("sku"))

    for a in ml_sku_like_attributes(variation.get("attributes")):
        ml_add_sku_candidate(candidates, f"{prefix}.attributes.{a.get('id')}", a.get("value_name") or a.get("value_id"))
    for a in ml_sku_like_attributes(variation.get("attribute_combinations")):
        ml_add_sku_candidate(candidates, f"{prefix}.attribute_combinations.{a.get('id')}", a.get("value_name") or a.get("value_id"))

    # Algunos endpoints pueden devolver datos anidados. No asumimos estructura,
    # pero si aparecen claves típicas, también las mostramos como pistas.
    for key in ("seller_sku", "sku", "SELLER_SKU", "seller_custom_field"):
        if key in variation:
            ml_add_sku_candidate(candidates, f"{prefix}.{key}", variation.get(key))

    return candidates


def ml_build_variation_sku_debug(item_id: str, variation_id: str, include_raw: bool = False) -> Dict[str, Any]:
    item_id = str(item_id or "").strip()
    variation_id = str(variation_id or "").strip()
    item = ml_get_item_detail(item_id)
    variation_from_item = ml_find_variation_in_item(item, variation_id)
    raw_detail = ml_get_variation_raw_debug(item_id, variation_id)
    variation_detail = raw_detail.get("body") if isinstance(raw_detail.get("body"), dict) else None

    item_level_candidates: List[Dict[str, Any]] = []
    for a in ml_sku_like_attributes(item.get("attributes")):
        ml_add_sku_candidate(item_level_candidates, f"item.attributes.{a.get('id')}", a.get("value_name") or a.get("value_id"))
    ml_add_sku_candidate(item_level_candidates, "item.seller_custom_field", item.get("seller_custom_field"))
    ml_add_sku_candidate(item_level_candidates, "item.seller_sku", item.get("seller_sku"))
    ml_add_sku_candidate(item_level_candidates, "item.sku", item.get("sku"))

    minimal_candidates = ml_collect_sku_candidates_for_variation_context(item, variation_from_item, "item.variations[]")
    detail_candidates = ml_collect_sku_candidates_for_variation_context(item, variation_detail, "variation_detail") if variation_detail else []

    chosen_minimal = ml_extract_sku_with_source_from_item_or_variation(item, variation_from_item)
    chosen_detail = ml_extract_sku_with_source_from_item_or_variation(item, variation_detail) if variation_detail else {"sku": None, "sku_source": None}
    inv_min = get_item_by_sku(chosen_minimal.get("sku")) if chosen_minimal.get("sku") else None
    inv_det = get_item_by_sku(chosen_detail.get("sku")) if chosen_detail.get("sku") else None

    # Para que el resumen de PowerShell sea directo, exponemos arriba el SKU elegido
    # desde el endpoint específico de variación, que es el que nos interesa.
    result = {
        "ok": True,
        "app_version": APP_VERSION,
        "item_id": item_id,
        "variation_id": variation_id,
        "title": item.get("title"),
        "sku": chosen_detail.get("sku"),
        "seller_sku": chosen_detail.get("sku"),
        "sku_source": chosen_detail.get("sku_source"),
        "item_status": item.get("status"),
        "permalink": item.get("permalink"),
        "variation_endpoint": {
            "ok": raw_detail.get("ok"),
            "status_code": raw_detail.get("status_code"),
            "url": raw_detail.get("url"),
        },
        "chosen_from_item_variation_basic": {
            "sku": chosen_minimal.get("sku"),
            "sku_source": chosen_minimal.get("sku_source"),
            "inventory_item_found": bool(inv_min),
            "inventory_item_id": inv_min.get("id") if inv_min else None,
            "inventory_item_name": inv_min.get("name") if inv_min else None,
        },
        "chosen_from_variation_endpoint": {
            "sku": chosen_detail.get("sku"),
            "sku_source": chosen_detail.get("sku_source"),
            "inventory_item_found": bool(inv_det),
            "inventory_item_id": inv_det.get("id") if inv_det else None,
            "inventory_item_name": inv_det.get("name") if inv_det else None,
        },
        "item_level_candidates": item_level_candidates,
        "variation_basic_candidates": minimal_candidates,
        "variation_endpoint_candidates": detail_candidates,
        "variation_basic_summary": {
            "available_quantity": variation_from_item.get("available_quantity") if variation_from_item else None,
            "price": variation_from_item.get("price") if variation_from_item else None,
            "attribute_combinations": variation_from_item.get("attribute_combinations") if variation_from_item else None,
        },
        "variation_endpoint_summary": {
            "available_quantity": variation_detail.get("available_quantity") if variation_detail else None,
            "price": variation_detail.get("price") if variation_detail else None,
            "attribute_combinations": variation_detail.get("attribute_combinations") if variation_detail else None,
            "attributes": variation_detail.get("attributes") if variation_detail else None,
        },
        "note": "Diagnóstico: no modifica sync. Busca dónde ML entrega el SELLER_SKU real para variaciones.",
    }

    if include_raw:
        result["raw_item_variation_basic"] = variation_from_item
        result["raw_variation_endpoint"] = raw_detail
        result["raw_item"] = item

    return result


@router.get("/admin/ml/items/{item_id}/raw")
def ml_item_raw_endpoint(
    item_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item_id = str(item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id requerido")
    return ml_get_item_raw_debug(item_id)


@router.get("/admin/ml/items/{item_id}/variations/{variation_id}/raw")
def ml_variation_raw_endpoint(
    item_id: str,
    variation_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item_id = str(item_id or "").strip()
    variation_id = str(variation_id or "").strip()
    if not item_id or not variation_id:
        raise HTTPException(status_code=400, detail="item_id y variation_id requeridos")
    return ml_get_variation_raw_debug(item_id, variation_id)


@router.get("/admin/ml/items/{item_id}/variations/{variation_id}/sku_debug")
def ml_variation_sku_debug_endpoint(
    item_id: str,
    variation_id: str,
    include_raw: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item_id = str(item_id or "").strip()
    variation_id = str(variation_id or "").strip()
    if not item_id or not variation_id:
        raise HTTPException(status_code=400, detail="item_id y variation_id requeridos")
    return ml_build_variation_sku_debug(item_id, variation_id, include_raw=include_raw)


@router.get("/admin/ml/items/{item_id}/sku_debug")
def ml_item_sku_debug_endpoint(
    item_id: str,
    include_raw: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item_id = str(item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id requerido")
    item = ml_get_item_detail(item_id)
    return ml_build_sku_debug_for_item(item, include_raw=include_raw)



# ============================================================
# ML - IMPORTADOR CONTROLADO DE SKUS NO MAPEADOS
# ============================================================

MANUAL_BUNDLE_COMPONENT_PATCHES = {
    # Composición informada por Gonzalo por chat.
    # No hace falta que esté en el CSV.
    "GDCOMBOCAJAS": [
        {"component_sku": "GDTCAJAC01", "component_qty": 1},
        {"component_sku": "GDTCAJAC02", "component_qty": 1},
        {"component_sku": "GDTCAJAC03", "component_qty": 1},
    ],
}


def parse_boolish(v: Any) -> bool:
    return str(v or "").strip().lower() in ["1", "true", "yes", "si", "sí", "on", "crear", "create"]


def parse_float_or_none(v: Any) -> Optional[float]:
    s = str(v if v is not None else "").strip()
    if not s or s.lower() in ["nan", "none", "null"]:
        return None
    s = s.replace("\ufeff", "").replace("$", "").replace(" ", "")
    # Soporta formato argentino simple si viene desde Excel.
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def parse_int_or_zero(v: Any) -> int:
    n = parse_float_or_none(v)
    if n is None:
        return 0
    try:
        return int(n)
    except Exception:
        return 0


def decode_request_body_text(raw: bytes) -> str:
    for enc in ["utf-8-sig", "utf-8", "latin-1"]:
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def sniff_csv_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except Exception:
        # El archivo de trabajo de Gonzalo viene separado por ;.
        return ";" if sample.count(";") >= sample.count(",") else ","


def parse_unmapped_skus_csv(text: str) -> List[Dict[str, Any]]:
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="CSV vacío")
    delimiter = sniff_csv_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV sin encabezados")

    rows: List[Dict[str, Any]] = []
    for idx, row in enumerate(reader, start=2):
        normalized = {}
        for k, v in (row or {}).items():
            key = str(k or "").replace("\ufeff", "").strip()
            val = str(v).strip() if v is not None else ""
            normalized[key] = val
        normalized["_row_number"] = idx
        rows.append(normalized)
    return rows


def inventory_table_columns() -> set:
    try:
        rows = sb.table("inventory_items").select("*").limit(1).execute().data or []
        if rows:
            return set(rows[0].keys())
    except Exception:
        pass
    # Fallback conservador por columnas ya usadas en este archivo.
    return {"id", "sku", "name", "variant_name", "category", "stock", "active", "item_type", "cost", "created_at", "updated_at"}


def build_unmapped_import_plan(csv_text: str, limit: int = 300) -> Dict[str, Any]:
    rows = parse_unmapped_skus_csv(csv_text)

    grouped: Dict[str, Dict[str, Any]] = {}
    row_errors: List[Dict[str, Any]] = []

    for r in rows:
        sku = norm_sku(r.get("sku"))
        if not sku:
            row_errors.append({"row": r.get("_row_number"), "error": "fila sin sku"})
            continue

        accion = str(r.get("Accion") or r.get("accion") or r.get("decision") or "crear").strip().lower()
        if accion and accion not in ["crear", "create"]:
            # Por ahora el importador solo implementa altas. El resto se reporta.
            grouped.setdefault(sku, {
                "sku": sku,
                "action": accion,
                "rows": [],
                "components": [],
                "notes": [],
                "source_item_ids": set(),
            })["rows"].append(r)
            continue

        g = grouped.setdefault(sku, {
            "sku": sku,
            "action": "crear",
            "rows": [],
            "components": [],
            "notes": [],
            "source_item_ids": set(),
        })
        g["rows"].append(r)

        note = str(r.get("Nota") or r.get("nota") or "").strip()
        if note and note not in g["notes"]:
            g["notes"].append(note)

        item_id = str(r.get("item_id") or "").strip()
        if item_id and item_id.lower() not in ["nan", "none", "null"]:
            g["source_item_ids"].add(item_id)

        comp_sku = norm_sku(r.get("component_sku"))
        qty = parse_float_or_none(r.get("component_qty"))
        if comp_sku:
            if qty is None or qty <= 0:
                row_errors.append({"row": r.get("_row_number"), "sku": sku, "component_sku": comp_sku, "error": "component_qty inválido"})
            else:
                comp = {"component_sku": comp_sku, "component_qty": qty}
                if comp not in g["components"]:
                    g["components"].append(comp)

    # Parches manuales de composición informados fuera del CSV.
    for bundle_sku, comps in MANUAL_BUNDLE_COMPONENT_PATCHES.items():
        sku = norm_sku(bundle_sku)
        if sku in grouped:
            for comp in comps:
                comp_norm = {"component_sku": norm_sku(comp.get("component_sku")), "component_qty": float(comp.get("component_qty") or 0)}
                if comp_norm["component_sku"] and comp_norm["component_qty"] > 0 and comp_norm not in grouped[sku]["components"]:
                    grouped[sku]["components"].append(comp_norm)
                    grouped[sku].setdefault("manual_patches", []).append(comp_norm)

    target_skus = sorted([sku for sku, g in grouped.items() if g.get("action") == "crear"])
    ml_by_sku = ml_collect_listing_candidates_by_skus(target_skus, limit=limit)
    inv_existing_by_sku: Dict[str, Dict[str, Any]] = {}
    inv_to_create_by_sku: Dict[str, Dict[str, Any]] = {}

    for sku in target_skus:
        item = get_item_by_sku(sku)
        if item:
            inv_existing_by_sku[sku] = item

    plans = []
    all_component_skus = set()
    for sku in target_skus:
        g = grouped[sku]
        components = g.get("components") or []
        for c in components:
            all_component_skus.add(c["component_sku"])

        ml_candidates = ml_by_sku.get(sku) or []
        chosen_ml = choose_ml_candidate_for_initial_item(ml_candidates)
        notes = g.get("notes") or []
        is_bundle = len(components) > 0

        item_type = "bundle" if is_bundle else "standard"
        name = (chosen_ml or {}).get("title") or sku
        price = (chosen_ml or {}).get("price")
        stock = parse_int_or_zero((chosen_ml or {}).get("available_quantity"))

        plan = {
            "sku": sku,
            "action": "skip_existing" if sku in inv_existing_by_sku else "create_inventory_item",
            "exists_in_inventory": sku in inv_existing_by_sku,
            "item_type": item_type,
            "name": name,
            "category": "COMBOS" if is_bundle else "ML IMPORT",
            "initial_stock_from_ml": stock,
            "initial_price_from_ml": price,
            "ml_candidates_count": len(ml_candidates),
            "ml_candidate": chosen_ml,
            "notes": notes,
            "source_item_ids": sorted(list(g.get("source_item_ids") or [])),
            "components": components,
            "manual_patches": g.get("manual_patches") or [],
            "warnings": [],
            "errors": [],
        }
        if not ml_candidates and sku != "LCSBBRect":
            plan["warnings"].append("No encontré publicación ML actual para este SKU; se crearía con stock 0 y nombre=SKU")
        if any("combo" in normalizar(n) for n in notes) and not components:
            plan["warnings"].append("La nota dice combo pero no tiene componentes")
        plans.append(plan)
        inv_to_create_by_sku[sku] = plan

    # Validación de componentes: pueden existir ya o crearse en este mismo lote.
    component_checks = []
    missing_components = []
    for comp_sku in sorted(all_component_skus):
        existing = get_item_by_sku(comp_sku)
        will_create = comp_sku in inv_to_create_by_sku and comp_sku not in inv_existing_by_sku
        check = {
            "component_sku": comp_sku,
            "exists_in_inventory": bool(existing),
            "will_be_created_in_this_import": bool(will_create),
            "ok": bool(existing or will_create),
        }
        component_checks.append(check)
        if not check["ok"]:
            missing_components.append(comp_sku)

    for plan in plans:
        for c in plan.get("components") or []:
            if c["component_sku"] in missing_components:
                plan["errors"].append(f"No existe componente y no se crea en este import: {c['component_sku']}")

    create_plans = [p for p in plans if p["action"] == "create_inventory_item"]
    bundle_plans = [p for p in create_plans if p["item_type"] == "bundle"]
    standard_plans = [p for p in create_plans if p["item_type"] != "bundle"]

    return {
        "ok": len(row_errors) == 0 and len(missing_components) == 0,
        "app_version": APP_VERSION,
        "csv_rows": len(rows),
        "skus_seen": len(grouped),
        "skus_to_create": len(create_plans),
        "skus_already_existing": len(inv_existing_by_sku),
        "bundles_to_create": len(bundle_plans),
        "standard_to_create": len(standard_plans),
        "component_lines": sum(len(p.get("components") or []) for p in plans),
        "components_checked": len(component_checks),
        "missing_components_count": len(missing_components),
        "missing_components": missing_components,
        "row_errors": row_errors[:50],
        "component_checks": component_checks,
        "plans": plans,
        "note": "Preview: no escribe en Supabase. variation_id del CSV no se usa para escribir; ML se relee por SKU.",
    }


def choose_ml_candidate_for_initial_item(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    # Preferimos una publicación activa y con stock. Si hay varias, la de mayor stock.
    def score(c: Dict[str, Any]):
        status = str(c.get("status") or "").lower()
        active_score = 1 if status in ["active", "paused"] else 0
        stock = parse_int_or_zero(c.get("available_quantity"))
        return (active_score, stock)
    return sorted(candidates, key=score, reverse=True)[0]


def ml_collect_listing_candidates_by_skus(target_skus: List[str], limit: int = 300) -> Dict[str, List[Dict[str, Any]]]:
    target = {norm_sku(s) for s in target_skus if norm_sku(s)}
    found: Dict[str, List[Dict[str, Any]]] = {s: [] for s in target}
    if not target:
        return found

    item_ids = ml_get_seller_item_ids(limit=limit)
    for item_id in item_ids:
        try:
            item = ml_get_item_detail(item_id)
            variations = item.get("variations") or []
            if variations:
                for variation in variations:
                    variation_for_sync = ml_enrich_variation_for_sync(item, variation)
                    sku_info = ml_extract_sku_with_source_from_item_or_variation(item, variation_for_sync)
                    sku = norm_sku(sku_info.get("sku"))
                    if sku not in target:
                        continue
                    attrs = variation_for_sync.get("attribute_combinations") or []
                    attr_txt = " / ".join([str(a.get("value_name") or a.get("value_id") or "") for a in attrs if isinstance(a, dict)])
                    title = item.get("title") or ""
                    if attr_txt:
                        title = f"{title} / {attr_txt}"
                    found.setdefault(sku, []).append({
                        "sku": sku,
                        "sku_source": sku_info.get("sku_source"),
                        "item_id": str(item.get("id") or item_id),
                        "variation_id": str(variation_for_sync.get("id") or "0"),
                        "title": title,
                        "price": variation_for_sync.get("price") if variation_for_sync.get("price") is not None else item.get("price"),
                        "available_quantity": ml_listing_available_quantity(variation_for_sync),
                        "status": ml_listing_status(item),
                        "listing_type": item.get("listing_type_id") or item.get("listing_type") or "standard",
                        "permalink": item.get("permalink"),
                    })
            else:
                sku_info = ml_extract_sku_with_source_from_item_or_variation(item, None)
                sku = norm_sku(sku_info.get("sku"))
                if sku not in target:
                    continue
                found.setdefault(sku, []).append({
                    "sku": sku,
                    "sku_source": sku_info.get("sku_source"),
                    "item_id": str(item.get("id") or item_id),
                    "variation_id": "0",
                    "title": item.get("title") or sku,
                    "price": item.get("price"),
                    "available_quantity": ml_listing_available_quantity(item),
                    "status": ml_listing_status(item),
                    "listing_type": item.get("listing_type_id") or item.get("listing_type") or "standard",
                    "permalink": item.get("permalink"),
                })
        except Exception:
            # El sync principal ya tiene diagnóstico de errores; este recolector no debe romper el preview completo.
            continue
    return found


def inventory_insert_payload_from_plan(plan: Dict[str, Any], columns: set) -> Dict[str, Any]:
    sku = norm_sku(plan.get("sku"))
    item_type = plan.get("item_type") or "standard"
    payload = {
        "sku": sku,
        "name": plan.get("name") or sku,
        "variant_name": None,
        "category": plan.get("category") or ("COMBOS" if item_type == "bundle" else "ML IMPORT"),
        "stock": int(plan.get("initial_stock_from_ml") or 0),
        "active": True,
        "item_type": item_type,
        "updated_at": now_iso(),
    }
    if "created_at" in columns:
        payload["created_at"] = now_iso()
    # Columnas opcionales: solo se envían si existen realmente en inventory_items.
    if "price" in columns:
        payload["price"] = plan.get("initial_price_from_ml")
    if "precio" in columns:
        payload["precio"] = plan.get("initial_price_from_ml")
    if "sale_price" in columns:
        payload["sale_price"] = plan.get("initial_price_from_ml")
    if "raw_data" in columns:
        payload["raw_data"] = {
            "source": "ml_unmapped_skus_import",
            "ml_candidate": plan.get("ml_candidate"),
            "notes": plan.get("notes"),
            "manual_patches": plan.get("manual_patches"),
        }
    # Evita mandar columnas inexistentes.
    return {k: v for k, v in payload.items() if k in columns or k == "sku"}


def apply_unmapped_import_plan(plan_doc: Dict[str, Any], recalc_bundles: bool = False, sync_ml_after: bool = True, ml_sync_limit: int = 300) -> Dict[str, Any]:
    if not plan_doc.get("ok"):
        raise HTTPException(status_code=400, detail={
            "message": "El preview tiene errores. No aplico cambios.",
            "missing_components": plan_doc.get("missing_components"),
            "row_errors": plan_doc.get("row_errors"),
        })

    columns = inventory_table_columns()
    created_items = []
    skipped_existing = []
    bundle_links = []
    bundle_errors = []

    # 1) Crear todos los inventory_items primero, para que componentes del mismo lote existan.
    for p in plan_doc.get("plans") or []:
        sku = norm_sku(p.get("sku"))
        if not sku:
            continue
        existing = get_item_by_sku(sku)
        if existing:
            skipped_existing.append({"sku": sku, "id": existing.get("id")})
            continue
        payload = inventory_insert_payload_from_plan(p, columns)
        try:
            inserted = sb.table("inventory_items").insert(payload).execute().data or []
            item = inserted[0] if inserted else get_item_by_sku(sku)
            created_items.append({"sku": sku, "id": item.get("id") if item else None, "item_type": p.get("item_type"), "stock": payload.get("stock"), "price": p.get("initial_price_from_ml")})
        except Exception as e:
            raise HTTPException(status_code=500, detail={"message": "Error creando inventory_item", "sku": sku, "error": str(e), "payload": payload})

    # 2) Crear/actualizar componentes de bundles.
    for p in plan_doc.get("plans") or []:
        if p.get("item_type") != "bundle":
            continue
        bundle_sku = norm_sku(p.get("sku"))
        bundle = get_item_by_sku(bundle_sku)
        if not bundle:
            bundle_errors.append({"bundle_sku": bundle_sku, "error": "No existe bundle después de crear"})
            continue
        for c in p.get("components") or []:
            comp_sku = norm_sku(c.get("component_sku"))
            qty = float(c.get("component_qty") or 0)
            comp = get_item_by_sku(comp_sku)
            if not comp:
                bundle_errors.append({"bundle_sku": bundle_sku, "component_sku": comp_sku, "error": "No existe componente"})
                continue
            existing = (
                sb.table("bundle_components")
                .select("id")
                .eq("bundle_item_id", bundle["id"])
                .eq("component_item_id", comp["id"])
                .limit(1)
                .execute()
                .data
                or []
            )
            try:
                if existing:
                    sb.table("bundle_components").update({"quantity": qty}).eq("id", existing[0]["id"]).execute()
                    action = "updated"
                else:
                    sb.table("bundle_components").insert({
                        "bundle_item_id": bundle["id"],
                        "component_item_id": comp["id"],
                        "quantity": qty,
                        "created_at": now_iso(),
                    }).execute()
                    action = "inserted"
                bundle_links.append({"action": action, "bundle_sku": bundle_sku, "component_sku": comp_sku, "quantity": qty})
            except Exception as e:
                bundle_errors.append({"bundle_sku": bundle_sku, "component_sku": comp_sku, "error": str(e)})

        if recalc_bundles:
            try:
                recalc_and_sync_bundle(bundle, dry_run=False, source_meta={"source": "ml_unmapped_skus_import"})
            except Exception as e:
                bundle_errors.append({"bundle_sku": bundle_sku, "error": f"recalc falló: {e}"})

    sync_result = None
    if sync_ml_after:
        sync_result = sync_ml_listings_to_supabase(limit=ml_sync_limit, dry_run=False, details_limit=50, include_raw_data=False)

    return {
        "ok": len(bundle_errors) == 0 and (not sync_result or sync_result.get("ok", False)),
        "app_version": APP_VERSION,
        "created_items_count": len(created_items),
        "created_items": created_items,
        "skipped_existing_count": len(skipped_existing),
        "skipped_existing": skipped_existing[:100],
        "bundle_links_count": len(bundle_links),
        "bundle_links": bundle_links,
        "bundle_errors": bundle_errors,
        "recalc_bundles": recalc_bundles,
        "sync_ml_after": sync_ml_after,
        "sync_result": sync_result,
        "note": "Aplicado. Por default no recalcula stock de bundles para respetar stock inicial desde ML.",
    }


@router.post("/admin/ml/unmapped-skus/import-preview")
async def ml_unmapped_skus_import_preview_endpoint(
    request: Request,
    limit: int = 300,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    raw = await request.body()
    text = decode_request_body_text(raw)
    return build_unmapped_import_plan(text, limit=limit)


@router.post("/admin/ml/unmapped-skus/import-apply")
async def ml_unmapped_skus_import_apply_endpoint(
    request: Request,
    limit: int = 300,
    recalc_bundles: bool = False,
    sync_ml_after: bool = True,
    ml_sync_limit: int = 300,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    raw = await request.body()
    text = decode_request_body_text(raw)
    plan_doc = build_unmapped_import_plan(text, limit=limit)
    return apply_unmapped_import_plan(
        plan_doc,
        recalc_bundles=recalc_bundles,
        sync_ml_after=sync_ml_after,
        ml_sync_limit=ml_sync_limit,
    )

def sync_ml_listings_to_supabase(
    limit: int = 200,
    dry_run: bool = False,
    details_limit: int = 100,
    include_raw_data: bool = False,
) -> Dict[str, Any]:
    item_ids = ml_get_seller_item_ids(limit=limit)
    results: List[Dict[str, Any]] = []
    action_counts: Dict[str, int] = {}
    error_samples: List[Dict[str, Any]] = []
    skipped_samples: List[Dict[str, Any]] = []

    inserted = 0
    updated = 0
    skipped = 0
    partial_errors = 0
    fatal_errors = 0
    actions_seen = 0

    details_limit = max(0, min(int(details_limit or 0), 500))

    def register_action(action: Dict[str, Any]) -> None:
        nonlocal inserted, updated, skipped, partial_errors, actions_seen
        actions_seen += 1
        act = str(action.get("action") or "sin_action")
        action_counts[act] = action_counts.get(act, 0) + 1

        if action.get("ok"):
            if act.startswith("inserted") or act.startswith("dry_run_insert"):
                inserted += 1
            elif act.startswith("updated") or act.startswith("dry_run_update"):
                updated += 1
            elif act.startswith("skipped"):
                skipped += 1
            else:
                skipped += 1
        else:
            if act.startswith("skipped"):
                skipped += 1
                if len(skipped_samples) < 30:
                    skipped_samples.append({
                        "action": act,
                        "item_id": action.get("item_id"),
                        "variation_id": action.get("variation_id"),
                        "sku": action.get("sku"),
                        "reason": action.get("reason"),
                    })
            else:
                partial_errors += 1
                if len(error_samples) < 30:
                    error_samples.append({
                        "action": act,
                        "item_id": action.get("item_id"),
                        "variation_id": action.get("variation_id"),
                        "sku": action.get("sku"),
                        "inventory_item_found": action.get("inventory_item_found"),
                        "error": action.get("error"),
                    })

    for item_id in item_ids:
        item_result = {"item_id": item_id, "actions": []}
        try:
            item = ml_get_item_detail(item_id)
            variations = item.get("variations") or []

            if variations:
                for variation in variations:
                    # En publicaciones con variaciones, ML puede ocultar SELLER_SKU
                    # en el endpoint específico de la variación. Usamos ese detalle
                    # como fuente principal para evitar falsos skipped_no_seller_sku.
                    variation_for_sync = ml_enrich_variation_for_sync(item, variation)
                    action = ml_upsert_marketplace_listing(
                        item,
                        variation=variation_for_sync,
                        dry_run=dry_run,
                        include_raw_data=include_raw_data,
                    )
                    item_result["actions"].append(action)
                    register_action(action)
            else:
                action = ml_upsert_marketplace_listing(
                    item,
                    variation=None,
                    dry_run=dry_run,
                    include_raw_data=include_raw_data,
                )
                item_result["actions"].append(action)
                register_action(action)

            if len(results) < details_limit:
                results.append(item_result)
        except Exception as e:
            fatal_errors += 1
            action_counts["fatal_item_error"] = action_counts.get("fatal_item_error", 0) + 1
            item_result["error"] = str(e)
            if len(error_samples) < 30:
                error_samples.append({
                    "action": "fatal_item_error",
                    "item_id": item_id,
                    "error": str(e),
                })
            if len(results) < details_limit:
                results.append(item_result)

    results_returned = len(results)
    results_truncated = len(item_ids) > results_returned

    return {
        "ok": fatal_errors == 0 and partial_errors == 0,
        "dry_run": dry_run,
        "limit": limit,
        "items_seen": len(item_ids),
        "actions_seen": actions_seen,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "partial_errors": partial_errors,
        "fatal_errors": fatal_errors,
        "errors": fatal_errors,
        "action_counts": dict(sorted(action_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "error_samples": error_samples,
        "skipped_samples": skipped_samples,
        "results_returned": results_returned,
        "results_truncated": results_truncated,
        "details_limit": details_limit,
        "include_raw_data": include_raw_data,
        "results": results,
        "ml_refresh_token_configurado": bool(ML_REFRESH_TOKEN),
    }


@router.post("/erp/sync_ml_listings")
def sync_ml_listings_endpoint(
    limit: int = 200,
    dry_run: bool = False,
    details_limit: int = 100,
    include_raw_data: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return sync_ml_listings_to_supabase(
        limit=limit,
        dry_run=dry_run,
        details_limit=details_limit,
        include_raw_data=include_raw_data,
    )


@router.post("/admin/ml/debug/refresh-token")
def debug_ml_refresh_token_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return ml_refresh_access_token()


@router.get("/admin/ml/debug/config")
def debug_ml_config(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    masked = None
    if ML_ACCESS_TOKEN:
        masked = {"length": len(ML_ACCESS_TOKEN), "start": ML_ACCESS_TOKEN[:4], "end": ML_ACCESS_TOKEN[-4:]}
    return {
        "ok": True,
        "ml_user_id_set": bool(ML_USER_ID),
        "ml_user_id_value": ML_USER_ID,
        "ml_token": masked,
        "ml_refresh_token": mask_secret(ML_REFRESH_TOKEN),
        "ml_client_id_set": bool(ML_CLIENT_ID),
        "ml_client_secret_set": bool(ML_CLIENT_SECRET),
        "can_refresh": ml_can_refresh(),
    }


@router.get("/admin/ml/debug/orders")
def debug_ml_orders(
    limit: int = 5,
    offset: int = 0,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    orders = ml_list_orders(limit=limit, offset=offset)
    return {"ok": True, "ml_user_id": ML_USER_ID, "limit": limit, "offset": offset, "total_returned": len(orders), "items": orders}


@router.get("/admin/ml/reconcile/shipping/{shipping_id}/preview")
def preview_ml_shipping_reconciliation(
    shipping_id: str,
    scan_limit: int = 250,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return ml_reconcile_shipping_preview_data(shipping_id=shipping_id, scan_limit=scan_limit)


@router.post("/admin/ml/reconcile/shipping/{shipping_id}/apply")
def apply_ml_shipping_reconciliation_endpoint(
    shipping_id: str,
    scan_limit: int = 250,
    notify: bool = False,
    block_on_errors: bool = True,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return apply_ml_shipping_reconciliation(
        shipping_id=shipping_id,
        scan_limit=scan_limit,
        notify=notify,
        block_on_errors=block_on_errors,
    )


@router.get("/admin/ml/reconcile/pack/{pack_id}/preview")
def preview_ml_pack_reconciliation(
    pack_id: str,
    scan_limit: int = 250,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return ml_reconcile_pack_preview_data(pack_id=pack_id, scan_limit=scan_limit)


@router.post("/admin/ml/reconcile/pack/{pack_id}/apply")
def apply_ml_pack_reconciliation_endpoint(
    pack_id: str,
    scan_limit: int = 250,
    notify: bool = False,
    block_on_errors: bool = True,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return apply_ml_pack_reconciliation(
        pack_id=pack_id,
        scan_limit=scan_limit,
        notify=notify,
        block_on_errors=block_on_errors,
    )


@router.get("/admin/ml/orders/{order_id}/preview")
def preview_ml_order(
    order_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = ml_get_order(order_id)
    result = apply_ml_order_payload(order, dry_run=True)
    customer = ml_extract_customer(order)
    result["ml_order"] = {
        "id": order.get("id"),
        "status": order.get("status"),
        "date_created": order.get("date_created"),
        "date_closed": order.get("date_closed"),
        "customer_name": customer.get("name"),
        "customer_phone": customer.get("phone"),
        "total": ml_order_total(order),
    }
    return result


@router.post("/admin/ml/orders/{order_id}/apply")
def apply_ml_order(
    order_id: str,
    notify: bool = True,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    order = ml_get_order(order_id)
    result = apply_ml_order_payload(order, dry_run=False)
    if notify and result.get("ok") and not result.get("duplicate"):
        result["whatsapp_notify"] = notify_ml_sale_once(result, order)
    return result


@router.post("/admin/ml/orders/poll")
def poll_ml_orders_endpoint(
    limit: int = 10,
    dry_run: bool = False,
    notify: bool = True,
    apply_existing: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return poll_ml_orders_once(limit=limit, dry_run=dry_run, notify=notify, apply_existing=apply_existing)


@router.get("/admin/ml/orders/recent")
def recent_ml_sales(
    limit: int = 20,
    offset: int = 0,
    exclude_tests: bool = True,
    q: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    fetched = fetch_recent_order_rows("ML", limit=limit, offset=offset, exclude_tests=exclude_tests)
    items = build_recent_ml_items(fetched["rows"])
    items = filter_recent_items(items, q)
    out = paginate_items(items, limit=limit, offset=offset)
    out["exclude_tests"] = exclude_tests
    return out


@router.post("/admin/ml/orders/{order_id}/notify")
def notify_ml_order_endpoint(
    order_id: str,
    force: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    row = get_order_row("ML", order_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No existe orden ML aplicada: {order_id}")
    raw = row.get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {}
    ml_order = raw.get("ml_raw") or {"id": order_id, "status": raw.get("ml_status")}
    if order_whatsapp_notified_ml(row) and not force:
        return {"ok": True, "skipped": True, "reason": "Ya estaba notificada"}

    movs = (
        sb.table("stock_movements")
        .select("*")
        .eq("reference_id", row.get("id"))
        .order("created_at", desc=False)
        .limit(50)
        .execute()
        .data
        or []
    )
    fake_result = {
        "order": row,
        "external_order_id": order_id,
        "sold_lines": raw.get("sold_lines") or [],
        "stock_applied": [{
            "sku": m.get("sku"),
            "old_stock": m.get("previous_stock"),
            "new_stock": m.get("new_stock"),
            "qty_decrement": abs(int(m.get("quantity") or 0)),
        } for m in movs],
    }
    msg = build_ml_sale_whatsapp_message(fake_result, ml_order)
    notify_result = send_whatsapp_admin(msg)
    if notify_result.get("ok"):
        mark_order_whatsapp_notified_ml(row.get("id"), notify_result)
    return {"ok": bool(notify_result.get("ok")), "forced": force, "notify_result": notify_result}





# ============================================================
# ARCA / FACTURACIÓN - V0 ERP
# ============================================================

def arca_prev_month_range() -> Dict[str, str]:
    today = datetime.now(timezone.utc).date()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return {"from": first_prev.isoformat(), "to": last_prev.isoformat()}


def arca_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def arca_money(value: Any) -> str:
    n = arca_float(value)
    return "$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def arca_digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def arca_deep_find(obj: Any, keys: List[str]) -> Optional[Any]:
    wanted = {normalizar(k) for k in keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if normalizar(k) in wanted and v not in [None, ""]:
                return v
        for v in obj.values():
            found = arca_deep_find(v, keys)
            if found not in [None, ""]:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = arca_deep_find(v, keys)
            if found not in [None, ""]:
                return found
    return None


def arca_get_order(channel: str, order_id: str) -> Dict[str, Any]:
    ch = norm_sku(channel).upper()
    oid = norm_sku(order_id)
    rows = (
        sb.table("orders")
        .select("*")
        .eq("channel", ch)
        .eq("external_order_id", oid)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        rows = (
            sb.table("orders")
            .select("*")
            .eq("channel", ch)
            .eq("id", oid)
            .limit(1)
            .execute()
            .data
            or []
        )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No existe orden {ch} {oid}")
    return rows[0]



def arca_ml_pack_id_from_row(row: Dict[str, Any]) -> Optional[str]:
    raw = row_raw_dict(row)
    pack = raw.get("ml_pack_id")
    if pack in [None, ""] and isinstance(raw.get("ml_raw"), dict):
        pack = raw.get("ml_raw", {}).get("pack_id")
    if pack in [None, "", "null"]:
        return None
    return str(pack)


def arca_ml_shipping_id_from_row(row: Dict[str, Any]) -> Optional[str]:
    raw = row_raw_dict(row)
    shipping = raw.get("ml_shipping_id")
    if shipping in [None, ""] and isinstance(raw.get("ml_raw"), dict):
        ship_obj = raw.get("ml_raw", {}).get("shipping") or {}
        if isinstance(ship_obj, dict):
            shipping = ship_obj.get("id")
    if shipping in [None, "", "null"]:
        return None
    return str(shipping)


def arca_extract_ml_buyer_shipping_cost(shipping_id: Any) -> float:
    """
    Mercado Libre no siempre deja el envío cobrado al comprador dentro de order/payments.
    Para facturación, cuando existe shipping_id, consultamos shipment y tomamos un costo real
    de shipping_option. Si no hay dato confiable, devuelve 0 y no inventa envío.
    """
    sid = str(shipping_id or "").strip()
    if not sid:
        return 0.0

    shipment = ml_get_shipment_info_safe(sid)
    if not isinstance(shipment, dict) or shipment.get("_error"):
        return 0.0

    shipping_option = shipment.get("shipping_option") or {}
    if not isinstance(shipping_option, dict):
        shipping_option = {}

    # Prioridad conservadora: valores explícitos de la opción de envío.
    # En ML suele aparecer como list_cost/base_cost/cost según modalidad/respuesta.
    candidates = [
        shipping_option.get("list_cost"),
        shipping_option.get("base_cost"),
        shipping_option.get("cost"),
        shipping_option.get("gross_amount"),
        shipping_option.get("amount"),
        shipment.get("shipping_cost"),
        shipment.get("base_cost"),
        shipment.get("cost"),
    ]

    for value in candidates:
        amount = arca_float(value, 0)
        if amount > 0.50 and amount < 1000000:
            return round(amount, 2)

    return 0.0


def arca_get_ml_group_orders(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Mercado Libre puede guardar una venta real como varias orders API.
    Para facturar hay que agrupar por pack_id; si no existe pack, por shipping_id.
    """
    if str(row.get("channel") or "").upper() != "ML":
        return [row]

    pack_id = arca_ml_pack_id_from_row(row)
    shipping_id = arca_ml_shipping_id_from_row(row)
    rows: List[Dict[str, Any]] = []

    try:
        if pack_id:
            rows = (
                sb.table("orders")
                .select("*")
                .eq("channel", "ML")
                .contains("raw_data", {"ml_pack_id": int(pack_id) if pack_id.isdigit() else pack_id})
                .order("created_at")
                .limit(100)
                .execute()
                .data
                or []
            )
    except Exception:
        rows = []

    # Fallback: algunos registros viejos podrían tener el pack solo dentro de ml_raw.
    if pack_id and not rows:
        try:
            candidates = (
                sb.table("orders")
                .select("*")
                .eq("channel", "ML")
                .eq("customer_name", row.get("customer_name") or "")
                .order("created_at")
                .limit(200)
                .execute()
                .data
                or []
            )
            rows = [r for r in candidates if arca_ml_pack_id_from_row(r) == pack_id]
        except Exception:
            rows = []

    # Si no hay pack, agrupamos por envío solo cuando encuentra más de una order asociada.
    if not rows and shipping_id:
        try:
            rows = (
                sb.table("orders")
                .select("*")
                .eq("channel", "ML")
                .contains("raw_data", {"ml_shipping_id": int(shipping_id) if shipping_id.isdigit() else shipping_id})
                .order("created_at")
                .limit(100)
                .execute()
                .data
                or []
            )
        except Exception:
            rows = []

    if not rows:
        return [row]

    # Evita duplicados por id/external_order_id conservando orden estable.
    seen = set()
    unique = []
    for r in rows:
        key = str(r.get("id") or r.get("external_order_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(r)
    return unique or [row]


def arca_line_from_sold_line(line: Dict[str, Any], source: str = "order_line") -> Optional[Dict[str, Any]]:
    if not isinstance(line, dict):
        return None
    qty = arca_float(line.get("quantity") or line.get("qty") or 1, 1)
    unit = arca_float(
        line.get("unit_price_gross") or line.get("unit_price") or line.get("price") or 0,
        0,
    )
    if unit <= 0:
        return None
    return {
        "sku": line.get("sku") or "",
        "description": line.get("description") or line.get("name") or line.get("title") or line.get("product_name") or line.get("sku") or "Producto",
        "quantity": qty,
        "unit_price_gross": unit,
        "discount_pct": arca_float(line.get("discount_pct") or 0),
        "iva_pct": ARCA_IVA_ALICUOTA,
        "source": source,
    }


def arca_build_lines_from_single_order(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = row_raw_dict(row)
    source_lines = raw.get("sold_lines") or []
    lines: List[Dict[str, Any]] = []
    for line in source_lines:
        normalized = arca_line_from_sold_line(line, "order_line")
        if normalized:
            lines.append(normalized)
    return lines


def arca_consolidate_order_for_invoice(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Devuelve una fila sintética facturable.
    Para ML, agrupa todas las orders del mismo pack/shipment para no facturar una sola order parcial.
    Para TN/manual, devuelve la fila original.
    """
    if str(row.get("channel") or "").upper() != "ML":
        return row

    group_rows = arca_get_ml_group_orders(row)
    if len(group_rows) <= 1:
        return row

    pack_id = arca_ml_pack_id_from_row(row) or arca_ml_pack_id_from_row(group_rows[0])
    shipping_id = arca_ml_shipping_id_from_row(row) or arca_ml_shipping_id_from_row(group_rows[0])
    child_ids = [str(r.get("external_order_id") or "") for r in group_rows if r.get("external_order_id")]

    sold_lines: List[Dict[str, Any]] = []
    total = 0.0
    customer_name = row.get("customer_name") or ""
    customer_phone = row.get("customer_phone")
    first_created = None

    for r in group_rows:
        total += arca_float(r.get("total"), 0)
        if not customer_name or normalizar(customer_name).startswith("mev"):
            customer_name = r.get("customer_name") or customer_name
        customer_phone = customer_phone or r.get("customer_phone")
        raw = row_raw_dict(r)
        if not first_created:
            first_created = raw.get("ml_date_created") or r.get("created_at")
        for ln in raw.get("sold_lines") or []:
            if isinstance(ln, dict):
                sold_lines.append(dict(ln))

    raw0 = row_raw_dict(row)
    grouped_raw = dict(raw0)
    grouped_raw.update({
        "source": "arca_ml_pack_grouped",
        "ml_pack_id": pack_id,
        "ml_shipping_id": shipping_id,
        "ml_order_id": str(pack_id or shipping_id or row.get("external_order_id") or ""),
        "ml_sale_number": str(pack_id or shipping_id or row.get("external_order_id") or ""),
        "ml_child_order_ids": child_ids,
        "sold_lines": sold_lines,
        "ml_date_created": first_created or raw0.get("ml_date_created"),
        "arca_group_rows_count": len(group_rows),
        "arca_group_total": round(total, 2),
    })

    out = dict(row)
    out.update({
        "external_order_id": str(pack_id or shipping_id or row.get("external_order_id") or ""),
        "customer_name": customer_name or row.get("customer_name"),
        "customer_phone": customer_phone,
        "total": round(total, 2),
        "raw_data": grouped_raw,
    })
    return out


def arca_source_number(row: Dict[str, Any]) -> str:
    raw = row_raw_dict(row)
    ch = str(row.get("channel") or "").upper()
    if ch == "TN":
        return str(raw.get("tn_order_number") or row.get("external_order_id") or "")
    if ch == "ML":
        return str(raw.get("ml_sale_number") or raw.get("ml_pack_id") or raw.get("ml_order_id") or row.get("external_order_id") or "")
    return str(row.get("external_order_id") or row.get("id") or "")


def arca_guess_customer(row: Dict[str, Any], override: Optional[ArcaCustomerOverrideIn] = None) -> Dict[str, Any]:
    raw = row_raw_dict(row)
    raw_all = raw.get("tn_raw") or raw.get("ml_raw") or raw

    name = row.get("customer_name") or arca_deep_find(raw_all, ["billing_name", "contact_name", "name", "first_name"])
    doc_number = arca_deep_find(raw_all, [
        "billing_document", "document", "document_number", "doc_number",
        "dni", "cuit", "contact_identification", "identification"
    ])
    doc_type = arca_deep_find(raw_all, ["document_type", "doc_type", "identification_type"])
    iva_condition = arca_deep_find(raw_all, [
        "iva_condition", "condicion_iva", "taxpayer_type", "billing_iva_condition",
        "fiscal_condition"
    ])
    address = arca_deep_find(raw_all, [
        "billing_address", "address", "domicilio", "billing_address_street",
        "contact_address", "shipping_address"
    ])
    email = arca_deep_find(raw_all, ["email", "contact_email", "billing_email"])

    doc_digits = arca_digits(doc_number)
    if not doc_type:
        doc_type = "CUIT" if len(doc_digits) == 11 else ("DNI" if len(doc_digits) in [7, 8] else "")
    if not iva_condition:
        # Sin dato fiscal confiable, no inventamos A: cae a Consumidor Final.
        iva_condition = "Consumidor Final"

    out = {
        "name": str(name or row.get("customer_name") or "Consumidor Final").strip(),
        "doc_type": str(doc_type or "").upper(),
        "doc_number": doc_digits,
        "iva_condition": str(iva_condition or "Consumidor Final").strip(),
        "address": str(address or "").strip(),
        "email": str(email or "").strip(),
    }

    if override:
        if override.name is not None:
            out["name"] = override.name
        if override.doc_type is not None:
            out["doc_type"] = override.doc_type.upper()
        if override.doc_number is not None:
            out["doc_number"] = arca_digits(override.doc_number)
        if override.iva_condition is not None:
            out["iva_condition"] = override.iva_condition
        if override.address is not None:
            out["address"] = override.address
        if override.email is not None:
            out["email"] = override.email

    return out


def arca_invoice_type_for_customer(customer: Dict[str, Any], force_type: Optional[str] = None) -> Dict[str, Any]:
    forced = normalizar(force_type or "")
    if forced == "a":
        return {"letter": "A", "code": 1, "label": "Factura A"}
    if forced == "b":
        return {"letter": "B", "code": 6, "label": "Factura B"}

    cond = normalizar(customer.get("iva_condition"))
    if "resp inscripto" in cond or "responsable inscripto" in cond or "monotrib" in cond:
        return {"letter": "A", "code": 1, "label": "Factura A"}
    return {"letter": "B", "code": 6, "label": "Factura B"}


def arca_gross_line_total(line: Dict[str, Any]) -> float:
    qty = arca_float(line.get("quantity") or line.get("qty") or 1, 1)
    unit = arca_float(line.get("unit_price_gross") or line.get("unit_price") or line.get("price") or 0, 0)
    return round(qty * unit, 2)


def arca_fetch_order_item_lines(order_id: str) -> List[Dict[str, Any]]:
    """
    Recupera líneas persistidas en order_items para facturación/impresión.
    Esto evita depender solo del raw_data del marketplace, que a veces llega incompleto
    o fue populado por SQL de prueba sin precios.
    """
    if not order_id:
        return []

    rows = (
        sb.table("order_items")
        .select("sku,quantity,unit_price,inventory_item_id")
        .eq("order_id", order_id)
        .order("created_at")
        .execute()
        .data
        or []
    )
    if not rows:
        return []

    inv_ids = sorted({r.get("inventory_item_id") for r in rows if r.get("inventory_item_id") is not None})
    inv_by_id = {}
    if inv_ids:
        invs = (
            sb.table("inventory_items")
            .select("id,sku,name")
            .in_("id", inv_ids)
            .execute()
            .data
            or []
        )
        inv_by_id = {i.get("id"): i for i in invs}

    out = []
    for r in rows:
        inv = inv_by_id.get(r.get("inventory_item_id")) or {}
        qty = arca_float(r.get("quantity") or 1, 1)
        unit = arca_float(r.get("unit_price") or 0, 0)
        out.append({
            "sku": r.get("sku") or inv.get("sku") or "",
            "description": inv.get("name") or r.get("sku") or "Producto",
            "quantity": qty,
            "unit_price_gross": unit,
            "discount_pct": 0,
            "iva_pct": ARCA_IVA_ALICUOTA,
            "source": "order_items",
        })
    return out


def arca_build_lines_from_order(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    row = arca_consolidate_order_for_invoice(row)
    lines = arca_build_lines_from_single_order(row)

    # Si raw_data no trajo precios útiles, usamos la tabla order_items del ERP.
    if not lines and row.get("id"):
        try:
            lines = arca_fetch_order_item_lines(str(row.get("id")))
        except Exception:
            lines = []

    line_total = round(sum(arca_gross_line_total(x) for x in lines), 2)
    order_total = arca_float(row.get("total"), 0)

    # Regla fiscal: no prorrateamos ni inventamos precios.
    # Toda factura debe salir de líneas reales con precio real.
    diff = round(order_total - line_total, 2)
    ch = str(row.get("channel") or "").upper()

    # TN puede traer envío cobrado como diferencia de total.
    if lines and diff > 0.50 and ch == "TN":
        lines.append({
            "sku": "envio_tnube",
            "description": "Envío",
            "quantity": 1,
            "unit_price_gross": diff,
            "discount_pct": 0,
            "iva_pct": ARCA_IVA_ALICUOTA,
            "source": "shipping_diff_tn",
        })

    # ML no debe inventar envío por diferencia, pero sí debe facturar el envío si shipment
    # informa un costo real cobrado al comprador. Nunca copiamos el precio de un producto.
    if lines and ch == "ML":
        shipping_id = arca_ml_shipping_id_from_row(row)
        ml_ship_cost = arca_extract_ml_buyer_shipping_cost(shipping_id)
        already_has_shipping = any(str(x.get("sku") or "").lower().startswith("envio") for x in lines)
        if ml_ship_cost > 0.50 and not already_has_shipping:
            lines.append({
                "sku": "envio_me_buyer",
                "description": "Envío por Mercado Envíos",
                "quantity": 1,
                "unit_price_gross": ml_ship_cost,
                "discount_pct": 0,
                "iva_pct": ARCA_IVA_ALICUOTA,
                "source": "ml_shipment_buyer_cost",
            })

    # Diferencia negativa: la venta tiene descuento global respecto de la suma de líneas.
    # No tocamos precio de líneas. Lo dejamos como línea de bonificación global para que el total cierre.
    if lines and diff < -0.50:
        lines.append({
            "sku": "descuento",
            "description": "Descuento / bonificación de la venta",
            "quantity": 1,
            "unit_price_gross": diff,
            "discount_pct": 0,
            "iva_pct": ARCA_IVA_ALICUOTA,
            "source": "global_discount_diff",
        })

    return lines

def arca_calculate_invoice(lines: List[Dict[str, Any]], cbte_letter: str) -> Dict[str, Any]:
    out_lines = []
    total_gross = 0.0
    total_net = 0.0
    total_iva = 0.0

    for line in lines:
        qty = arca_float(line.get("quantity"), 1)
        gross_unit = arca_float(line.get("unit_price_gross"), 0)
        iva_pct = arca_float(line.get("iva_pct"), ARCA_IVA_ALICUOTA)
        gross_total = round(qty * gross_unit, 2)

        if cbte_letter == "A":
            net_unit = round(gross_unit / (1 + iva_pct / 100), 2)
            net_total = round(gross_total / (1 + iva_pct / 100), 2)
            iva_amount = round(gross_total - net_total, 2)
            display_unit = net_unit
            display_subtotal = net_total
        else:
            net_total = gross_total
            iva_amount = round(gross_total - (gross_total / (1 + iva_pct / 100)), 2)
            display_unit = gross_unit
            display_subtotal = gross_total

        total_gross += gross_total
        total_net += net_total if cbte_letter == "A" else gross_total
        total_iva += iva_amount

        out_lines.append({
            **line,
            "quantity": qty,
            "unit_price_display": display_unit,
            "subtotal_display": display_subtotal,
            "subtotal_net": round(gross_total / (1 + iva_pct / 100), 2),
            "iva_amount": iva_amount,
            "total_gross": gross_total,
        })

    if cbte_letter == "A":
        return {
            "lines": out_lines,
            "importe_neto": round(sum(x["subtotal_net"] for x in out_lines), 2),
            "importe_iva": round(sum(x["iva_amount"] for x in out_lines), 2),
            "importe_total": round(sum(x["total_gross"] for x in out_lines), 2),
            "iva_contenido": None,
        }

    total_gross = round(sum(x["total_gross"] for x in out_lines), 2)
    iva_content = round(sum(x["iva_amount"] for x in out_lines), 2)
    return {
        "lines": out_lines,
        "importe_neto": total_gross,
        "importe_iva": 0.0,
        "importe_total": total_gross,
        "iva_contenido": iva_content,
    }


def arca_build_invoice_payload(
    row: Dict[str, Any],
    body: Optional[ArcaInvoiceDraftIn] = None,
) -> Dict[str, Any]:
    body = body or ArcaInvoiceDraftIn()
    row = arca_consolidate_order_for_invoice(row)
    customer = arca_guess_customer(row, body.customer)
    cbte = arca_invoice_type_for_customer(customer, body.force_type)
    lines = arca_build_lines_from_order(row)
    if not lines:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede facturar {row.get('channel')} {arca_source_number(row)}: no hay líneas reales de venta."
        )

    calc = arca_calculate_invoice(lines, cbte["letter"])
    if not calc.get("lines") or arca_float(calc.get("importe_total"), 0) <= 0.50:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede facturar {row.get('channel')} {arca_source_number(row)}: las líneas no tienen precios reales."
        )

    invoice_date = body.invoice_date or datetime.now(timezone.utc).date().isoformat()

    raw = row_raw_dict(row)
    source_meta = build_sync_source_meta(
        channel=row.get("channel") or "",
        external_order_id=row.get("external_order_id") or row.get("id") or "",
        raw_payload=raw,
        customer_name=row.get("customer_name"),
        customer_phone=row.get("customer_phone"),
        erp_order_id=row.get("id"),
    )

    return {
        "status": "draft",
        "environment": ARCA_ENV,
        "source_channel": row.get("channel"),
        "source_order_id": row.get("external_order_id") or row.get("id"),
        "source_order_number": source_meta.get("source_order_number") or arca_source_number(row),
        "source_order_api_id": source_meta.get("source_order_api_id"),
        "source_pack_id": source_meta.get("source_pack_id"),
        "source_shipping_id": source_meta.get("source_shipping_id"),
        "source_customer_name": row.get("customer_name"),
        "source_created_at": source_meta.get("source_created_at"),
        "invoice_date": invoice_date,
        "concept": body.concept or ARCA_DEFAULT_CONCEPTO,
        "cbte_tipo": cbte["code"],
        "cbte_letra": cbte["letter"],
        "cbte_tipo_label": cbte["label"],
        "pto_vta": ARCA_PTO_VTA,
        "cbte_nro": None,
        "cae": None,
        "cae_vto": None,
        "customer_name": customer.get("name"),
        "customer_doc_type": customer.get("doc_type"),
        "customer_doc_number": customer.get("doc_number"),
        "customer_iva_condition": customer.get("iva_condition"),
        "customer_address": customer.get("address"),
        "customer_email": customer.get("email"),
        "importe_neto": calc["importe_neto"],
        "importe_iva": calc["importe_iva"],
        "importe_total": calc["importe_total"],
        "iva_contenido": calc["iva_contenido"],
        "currency": "ARS",
        "lines": calc["lines"],
        "notes": body.notes,
        "raw_data": {
            "order": row,
            "source_meta": source_meta,
            "built_at": now_iso(),
            "v0_warning": "Borrador ERP. No tiene CAE hasta activar emision fiscal ARCA WSFE.",
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }



# ============================================================
# ARCA WSAA / WSFE - emisión real CAE
# ============================================================

def arca_env_is_prod() -> bool:
    return normalizar(ARCA_ENV) in ["prod", "produccion", "producción", "production"]


def arca_wsaa_url() -> str:
    return ARCA_WSAA_URL_PROD if arca_env_is_prod() else ARCA_WSAA_URL_HOMO


def arca_wsfe_url() -> str:
    return ARCA_WSFE_URL_PROD if arca_env_is_prod() else ARCA_WSFE_URL_HOMO


def arca_secret_to_pem(value: str, value_b64: str = "", file_path: str = "") -> bytes:
    if value:
        txt = value.replace("\\n", "\n").strip()
        return txt.encode("utf-8")
    if value_b64:
        return base64.b64decode(value_b64)
    if file_path:
        with open(file_path, "rb") as f:
            return f.read()
    return b""


def arca_ws_ready() -> Dict[str, Any]:
    cert = bool(ARCA_CERT_PEM or ARCA_CERT_BASE64 or ARCA_CERT_FILE)
    key = bool(ARCA_KEY_PEM or ARCA_KEY_BASE64 or ARCA_KEY_FILE)
    cuit = bool(arca_digits(ARCA_EMISOR_CUIT))
    ready = bool(cert and key and cuit and ARCA_PTO_VTA)
    return {
        "ready": ready,
        "emit_enabled": ARCA_EMIT_ENABLED,
        "auto_invoice_enabled": ARCA_AUTO_INVOICE_ENABLED,
        "environment": ARCA_ENV,
        "is_production": arca_env_is_prod(),
        "pto_vta": ARCA_PTO_VTA,
        "cert_configurado": cert,
        "key_configurada": key,
        "cuit_configurado": cuit,
        "wsaa_url": arca_wsaa_url(),
        "wsfe_url": arca_wsfe_url(),
        "service": ARCA_WSAA_SERVICE,
    }


def arca_localname(tag: str) -> str:
    return str(tag or "").split("}")[-1].split(":")[-1]


def arca_find_text_by_localname(root: ET.Element, name: str) -> str:
    wanted = name.lower()
    for el in root.iter():
        if arca_localname(el.tag).lower() == wanted:
            return (el.text or "").strip()
    return ""


def arca_wsaa_tra_xml(service: str = "wsfe") -> bytes:
    tz_ar = timezone(timedelta(hours=-3))
    now = datetime.now(tz_ar)
    gen = (now - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    exp = (now + timedelta(hours=12)).replace(microsecond=0).isoformat()
    unique_id = str(int(time.time()))
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketRequest version="1.0">
  <header>
    <uniqueId>{unique_id}</uniqueId>
    <generationTime>{gen}</generationTime>
    <expirationTime>{exp}</expirationTime>
  </header>
  <service>{service}</service>
</loginTicketRequest>"""
    return xml.encode("utf-8")


def arca_wsaa_sign_cms(tra_xml: bytes) -> str:
    cert_pem = arca_secret_to_pem(ARCA_CERT_PEM, ARCA_CERT_BASE64, ARCA_CERT_FILE)
    key_pem = arca_secret_to_pem(ARCA_KEY_PEM, ARCA_KEY_BASE64, ARCA_KEY_FILE)
    if not cert_pem or not key_pem:
        raise HTTPException(status_code=400, detail="Faltan ARCA_CERT_PEM/BASE64/FILE o ARCA_KEY_PEM/BASE64/FILE")
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    cms = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml)
        .add_signer(cert, key, hashes.SHA256())
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    )
    return base64.b64encode(cms).decode("ascii")


def arca_soap_post(url: str, soap_action: str, body: str) -> str:
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": soap_action,
    }
    r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=ARCA_WS_TIMEOUT_SECONDS)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"ARCA HTTP {r.status_code}: {r.text[:1200]}")
    return r.text


def arca_wsaa_login_ticket(force_refresh: bool = False) -> Dict[str, Any]:
    service = ARCA_WSAA_SERVICE or "wsfe"
    cache_key = f"{ARCA_ENV}:{service}:{ARCA_EMISOR_CUIT}"
    cached = ARCA_WSAA_TA_CACHE.get(cache_key) or {}
    exp_txt = cached.get("expiration_time")
    if cached and not force_refresh and exp_txt:
        try:
            exp = datetime.fromisoformat(exp_txt.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) < (exp.astimezone(timezone.utc) - timedelta(minutes=10)):
                return cached
        except Exception:
            pass

    cms_b64 = arca_wsaa_sign_cms(arca_wsaa_tra_xml(service))
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov">
  <soapenv:Header/>
  <soapenv:Body>
    <wsaa:loginCms>
      <wsaa:in0>{cms_b64}</wsaa:in0>
    </wsaa:loginCms>
  </soapenv:Body>
</soapenv:Envelope>"""
    response = arca_soap_post(arca_wsaa_url(), "", soap)
    root = ET.fromstring(response)
    returned = arca_find_text_by_localname(root, "loginCmsReturn")
    if not returned:
        raise HTTPException(status_code=502, detail=f"ARCA WSAA sin loginCmsReturn: {response[:1200]}")
    returned = html_parser.unescape(returned)
    ta_root = ET.fromstring(returned)
    token = arca_find_text_by_localname(ta_root, "token")
    sign = arca_find_text_by_localname(ta_root, "sign")
    expiration = arca_find_text_by_localname(ta_root, "expirationTime")
    generation = arca_find_text_by_localname(ta_root, "generationTime")
    if not token or not sign:
        raise HTTPException(status_code=502, detail=f"ARCA WSAA TA incompleto: {returned[:1200]}")
    ta = {
        "token": token,
        "sign": sign,
        "generation_time": generation,
        "expiration_time": expiration,
        "service": service,
        "environment": ARCA_ENV,
        "cached_at": now_iso(),
    }
    ARCA_WSAA_TA_CACHE[cache_key] = ta
    return ta


def arca_wsfe_auth_xml(ta: Dict[str, Any]) -> str:
    return f"""<ar:Auth>
  <ar:Token>{html_lib.escape(str(ta.get("token") or ""))}</ar:Token>
  <ar:Sign>{html_lib.escape(str(ta.get("sign") or ""))}</ar:Sign>
  <ar:Cuit>{arca_digits(ARCA_EMISOR_CUIT)}</ar:Cuit>
</ar:Auth>"""


def arca_wsfe_request(method: str, inner_xml: str) -> ET.Element:
    ta = arca_wsaa_login_ticket()
    soap_action = f"http://ar.gov.afip.dif.FEV1/{method}"
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ar="http://ar.gov.afip.dif.FEV1/">
  <soapenv:Header/>
  <soapenv:Body>
    <ar:{method}>
      {arca_wsfe_auth_xml(ta)}
      {inner_xml}
    </ar:{method}>
  </soapenv:Body>
</soapenv:Envelope>"""
    txt = arca_soap_post(arca_wsfe_url(), soap_action, soap)
    return ET.fromstring(txt)


def arca_wsfe_last_voucher(cbte_tipo: int) -> int:
    inner = f"""<ar:PtoVta>{ARCA_PTO_VTA}</ar:PtoVta>
<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"""
    root = arca_wsfe_request("FECompUltimoAutorizado", inner)
    errors = arca_wsfe_errors(root)
    if errors:
        raise HTTPException(status_code=502, detail={"arca_errors": errors})
    nro = arca_find_text_by_localname(root, "CbteNro")
    return int(arca_digits(nro) or "0")


def arca_wsfe_errors(root: ET.Element) -> List[Dict[str, Any]]:
    out = []
    for err in root.iter():
        if arca_localname(err.tag).lower() in ["err", "obs"]:
            code = arca_find_text_by_localname(err, "Code") or arca_find_text_by_localname(err, "CodeMsg")
            msg = arca_find_text_by_localname(err, "Msg")
            if code or msg:
                out.append({"code": code, "msg": msg})
    return out


def arca_doc_tipo_nro(customer: Dict[str, Any], cbte_letter: str) -> Dict[str, int]:
    doc_digits = arca_digits(customer.get("doc_number"))
    doc_type = normalizar(customer.get("doc_type"))
    iva = normalizar(customer.get("iva_condition"))
    if len(doc_digits) == 11 or doc_type == "cuit" or "responsable" in iva or "monotrib" in iva:
        if len(doc_digits) != 11:
            raise HTTPException(status_code=400, detail="Factura A / cliente fiscal requiere CUIT de 11 dígitos")
        return {"doc_tipo": 80, "doc_nro": int(doc_digits)}
    if len(doc_digits) in [7, 8] or doc_type == "dni":
        return {"doc_tipo": 96, "doc_nro": int(doc_digits)}
    return {"doc_tipo": 99, "doc_nro": 0}


def arca_wsfe_amounts(payload: Dict[str, Any]) -> Dict[str, float]:
    lines = payload.get("lines") or []
    net = round(sum(arca_float(x.get("subtotal_net"), 0) for x in lines), 2)
    iva = round(sum(arca_float(x.get("iva_amount"), 0) for x in lines), 2)
    total = round(sum(arca_float(x.get("total_gross"), 0) for x in lines), 2)
    if total <= 0 or net <= 0:
        raise HTTPException(status_code=400, detail="Factura sin importes válidos para WSFE")
    iva = round(total - net, 2)
    return {"net": net, "iva": iva, "total": total}


def arca_wsfe_issue_cae(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not ARCA_EMIT_ENABLED:
        raise HTTPException(status_code=400, detail="ARCA_EMIT_ENABLED=false. Emisión real bloqueada por seguridad.")
    ready = arca_ws_ready()
    if not ready.get("ready"):
        raise HTTPException(status_code=400, detail={"error": "ARCA no está listo para emitir", "ready": ready})

    cbte_tipo = int(payload.get("cbte_tipo") or 0)
    cbte_letter = str(payload.get("cbte_letra") or "").upper()
    if cbte_tipo not in [1, 6]:
        raise HTTPException(status_code=400, detail=f"CbteTipo no soportado aún: {cbte_tipo}")

    customer = {
        "doc_type": payload.get("customer_doc_type"),
        "doc_number": payload.get("customer_doc_number"),
        "iva_condition": payload.get("customer_iva_condition"),
    }
    doc = arca_doc_tipo_nro(customer, cbte_letter)
    amounts = arca_wsfe_amounts(payload)
    cbte_nro = arca_wsfe_last_voucher(cbte_tipo) + 1
    inv_date = str(payload.get("invoice_date") or "")
    if re.match(r"^\d{2}/\d{2}/\d{4}$", inv_date):
        d, m, y = inv_date.split("/")
        fch = f"{y}{m}{d}"
    elif re.match(r"^\d{4}-\d{2}-\d{2}", inv_date):
        fch = inv_date[:10].replace("-", "")
    else:
        fch = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y%m%d")

    inner = f"""<ar:FeCAEReq>
  <ar:FeCabReq>
    <ar:CantReg>1</ar:CantReg>
    <ar:PtoVta>{ARCA_PTO_VTA}</ar:PtoVta>
    <ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>
  </ar:FeCabReq>
  <ar:FeDetReq>
    <ar:FECAEDetRequest>
      <ar:Concepto>1</ar:Concepto>
      <ar:DocTipo>{doc['doc_tipo']}</ar:DocTipo>
      <ar:DocNro>{doc['doc_nro']}</ar:DocNro>
      <ar:CbteDesde>{cbte_nro}</ar:CbteDesde>
      <ar:CbteHasta>{cbte_nro}</ar:CbteHasta>
      <ar:CbteFch>{fch}</ar:CbteFch>
      <ar:ImpTotal>{amounts['total']:.2f}</ar:ImpTotal>
      <ar:ImpTotConc>0.00</ar:ImpTotConc>
      <ar:ImpNeto>{amounts['net']:.2f}</ar:ImpNeto>
      <ar:ImpOpEx>0.00</ar:ImpOpEx>
      <ar:ImpTrib>0.00</ar:ImpTrib>
      <ar:ImpIVA>{amounts['iva']:.2f}</ar:ImpIVA>
      <ar:MonId>PES</ar:MonId>
      <ar:MonCotiz>1.000</ar:MonCotiz>
      <ar:Iva>
        <ar:AlicIva>
          <ar:Id>5</ar:Id>
          <ar:BaseImp>{amounts['net']:.2f}</ar:BaseImp>
          <ar:Importe>{amounts['iva']:.2f}</ar:Importe>
        </ar:AlicIva>
      </ar:Iva>
    </ar:FECAEDetRequest>
  </ar:FeDetReq>
</ar:FeCAEReq>"""
    root = arca_wsfe_request("FECAESolicitar", inner)
    cae = arca_find_text_by_localname(root, "CAE")
    cae_vto = arca_find_text_by_localname(root, "CAEFchVto")
    resultado = arca_find_text_by_localname(root, "Resultado")
    errors = arca_wsfe_errors(root)
    if not cae:
        raise HTTPException(status_code=502, detail={"error": "ARCA no devolvió CAE", "resultado": resultado, "arca_errors": errors})
    return {
        "cae": cae,
        "cae_vto": cae_vto,
        "cbte_nro": cbte_nro,
        "resultado": resultado,
        "arca_errors": errors,
        "wsfe_amounts": amounts,
    }


def arca_save_issued_invoice(payload: Dict[str, Any]) -> Dict[str, Any]:
    existing = arca_existing_invoice(payload["source_channel"], payload["source_order_id"])
    payload["updated_at"] = now_iso()
    if existing:
        if existing.get("cae"):
            return {"inserted": False, "updated": False, "invoice": existing, "duplicate": True}
        updated = sb.table("arca_invoices").update(payload).eq("id", existing.get("id")).execute().data or []
        return {"inserted": False, "updated": True, "invoice": updated[0] if updated else {**existing, **payload}, "duplicate": False}
    inserted = sb.table("arca_invoices").insert(payload).execute().data or []
    return {"inserted": True, "updated": False, "invoice": inserted[0] if inserted else payload, "duplicate": False}

def arca_existing_invoice(source_channel: str, source_order_id: str) -> Optional[Dict[str, Any]]:
    rows = (
        sb.table("arca_invoices")
        .select("*")
        .eq("source_channel", source_channel)
        .eq("source_order_id", str(source_order_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def arca_insert_invoice(payload: Dict[str, Any]) -> Dict[str, Any]:
    existing = arca_existing_invoice(payload["source_channel"], payload["source_order_id"])
    if existing:
        return {"inserted": False, "invoice": existing}
    inserted = sb.table("arca_invoices").insert(payload).execute().data or []
    return {"inserted": True, "invoice": inserted[0] if inserted else payload}


def arca_recent_orders_for_preview(channel: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Trae últimas ventas pagadas para probar armado de facturas SIN CAE.
    No filtra por facturación existente porque se usa únicamente para layout/control,
    no como bandeja fiscal real de pendientes.
    """
    ch = norm_sku(channel).upper()
    lim = max(1, min(int(limit or 10), 50))
    rows = (
        sb.table("orders")
        .select("*")
        .eq("channel", ch)
        .eq("status", "paid")
        .order("created_at", desc=True)
        .limit(lim)
        .execute()
        .data
        or []
    )
    return rows


def arca_preview_payload_for_order(row: Dict[str, Any], force_type: Optional[str] = None) -> Dict[str, Any]:
    body = ArcaInvoiceDraftIn(
        concept=ARCA_DEFAULT_CONCEPTO,
        force_type=force_type,
        notes="Vista previa sin CAE para control de importes/layout. No tiene validez fiscal.",
    )
    payload = arca_build_invoice_payload(row, body)
    payload["status"] = "preview_no_cae"
    payload["cbte_nro"] = None
    payload["cae"] = None
    payload["cae_vto"] = None
    payload["raw_data"]["preview_no_cae"] = True
    payload["raw_data"]["preview_warning"] = "No insertado. No emitido. Solo prueba visual/fiscal previa."
    return payload


def arca_preview_summary_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    lines = payload.get("lines") or []
    return {
        "source_channel": payload.get("source_channel"),
        "source_order_id": payload.get("source_order_id"),
        "source_order_number": payload.get("source_order_number"),
        "invoice_date": payload.get("invoice_date"),
        "cbte_letra": payload.get("cbte_letra"),
        "cbte_tipo": payload.get("cbte_tipo"),
        "cbte_tipo_label": payload.get("cbte_tipo_label"),
        "customer_name": payload.get("customer_name"),
        "customer_doc_type": payload.get("customer_doc_type"),
        "customer_doc_number": payload.get("customer_doc_number"),
        "customer_iva_condition": payload.get("customer_iva_condition"),
        "importe_neto": payload.get("importe_neto"),
        "importe_iva": payload.get("importe_iva"),
        "iva_contenido": payload.get("iva_contenido"),
        "importe_total": payload.get("importe_total"),
        "line_count": len(lines),
        "status": payload.get("status"),
    }


def arca_get_invoice(invoice_id: str) -> Dict[str, Any]:
    rows = (
        sb.table("arca_invoices")
        .select("*")
        .eq("id", invoice_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No existe factura {invoice_id}")
    return rows[0]


def arca_format_date(value: Any) -> str:
    txt = str(value or "").strip()
    if not txt:
        return ""
    # Mantiene DD/MM/AAAA si ya viene así; convierte YYYY-MM-DD o YYYYMMDD.
    try:
        if re.match(r"^\d{8}$", txt):
            return f"{txt[6:8]}/{txt[4:6]}/{txt[0:4]}"
        if re.match(r"^\d{4}-\d{2}-\d{2}", txt):
            return datetime.fromisoformat(txt[:10]).strftime("%d/%m/%Y")
    except Exception:
        pass
    return txt


def arca_invoice_total(inv: Dict[str, Any]) -> float:
    total = arca_float(inv.get("importe_total"), 0)
    if total > 0:
        return total
    raw = inv.get("raw_data") or {}
    if isinstance(raw, dict):
        order = raw.get("order") or {}
        if isinstance(order, dict):
            total = arca_float(order.get("total"), 0)
            if total > 0:
                return total
    return 0.0


def arca_rebuild_lines_from_invoice_source(inv: Dict[str, Any], letter: str) -> List[Dict[str, Any]]:
    """Intenta reconstruir líneas desde orders, agrupando packs ML si aplica."""
    try:
        ch = str(inv.get("source_channel") or "").upper()
        raw = inv.get("raw_data") or {}
        raw_order = raw.get("order") if isinstance(raw, dict) else None

        candidate_ids = []
        if ch == "ML":
            for v in [
                inv.get("source_pack_id"),
                inv.get("source_order_number"),
                inv.get("source_order_id"),
                inv.get("source_order_api_id"),
            ]:
                if v and str(v).lstrip("#") not in candidate_ids:
                    candidate_ids.append(str(v).lstrip("#"))
            if isinstance(raw_order, dict):
                rr = row_raw_dict(raw_order)
                for v in [rr.get("ml_pack_id"), rr.get("ml_order_id"), raw_order.get("external_order_id")]:
                    if v and str(v).lstrip("#") not in candidate_ids:
                        candidate_ids.append(str(v).lstrip("#"))
        else:
            for v in [inv.get("source_order_id"), inv.get("source_order_api_id")]:
                if v and str(v) not in candidate_ids:
                    candidate_ids.append(str(v))

        for oid in candidate_ids:
            try:
                order = arca_get_order(ch, oid)
            except Exception:
                continue
            order = arca_consolidate_order_for_invoice(order)
            lines = arca_build_lines_from_order(order)
            calc = arca_calculate_invoice(lines, letter)
            if arca_float(calc.get("importe_total"), 0) > 0.50:
                return calc.get("lines") or []

        if isinstance(raw_order, dict) and raw_order:
            order = arca_consolidate_order_for_invoice(raw_order)
            lines = arca_build_lines_from_order(order)
            calc = arca_calculate_invoice(lines, letter)
            if arca_float(calc.get("importe_total"), 0) > 0.50:
                return calc.get("lines") or []
    except Exception:
        return []
    return []


def arca_lines_from_invoice_or_raw(inv: Dict[str, Any], letter: str) -> List[Dict[str, Any]]:
    """
    Devuelve líneas listas para imprimir.
    Soporta:
    - facturas nuevas con arca_invoices.lines cargado;
    - facturas test/draft con líneas incompletas;
    - facturas viejas donde quedó raw_data.order;
    - fallback compacto para facturas ya emitidas sin detalle persistido.
    """
    # Para borradores/test de ML, siempre intentamos reconstruir desde el pack completo antes
    # de confiar en líneas guardadas viejas que pueden representar una sola order parcial.
    if str(inv.get("source_channel") or "").upper() == "ML" and not inv.get("cae"):
        rebuilt_first = arca_rebuild_lines_from_invoice_source(inv, letter)
        if rebuilt_first:
            return rebuilt_first

    raw_lines = inv.get("lines") or []
    if isinstance(raw_lines, list) and len(raw_lines) > 0:
        if any(("unit_price_display" not in x or "total_gross" not in x) for x in raw_lines if isinstance(x, dict)):
            normalized = []
            for x in raw_lines:
                if not isinstance(x, dict):
                    continue
                normalized.append({
                    "sku": x.get("sku") or x.get("code") or "",
                    "description": x.get("description") or x.get("name") or x.get("title") or x.get("product_name") or x.get("sku") or "Producto",
                    "quantity": arca_float(x.get("quantity") or x.get("qty") or 1, 1),
                    "unit_price_gross": arca_float(x.get("unit_price_gross") or x.get("unit_price") or x.get("price") or 0, 0),
                    "discount_pct": arca_float(x.get("discount_pct") or 0, 0),
                    "iva_pct": arca_float(x.get("iva_pct") or ARCA_IVA_ALICUOTA, ARCA_IVA_ALICUOTA),
                    "source": x.get("source") or "invoice_line",
                })
            calc = arca_calculate_invoice(normalized, letter)
            if arca_float(calc.get("importe_total"), 0) > 0.50:
                return calc.get("lines") or []
            # Si las líneas existen pero tienen importes cero, seguimos a raw/order fallback.
        else:
            calc_existing = arca_calculate_invoice(raw_lines, letter)
            if arca_float(calc_existing.get("importe_total"), 0) > 0.50:
                return raw_lines

    raw = inv.get("raw_data") or {}
    if isinstance(raw, dict):
        order = raw.get("order") or {}
        if isinstance(order, dict) and order:
            try:
                lines = arca_build_lines_from_order(order)
                calc = arca_calculate_invoice(lines, letter)
                if calc.get("lines"):
                    return calc["lines"]
            except Exception:
                pass

    # Si hay source_channel/source_order_id, intentamos reconstruir desde orders + order_items.
    try:
        ch = str(inv.get("source_channel") or "").upper()
        oid = str(inv.get("source_order_id") or inv.get("source_order_api_id") or "")
        if ch and oid:
            order = arca_get_order(ch, oid)
            lines = arca_build_lines_from_order(order)
            calc = arca_calculate_invoice(lines, letter)
            if calc.get("lines"):
                return calc["lines"]
    except Exception:
        pass

    # Regla fiscal: si no se pudieron reconstruir líneas reales, no inventamos una línea resumen.
    return []

def arca_effective_totals(inv: Dict[str, Any], lines: List[Dict[str, Any]], letter: str) -> Dict[str, Any]:
    calc = arca_calculate_invoice(lines, letter) if lines else {}
    calc_total = arca_float(calc.get("importe_total"), 0)
    inv_total = arca_float(inv.get("importe_total"), 0)
    is_mutable_preview = (not inv.get("cae")) or str(inv.get("status") or "").lower() in ["draft", "test", "preview"]

    if is_mutable_preview and calc_total > 0.50:
        if letter == "A":
            return {
                "importe_neto": arca_float(calc.get("importe_neto"), 0),
                "importe_iva": arca_float(calc.get("importe_iva"), 0),
                "importe_total": calc_total,
                "iva_contenido": None,
            }
        return {
            "importe_neto": calc_total,
            "importe_iva": 0.0,
            "importe_total": calc_total,
            "iva_contenido": arca_float(calc.get("iva_contenido"), 0),
        }

    total = inv_total or calc_total
    if letter == "A":
        neto = arca_float(inv.get("importe_neto"), 0) or arca_float(calc.get("importe_neto"), 0)
        iva = arca_float(inv.get("importe_iva"), 0) or arca_float(calc.get("importe_iva"), 0)
        if total and (not neto or not iva):
            neto = round(total / (1 + ARCA_IVA_ALICUOTA / 100), 2)
            iva = round(total - neto, 2)
        return {"importe_neto": neto, "importe_iva": iva, "importe_total": total, "iva_contenido": None}

    iva_cont = arca_float(inv.get("iva_contenido"), 0) or arca_float(calc.get("iva_contenido"), 0)
    if total and not iva_cont:
        iva_cont = round(total - (total / (1 + ARCA_IVA_ALICUOTA / 100)), 2)
    return {"importe_neto": total, "importe_iva": 0.0, "importe_total": total, "iva_contenido": iva_cont}



ARCA_TEMPLATE_LAST_INFO: Dict[str, Any] = {}


def arca_factura_template_info() -> Dict[str, Any]:
    template_path = os.path.join(os.path.dirname(__file__), "templates", "arca_factura.html")
    info = {
        "app_version": APP_VERSION,
        "cwd": os.getcwd(),
        "module_file": __file__,
        "module_dir": os.path.dirname(__file__),
        "template_path": template_path,
        "template_exists": os.path.exists(template_path),
        "template_source": "fallback_internal",
        "template_size": 0,
        "template_mtime": None,
        "error": None,
    }
    try:
        if info["template_exists"]:
            st = os.stat(template_path)
            info["template_size"] = st.st_size
            info["template_mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat()
            info["template_source"] = "external_file"
    except Exception as e:
        info["error"] = str(e)
        info["template_source"] = "fallback_internal"
    return info


def arca_factura_template_html() -> str:
    """Carga la plantilla HTML editable de comprobantes ARCA.
    Si no existe el archivo externo, usa fallback interno pero lo marca visible.
    """
    global ARCA_TEMPLATE_LAST_INFO
    info = arca_factura_template_info()
    ARCA_TEMPLATE_LAST_INFO = info
    template_path = info["template_path"]
    if info.get("template_source") == "external_file":
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            info["error"] = str(e)
            info["template_source"] = "fallback_internal"
            ARCA_TEMPLATE_LAST_INFO = info
            print(f"[ARCA] No pude leer plantilla externa {template_path}: {e}")
    else:
        print(f"[ARCA] Plantilla externa NO encontrada: {template_path}")
    return ARCA_FACTURA_TEMPLATE_INTERNAL


def arca_template_debug_banner(info: Dict[str, Any]) -> str:
    source = str(info.get("template_source") or "")
    path = html_lib.escape(str(info.get("template_path") or ""))
    exists = html_lib.escape(str(info.get("template_exists")))
    version = html_lib.escape(str(info.get("app_version") or APP_VERSION))
    if source == "external_file":
        return f"<!-- ARCA_TEMPLATE source=external_file path={path} exists={exists} version={version} -->"
    error = html_lib.escape(str(info.get("error") or ""))
    return (
        '<div class="noPrint" style="width:210mm;margin:6px auto 0 auto;text-align:left;'
        'background:#ffe5e5;border:2px solid #b42318;color:#7a0000;padding:8px;'
        'font-family:Arial;font-size:12px;font-weight:700">'
        f'ARCA TEMPLATE: USANDO FALLBACK INTERNO - no se encontró templates/arca_factura.html.<br>'
        f'path: {path} · exists: {exists} · version: {version} · error: {error}'
        '</div>'
    )


def arca_invoice_html(inv: Dict[str, Any]) -> str:
    from string import Template

    raw_letter = str(inv.get("cbte_letra") or "").strip().upper()
    cbte_tipo = int(inv.get("cbte_tipo") or 0)
    letter = raw_letter or ("A" if cbte_tipo in (1, 2, 3, 4) else "B" if cbte_tipo in (6, 7, 8, 9) else "C" if cbte_tipo in (11, 12, 13, 15) else "B")
    tipo_base = str(inv.get("cbte_tipo_label") or "Factura").strip()
    upper_label = tipo_base.upper()
    if "NOTA" in upper_label and "CRED" in upper_label:
        tipo_base = "Nota de Crédito"
    elif "NOTA" in upper_label and "DEB" in upper_label:
        tipo_base = "Nota de Débito"
    elif "RECIB" in upper_label:
        tipo_base = "Recibo"
    elif "REMIT" in upper_label:
        tipo_base = "Remito"
    else:
        tipo_base = "Factura" if "FACT" in upper_label or not tipo_base else tipo_base.replace(f" {letter}", "")
    title = f"{tipo_base} {letter}".strip()

    status = inv.get("status") or "draft"
    cae = inv.get("cae") or ""
    cae_vto = inv.get("cae_vto") or ""
    cbte_nro = inv.get("cbte_nro")
    pto_vta = int(inv.get("pto_vta") or ARCA_PTO_VTA)
    nro = f"{pto_vta:04d}-{int(cbte_nro or 0):08d}" if cbte_nro else f"{pto_vta:04d}-BORRADOR"
    is_a = letter == "A"
    is_fiscal = tipo_base.lower() != "remito"

    lines = arca_lines_from_invoice_or_raw(inv, letter)
    totals = arca_effective_totals(inv, lines, letter)

    item_rows = []
    for line in lines[:14]:
        qty = arca_float(line.get("quantity"), 0)
        unit = arca_float(line.get("unit_price_display"), 0)
        subtotal = arca_float(line.get("subtotal_display"), 0)
        discount_pct = arca_float(line.get("discount_pct"), 0)
        gross = round(qty * unit, 2)
        discount_amount = max(0.0, round(gross - subtotal, 2))
        iva_cell = '<td class="r">21%</td>' if is_a else ''
        item_rows.append(f"""
        <tr>
          <td class="code">{html_lib.escape(str(line.get('sku') or ''))}</td>
          <td class="desc">{html_lib.escape(str(line.get('description') or ''))}</td>
          <td class="c">{qty:g}</td>
          <td class="c">unidades</td>
          <td class="r">{arca_money(unit)}</td>
          <td class="r">{discount_pct:.0f}%</td>
          <td class="r">{arca_money(discount_amount)}</td>
          <td class="r">{arca_money(subtotal)}</td>
          {iva_cell}
        </tr>
        """)

    col_count = 9 if is_a else 8
    for _ in range(max(0, 12 - len(item_rows))):
        item_rows.append(f"<tr class='empty'><td colspan='{col_count}'>&nbsp;</td></tr>")

    headers_extra = "<th>IVA</th>" if is_a else ""
    if is_a:
        totals_block = f"""
          <div class="tot-row"><span>Subtotal:</span><b>{arca_money(totals.get('importe_neto'))}</b></div>
          <div class="tot-row"><span>IVA 21%:</span><b>{arca_money(totals.get('importe_iva'))}</b></div>
          <div class="tot-row"><span>Importe Otros Tributos:</span><b>{arca_money(0)}</b></div>
          <div class="tot-row grand"><span>Importe Total:</span><b>{arca_money(totals.get('importe_total'))}</b></div>
        """
        fiscal_block = ""
    else:
        totals_block = f"""
          <div class="tot-row"><span>Subtotal:</span><b>{arca_money(totals.get('importe_total'))}</b></div>
          <div class="tot-row"><span>Importe Otros Tributos:</span><b>{arca_money(0)}</b></div>
          <div class="tot-row grand"><span>Importe Total:</span><b>{arca_money(totals.get('importe_total'))}</b></div>
        """
        fiscal_block = f"""
          <div class="transparency-title">Régimen de Transparencia Fiscal al Consumidor (Ley 27.743)</div>
          <div class="tax-line"><span>IVA Contenido:</span><b>{arca_money(totals.get('iva_contenido'))}</b></div>
          <div class="tax-line"><span>Otros Impuestos Nacionales Directos:</span><b>{arca_money(0)}</b></div>
        """

    template_info_for_marker = arca_factura_template_info()
    factura_desde = "HTML" if template_info_for_marker.get("template_exists") else "PY"
    warning = "" if cae else f"<div class='draft-stamp'>BORRADOR ERP - NO FISCAL / SIN CAE · Factura desde {factura_desde}</div>"
    qr = "ARCA" if cae else "QR<br>pendiente"
    invoice_date = arca_format_date(inv.get("invoice_date"))
    cae_vto_print = arca_format_date(cae_vto)
    customer_name = str(inv.get("customer_name") or "")
    doc_type = str(inv.get("customer_doc_type") or "").strip()
    doc_num = str(inv.get("customer_doc_number") or "").strip()
    doc_full = f"{doc_type} {doc_num}".strip()
    customer_address = str(inv.get("customer_address") or "")
    source_channel = str(inv.get("source_channel") or "")
    source_number = str(inv.get("source_order_number") or inv.get("source_order_id") or "")
    notes = str(inv.get("notes") or "")
    if warning and notes:
        notes_html = f"{html_lib.escape(notes)}<br>{warning}"
    elif warning:
        notes_html = warning
    else:
        notes_html = html_lib.escape(notes)

    cond_pago = str(inv.get("payment_condition") or inv.get("condicion_pago") or "")
    comprobante_autorizado = "Comprobante Autorizado" if cae else "Comprobante pendiente de autorización"
    cae_label = "CAE Nº:" if is_fiscal else "Código interno:"
    cae_vto_label = "Fecha de Vto. CAE:" if is_fiscal else ""
    legal_note = "Esta Agencia no se responsabiliza por los datos ingresados en el detalle de la operación" if is_fiscal else "Documento interno no fiscal. No reemplaza factura ni comprobante fiscal."

    ctx = {
        "page_title": html_lib.escape(f"{title} {nro}"),
        "logo_url": html_lib.escape(ARCA_LOGO_URL),
        "letter": html_lib.escape(letter),
        "cbte_codigo": f"{cbte_tipo:02d}" if cbte_tipo else "--",
        "title": html_lib.escape(title.upper()),
        "original_label": "ORIGINAL" if is_fiscal else "NO FISCAL",
        "emisor_razon": html_lib.escape(ARCA_EMISOR_RAZON_SOCIAL),
        "emisor_cuit": html_lib.escape(ARCA_EMISOR_CUIT),
        "emisor_cond_iva": html_lib.escape(ARCA_EMISOR_COND_IVA),
        "emisor_dom": html_lib.escape(ARCA_EMISOR_DOMICILIO),
        "emisor_iibb": html_lib.escape(ARCA_EMISOR_IIBB),
        "emisor_inicio": html_lib.escape(ARCA_EMISOR_INICIO),
        "emisor_email": html_lib.escape(ARCA_EMISOR_EMAIL),
        "emisor_web": html_lib.escape(ARCA_EMISOR_WEB),
        "pto_vta": f"{pto_vta:04d}",
        "nro": html_lib.escape(nro),
        "invoice_date": html_lib.escape(invoice_date),
        "periodo_desde": html_lib.escape(str(inv.get("periodo_desde") or "")),
        "periodo_hasta": html_lib.escape(str(inv.get("periodo_hasta") or "")),
        "fecha_vto_pago": html_lib.escape(str(inv.get("fecha_vto_pago") or "")),
        "customer_name": html_lib.escape(customer_name),
        "customer_doc": html_lib.escape(doc_full),
        "customer_iva": html_lib.escape(str(inv.get("customer_iva_condition") or "")),
        "customer_address": html_lib.escape(customer_address),
        "customer_localidad": html_lib.escape(str(inv.get("customer_localidad") or "")),
        "cond_pago": html_lib.escape(cond_pago),
        "concept": html_lib.escape(str(inv.get("concept") or "Productos")),
        "source_channel": html_lib.escape(source_channel),
        "source_number": html_lib.escape(source_number),
        "headers_extra": headers_extra,
        "item_rows": "".join(item_rows),
        "notes_html": notes_html,
        "fiscal_block": fiscal_block,
        "totals_block": totals_block,
        "qr": qr,
        "comprobante_autorizado": html_lib.escape(comprobante_autorizado),
        "status": html_lib.escape(str(status)),
        "cae_label": html_lib.escape(cae_label),
        "cae": html_lib.escape(str(cae)),
        "cae_vto_label": html_lib.escape(cae_vto_label),
        "cae_vto": html_lib.escape(str(cae_vto_print)),
        "legal_note": html_lib.escape(legal_note),
    }
    template_html = arca_factura_template_html()
    debug_banner = arca_template_debug_banner(ARCA_TEMPLATE_LAST_INFO)
    if "<body>" in template_html:
        template_html = template_html.replace("<body>", "<body>" + debug_banner, 1)
    else:
        template_html = debug_banner + template_html
    return Template(template_html).safe_substitute(ctx)


ARCA_FACTURA_TEMPLATE_INTERNAL = '<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n<title>$page_title</title>\n<style>\n@page{size:A4;margin:8mm}\n*{box-sizing:border-box}\nhtml,body{margin:0;padding:0;background:#ddd}\nbody{font-family:Arial,Helvetica,sans-serif;color:#111;font-size:10px}\n.noPrint{width:210mm;margin:8px auto;text-align:right}.noPrint button{padding:8px 13px;border:0;background:#111;color:#fff;border-radius:6px;font-weight:700;cursor:pointer}\n.sheet{width:210mm;height:297mm;margin:0 auto;background:#fff;padding:8mm;position:relative;overflow:hidden;box-shadow:0 0 6px rgba(0,0,0,.28)}\n.box{border:1px solid #222}.top{height:56mm;display:grid;grid-template-columns:44mm 1fr 76mm;position:relative}.logoBox{padding:5mm;border-right:1px solid #222}.logo{width:34mm;height:28mm;object-fit:contain;border:1px solid #222;margin-bottom:3mm}.brandSmall{line-height:1.25;font-size:8px}.letterBox{position:absolute;left:50%;top:-1px;transform:translateX(-50%);width:18mm;height:18mm;border:1px solid #222;background:#fff;text-align:center;padding-top:2.4mm;z-index:5}.letter{font-size:18px;font-weight:900;line-height:1}.cod{font-size:6px;margin-top:1mm}.issuer{padding:31mm 5mm 3mm 6mm;border-right:1px solid #222;line-height:1.45}.invoice{padding:10mm 6mm 4mm 14mm;line-height:1.55}.original{text-align:left;font-weight:800;font-size:9px;margin-bottom:3mm}.invoice h1{margin:0 0 4mm 0;font-size:21px;letter-spacing:.09em}.label{font-weight:800}.period{height:9mm;border-left:1px solid #222;border-right:1px solid #222;border-bottom:1px solid #222;display:grid;grid-template-columns:47mm 40mm 1fr;align-items:center;padding:0 4mm}.customer{min-height:30mm;border-left:1px solid #222;border-right:1px solid #222;border-bottom:1px solid #222;display:grid;grid-template-columns:1fr 1fr;gap:8mm;padding:4mm;line-height:1.55}.items{margin-top:6mm}table{width:100%;border-collapse:collapse;font-size:8.5px}th{background:#cfe7e0;border:1px solid #7da097;padding:1.4mm .9mm;text-align:center;font-weight:800}td{border-left:1px solid #b7c5c1;border-right:1px solid #b7c5c1;padding:1.35mm .9mm;vertical-align:top;line-height:1.18}tbody tr:not(.empty) td{border-bottom:0}tbody tr:last-child td{border-bottom:1px solid #b7c5c1}.empty td{height:6mm;color:#fff;border-bottom:0}.code{width:28mm;text-align:center;word-break:break-word}.desc{width:auto}.c{text-align:center}.r{text-align:right;white-space:nowrap}.bottomArea{position:absolute;left:8mm;right:8mm;bottom:46mm}.observ{min-height:18mm;border:1px solid #222;padding:3mm 4mm;margin-bottom:3mm}.totals{border:1px solid #222;display:grid;grid-template-columns:1fr 66mm;min-height:33mm}.taxes{border-right:1px solid #222;padding:3mm 4mm;align-self:stretch}.transparency-title{font-weight:800;font-style:italic;border-bottom:1px solid #222;margin-bottom:2mm;padding-bottom:1mm}.tax-line{display:flex;gap:9mm;justify-content:flex-start;margin:1.5mm 0}.amounts{padding:4mm 5mm;align-self:center}.tot-row{display:flex;justify-content:space-between;gap:8mm;margin:2mm 0;font-size:10.5px}.grand{font-size:12px;font-weight:900}.auth{position:absolute;left:8mm;right:8mm;bottom:14mm;display:grid;grid-template-columns:30mm 1fr 64mm;gap:6mm;align-items:end}.qr{width:28mm;height:28mm;border:1px solid #222;display:flex;align-items:center;justify-content:center;text-align:center;font-weight:800}.authText{font-size:8px;line-height:1.45}.cae{font-size:8.5px;line-height:1.7}.footer{position:absolute;left:8mm;right:8mm;bottom:5mm;text-align:center;font-size:7px;color:#444}\n.draft-stamp{border:1px solid #111;text-align:center;font-weight:900;padding:1.4mm;margin-top:2mm}\n@media print{html,body{background:#fff}.noPrint{display:none}.sheet{width:194mm;height:281mm;margin:0;padding:0;box-shadow:none}.bottomArea{left:0;right:0}.auth{left:0;right:0}.footer{left:0;right:0}}\n</style>\n</head>\n<body>\n<div class="noPrint"><button onclick="window.print()">Imprimir / Guardar PDF</button></div>\n<div class="sheet">\n  <section class="top box">\n    <div class="logoBox"><div class="logo" style="background:url(\'$logo_url\') center/contain no-repeat"></div><div class="brandSmall"><b>$emisor_razon</b><br>$emisor_dom<br>$emisor_email<br>$emisor_web</div></div>\n    <div class="letterBox"><div class="letter">$letter</div><div class="cod">COD. $cbte_codigo</div></div>\n    <div class="issuer"><div><span class="label">Razón Social:</span> $emisor_razon</div><div><span class="label">Domicilio Comercial:</span> $emisor_dom</div><div><span class="label">Condición frente al IVA:</span> $emisor_cond_iva</div></div>\n    <div class="invoice"><div class="original">$original_label</div><h1>$title</h1><div><span class="label">Punto de Venta:</span> $pto_vta</div><div><span class="label">Nro:</span> $nro</div><div><span class="label">Fecha de Emisión:</span> $invoice_date</div><div><span class="label">CUIT:</span> $emisor_cuit</div><div><span class="label">Ingresos Brutos:</span> $emisor_iibb</div><div><span class="label">Fecha de Inicio de Actividades:</span> $emisor_inicio</div></div>\n  </section>\n  <section class="period"><div><span class="label">Período Facturado Desde:</span> $periodo_desde</div><div><span class="label">Hasta:</span> $periodo_hasta</div><div><span class="label">Fecha de Vto. para el pago:</span> $fecha_vto_pago</div></section>\n  <section class="customer"><div><div><span class="label">CUIT:</span> $customer_doc</div><div><span class="label">Condición frente al IVA:</span> $customer_iva</div><div><span class="label">Domicilio de venta:</span> $customer_address</div></div><div><div><span class="label">Apellido y Nombre / Razón Social:</span> $customer_name</div><div><span class="label">Localidad:</span> $customer_localidad</div><div><span class="label">Condición de pago:</span> $cond_pago</div><div><span class="label">Concepto:</span> $concept</div><div><span class="label">Canal:</span> $source_channel &nbsp; <span class="label">Operación:</span> $source_number</div></div></section>\n  <section class="items"><table><thead><tr><th>Código</th><th>Producto / Servicio</th><th>Cantidad</th><th>U. Medida</th><th>Precio Unit.</th><th>% Bonif.</th><th>Imp. Bonif.</th><th>Subtotal</th>$headers_extra</tr></thead><tbody>$item_rows</tbody></table></section>\n  <section class="bottomArea"><div class="observ"><b>Observaciones:</b><br>$notes_html</div><div class="totals"><div class="taxes">$fiscal_block</div><div class="amounts">$totals_block</div></div></section>\n  <section class="auth"><div class="qr">$qr</div><div class="authText"><b>ARCA</b><br>$comprobante_autorizado<br>Estado ERP: $status<br><small>$legal_note</small></div><div class="cae"><b>$cae_label</b> $cae<br><b>$cae_vto_label</b> $cae_vto</div></section>\n  <div class="footer">Planeta Casa ERP · Facturación · Pág. 1/1</div>\n</div>\n</body>\n</html>\n'


@router.get("/admin/arca/template/debug")
def arca_template_debug_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    info = arca_factura_template_info()
    preview = ""
    if info.get("template_exists"):
        try:
            with open(info["template_path"], "r", encoding="utf-8") as f:
                preview = f.read(500)
        except Exception as e:
            info["read_preview_error"] = str(e)
    info["preview_start"] = preview
    return info


@router.get("/admin/arca/check")
def arca_check_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return {
        "ok": True,
        "version": APP_VERSION,
        "environment": ARCA_ENV,
        "pto_vta": ARCA_PTO_VTA,
        "emisor": {
            "razon_social": ARCA_EMISOR_RAZON_SOCIAL,
            "cuit": ARCA_EMISOR_CUIT,
            "condicion_iva": ARCA_EMISOR_COND_IVA,
            "domicilio": ARCA_EMISOR_DOMICILIO,
            "iibb": ARCA_EMISOR_IIBB,
            "inicio_actividad": ARCA_EMISOR_INICIO,
        },
        "real_wsfe_enabled": ARCA_EMIT_ENABLED,
        "arca_ws_ready": arca_ws_ready(),
        "nota": "ERP: arma borradores y puede emitir CAE real si ARCA_EMIT_ENABLED=true y certificados WSAA/WSFE están configurados.",
    }


@router.get("/admin/arca/facturas")
def arca_list_invoices_endpoint(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    cbte_tipo: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    concept: Optional[str] = Query(default=None),
    limit: int = 100,
    offset: int = 0,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    default_range = arca_prev_month_range()
    df = date_from or default_range["from"]
    dt = date_to or default_range["to"]

    q = sb.table("arca_invoices").select("*").gte("invoice_date", df).lte("invoice_date", dt)
    if cbte_tipo and cbte_tipo != "Todos":
        if str(cbte_tipo).upper() in ["A", "B"]:
            q = q.eq("cbte_letra", str(cbte_tipo).upper())
        else:
            q = q.eq("cbte_tipo_label", cbte_tipo)
    if channel and channel != "Todos":
        q = q.eq("source_channel", channel)
    if concept and concept != "Todos":
        q = q.eq("concept", concept)

    rows = (
        q.order("invoice_date", desc=True)
         .order("created_at", desc=True)
         .range(max(0, int(offset)), max(0, int(offset)) + max(1, min(int(limit or 100), 300)) - 1)
         .execute()
         .data
        or []
    )

    # Para borradores/test ML creados antes del fix de agrupación por pack,
    # mostramos importes recalculados sin tocar la fila emitida/guardada.
    for inv in rows:
        try:
            if str(inv.get("source_channel") or "").upper() == "ML" and not inv.get("cae"):
                letter = inv.get("cbte_letra") or ("A" if int(inv.get("cbte_tipo") or 0) == 1 else "B")
                lines = arca_lines_from_invoice_or_raw(inv, letter)
                totals = arca_effective_totals(inv, lines, letter)
                inv["importe_neto"] = totals.get("importe_neto")
                inv["importe_iva"] = totals.get("importe_iva")
                inv["importe_total"] = totals.get("importe_total")
                inv["iva_contenido"] = totals.get("iva_contenido")
                inv["lines"] = lines
                inv["recalculated_preview"] = True
        except Exception:
            pass

    return {
        "ok": True,
        "date_from": df,
        "date_to": dt,
        "limit": limit,
        "offset": offset,
        "count": len(rows),
        "items": rows,
    }


@router.get("/admin/arca/facturas/{invoice_id}")
def arca_get_invoice_endpoint(
    invoice_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return {"ok": True, "invoice": arca_get_invoice(invoice_id)}


@router.get("/admin/arca/facturas/{invoice_id}/html", response_class=HTMLResponse)
def arca_invoice_html_endpoint(
    invoice_id: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return HTMLResponse(arca_invoice_html(arca_get_invoice(invoice_id)))


@router.get("/admin/arca/previews/test-batch")
def arca_test_batch_previews_endpoint(
    ml_limit: int = 10,
    tn_limit: int = 5,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    items = []
    for ch, lim in [("ML", ml_limit), ("TN", tn_limit)]:
        for row in arca_recent_orders_for_preview(ch, lim):
            try:
                payload = arca_preview_payload_for_order(row)
                items.append(arca_preview_summary_from_payload(payload))
            except Exception as e:
                items.append({
                    "source_channel": ch,
                    "source_order_id": row.get("external_order_id") or row.get("id"),
                    "source_order_number": arca_source_number(row),
                    "customer_name": row.get("customer_name"),
                    "importe_total": row.get("total"),
                    "status": "preview_error",
                    "error": str(e),
                })
    return {
        "ok": True,
        "note": "Previews sin CAE: no insertan, no emiten y no tienen validez fiscal.",
        "ml_limit": ml_limit,
        "tn_limit": tn_limit,
        "count": len(items),
        "items": items,
    }


@router.get("/admin/arca/previews/{channel}/{order_id}/html", response_class=HTMLResponse)
def arca_order_preview_html_endpoint(
    channel: str,
    order_id: str,
    force_type: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    row = arca_get_order(channel, order_id)
    payload = arca_preview_payload_for_order(row, force_type=force_type)
    return HTMLResponse(arca_invoice_html(payload))


@router.get("/admin/arca/orders/{channel}/{order_id}/preview")
def arca_order_preview_endpoint(
    channel: str,
    order_id: str,
    force_type: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    row = arca_get_order(channel, order_id)
    body = ArcaInvoiceDraftIn(force_type=force_type) if force_type else ArcaInvoiceDraftIn()
    return {"ok": True, "invoice_preview": arca_build_invoice_payload(row, body)}


@router.post("/admin/arca/orders/{channel}/{order_id}/draft")
def arca_order_draft_endpoint(
    channel: str,
    order_id: str,
    body: Optional[ArcaInvoiceDraftIn] = None,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    row = arca_get_order(channel, order_id)
    payload = arca_build_invoice_payload(row, body or ArcaInvoiceDraftIn())
    saved = arca_insert_invoice(payload)
    return {
        "ok": True,
        "inserted": saved["inserted"],
        "invoice": saved["invoice"],
        "warning": "Borrador ERP creado. No tiene validez fiscal hasta emisión real ARCA/CAE.",
    }



@router.get("/admin/arca/prod/status")
def arca_prod_status_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    return {"ok": True, **arca_ws_ready()}


@router.post("/admin/arca/orders/{channel}/{order_id}/issue")
def arca_order_issue_endpoint(
    channel: str,
    order_id: str,
    body: Optional[ArcaInvoiceDraftIn] = None,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    row = arca_get_order(channel, order_id)
    payload = arca_build_invoice_payload(row, body or ArcaInvoiceDraftIn())
    wsfe = arca_wsfe_issue_cae(payload)
    payload.update({
        "status": "issued",
        "cbte_nro": wsfe.get("cbte_nro"),
        "cae": wsfe.get("cae"),
        "cae_vto": wsfe.get("cae_vto"),
    })
    payload.setdefault("raw_data", {})
    payload["raw_data"]["wsfe"] = wsfe
    payload["raw_data"]["issued_at"] = now_iso()
    saved = arca_save_issued_invoice(payload)
    return {
        "ok": True,
        "inserted": saved.get("inserted"),
        "updated": saved.get("updated"),
        "duplicate": saved.get("duplicate"),
        "invoice": saved.get("invoice"),
        "wsfe": wsfe,
    }


# ============================================================
# ENDPOINTS BÁSICOS
# ============================================================

@router.get("/")
def root():
    return {
        "ok": True,
        "service": "Planeta Casa ERP Admin Standalone",
        "version": APP_VERSION,
        "note": "No toca app_v2.py. Ejecutando ERP/Admin separado."
    }


@router.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "app_version": APP_VERSION,
        "erp_admin_version": APP_VERSION,
        "supabase_configurado": bool(sb),
        "admin_token_configurado": bool(ADMIN_TOKEN),
        "tn_orders_store_id": TN_ORDERS_STORE_ID,
        "tn_products_store_id": TN_PRODUCTS_STORE_ID,
        "tn_token_configurado": bool(TN_TOKEN),
        "ml_user_id_configurado": bool(ML_USER_ID),
        "ml_token_configurado": bool(ML_ACCESS_TOKEN),
        "ml_refresh_token_configurado": bool(ML_REFRESH_TOKEN),
        "ml_can_refresh": ml_can_refresh(),
        "tn_auto_poll_enabled": TN_AUTO_POLL_ENABLED,
        "tn_auto_poll_interval_seconds": TN_AUTO_POLL_INTERVAL_SECONDS,
        "tn_auto_poll_limit": TN_AUTO_POLL_LIMIT,
        "tn_auto_poll_lookback_minutes": TN_AUTO_POLL_LOOKBACK_MINUTES,
        "ml_auto_poll_enabled": ML_AUTO_POLL_ENABLED,
        "ml_auto_poll_interval_seconds": ML_AUTO_POLL_INTERVAL_SECONDS,
        "ml_auto_poll_limit": ML_AUTO_POLL_LIMIT,
        "ml_auto_poll_scan_limit": ML_AUTO_POLL_SCAN_LIMIT,
        "erp_auto_sync_enabled": ERP_AUTO_SYNC_ENABLED,
        "erp_auto_sync_interval_seconds": ERP_AUTO_SYNC_INTERVAL_SECONDS,
        "erp_auto_sync_limit": ERP_AUTO_SYNC_LIMIT,
        "erp_auto_sync_start_delay_seconds": ERP_AUTO_SYNC_START_DELAY_SECONDS,
        "whatsapp_admin_configurado": bool(HUMAN_NOTIFY_PHONE and WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
        "arca_env": ARCA_ENV,
        "arca_emit_enabled": ARCA_EMIT_ENABLED,
        "arca_auto_invoice_enabled": ARCA_AUTO_INVOICE_ENABLED,
        "arca_ws_ready": arca_ws_ready(),
    }




def mask_secret(value: Any) -> Dict[str, Any]:
    txt = str(value or "")
    if not txt:
        return {"set": False, "length": 0, "start": "", "end": ""}
    return {
        "set": True,
        "length": len(txt),
        "start": txt[:4],
        "end": txt[-4:] if len(txt) >= 4 else txt,
    }


@router.get("/admin/debug/auth")
def debug_auth_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Debug temporal para el panel HTML.
    No devuelve tokens completos. Sirve para detectar si el browser está mandando
    otro token, si viene vacío, o si el endpoint recibe algo distinto a PowerShell.
    """
    supplied = token or x_admin_token or ""
    admin_match = bool(ADMIN_TOKEN) and supplied == ADMIN_TOKEN
    return {
        "ok": True,
        "app_version": APP_VERSION,
        "admin_token_configurado": bool(ADMIN_TOKEN),
        "admin_match": admin_match,
        "supplied_token": mask_secret(supplied),
        "admin_token_expected": {
            "set": bool(ADMIN_TOKEN),
            "length": len(ADMIN_TOKEN or ""),
            # No exponemos inicio/fin del token real salvo que ya haya matcheado.
            "start": ADMIN_TOKEN[:4] if admin_match else "****",
            "end": ADMIN_TOKEN[-4:] if admin_match and len(ADMIN_TOKEN) >= 4 else "****",
        },
        "ids": {
            "tn_user_id": TN_USER_ID,
            "tn_store_id": TN_STORE_ID,
            "tn_orders_store_id": TN_ORDERS_STORE_ID,
            "tn_products_store_id": TN_PRODUCTS_STORE_ID,
            "ml_user_id": ML_USER_ID,
        },
        "tokens_configurados": {
            "tn_token": mask_secret(TN_TOKEN),
            "ml_access_token": mask_secret(ML_ACCESS_TOKEN),
            "ml_refresh_token": mask_secret(ML_REFRESH_TOKEN),
            "ml_client_id": mask_secret(ML_CLIENT_ID),
            "ml_client_secret": mask_secret(ML_CLIENT_SECRET),
            "whatsapp_token": mask_secret(WHATSAPP_TOKEN),
            "human_notify_phone": mask_secret(HUMAN_NOTIFY_PHONE),
        },
    }


def is_sensitive_env_key(key: str) -> bool:
    k = str(key or "").upper()
    sensitive_words = [
        "TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "AUTH",
        "SUPABASE", "SERVICE_ROLE", "BEARER", "ACCESS", "REFRESH",
        "DATABASE", "DB_", "PRIVATE", "CLIENT_SECRET", "WEBHOOK"
    ]
    return any(w in k for w in sensitive_words)


def mask_env_value(key: str, value: Any) -> Dict[str, Any]:
    txt = str(value or "")
    if txt == "":
        return {"set": False, "length": 0, "value": ""}

    if is_sensitive_env_key(key):
        return {
            "set": True,
            "length": len(txt),
            "start": txt[:4],
            "end": txt[-4:] if len(txt) >= 4 else txt,
            "masked": True,
        }

    # Variables no sensibles: mostramos valor completo para debug real de IDs/flags.
    return {
        "set": True,
        "length": len(txt),
        "value": txt,
        "masked": False,
    }


@router.get("/admin/debug/env")
def debug_env_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Debug fuerte para Fallas/jobs.
    Devuelve TODAS las variables de entorno, pero enmascara tokens/secrets.
    Se usa antes de ejecutar sync jobs para verificar el entorno real de Render.
    """
    supplied = token or x_admin_token or ""
    require_admin(token, x_admin_token)

    env_items = {
        k: mask_env_value(k, v)
        for k, v in sorted(os.environ.items(), key=lambda kv: kv[0].upper())
    }

    return {
        "ok": True,
        "app_version": APP_VERSION,
        "generated_at": now_iso(),
        "admin_token_received": mask_secret(supplied),
        "admin_token_match": bool(ADMIN_TOKEN) and supplied == ADMIN_TOKEN,
        "resolved_runtime_values": {
            "TN_STORE_ID": TN_STORE_ID,
            "TN_USER_ID": TN_USER_ID,
            "TN_ORDERS_STORE_ID": TN_ORDERS_STORE_ID,
            "TN_PRODUCTS_STORE_ID": TN_PRODUCTS_STORE_ID,
            "TN_TOKEN": mask_secret(TN_TOKEN),
            "ML_USER_ID": ML_USER_ID,
            "ML_ACCESS_TOKEN": mask_secret(ML_ACCESS_TOKEN),
            "ML_REFRESH_TOKEN": mask_secret(ML_REFRESH_TOKEN),
            "ML_CLIENT_ID": mask_secret(ML_CLIENT_ID),
            "ML_CLIENT_SECRET": mask_secret(ML_CLIENT_SECRET),
            "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
            "WHATSAPP_TOKEN": mask_secret(WHATSAPP_TOKEN),
            "GRAPH_API_VERSION": GRAPH_API_VERSION,
            "HUMAN_NOTIFY_PHONE": mask_secret(HUMAN_NOTIFY_PHONE),
            "ADMIN_TOKEN": mask_secret(ADMIN_TOKEN),
        },
        "environment": env_items,
    }


@router.get("/admin/tn/debug/products-auth")
def tn_debug_products_auth_endpoint(
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Debug SOLO LECTURA para Tienda Nube products/variants.
    No ejecuta jobs, no descuenta stock y no hace PUT.
    Sirve para separar si el 401 viene del token/contexto products o del PUT de variants.
    """
    require_admin(token, x_admin_token)

    if not TN_PRODUCTS_STORE_ID:
        raise HTTPException(status_code=500, detail="TN_PRODUCTS_STORE_ID no configurado")
    if not TN_TOKEN:
        raise HTTPException(status_code=500, detail="TN_TOKEN/TN_ACCESS_TOKEN no configurado")

    headers = tn_headers()
    products_url = f"https://api.tiendanube.com/v1/{TN_PRODUCTS_STORE_ID}/products"
    params = {"page": 1, "per_page": 1}

    result = {
        "ok": False,
        "app_version": APP_VERSION,
        "generated_at": now_iso(),
        "test_type": "read_only_get_products",
        "tn_runtime": {
            "TN_STORE_ID": TN_STORE_ID,
            "TN_USER_ID": TN_USER_ID,
            "TN_ORDERS_STORE_ID": TN_ORDERS_STORE_ID,
            "TN_PRODUCTS_STORE_ID": TN_PRODUCTS_STORE_ID,
            "TN_TOKEN": mask_secret(TN_TOKEN),
            "header_authentication_masked": mask_secret(headers.get("Authentication", "")),
            "user_agent": headers.get("User-Agent"),
        },
        "request": {
            "method": "GET",
            "url": products_url,
            "params": params,
        },
        "products_get": None,
        "pending_job_probe": None,
    }

    try:
        r = requests.get(products_url, headers=headers, params=params, timeout=45)
        body_text = r.text or ""
        try:
            body_json = r.json()
        except Exception:
            body_json = None

        result["products_get"] = {
            "status_code": r.status_code,
            "ok": 200 <= r.status_code < 300,
            "body_preview": body_text[:2000],
            "body_json_type": type(body_json).__name__ if body_json is not None else None,
        }
        result["ok"] = 200 <= r.status_code < 300

        # Si products responde OK, aprovechamos para probar SOLO LECTURA del variant del primer job pendiente/fallido TN.
        # Esto no escribe nada. Ayuda a saber si la ruta product/variant existe y si el problema es solo PUT.
        if result["ok"] and sb:
            jobs = (
                sb.table("sync_jobs")
                .select("*")
                .eq("marketplace", "TN")
                .in_("status", ["pending", "failed_retry", "manual_review"])
                .order("created_at", desc=False)
                .limit(1)
                .execute()
                .data
                or []
            )
            if jobs:
                job = jobs[0]
                listing_id = job.get("listing_id") or job.get("external_product_id")
                variant_id = job.get("variant_id") or job.get("external_variant_id")
                probe = {
                    "job_id": job.get("id"),
                    "sku": job.get("sku"),
                    "status": job.get("status"),
                    "attempts": job.get("attempts"),
                    "listing_id": listing_id,
                    "variant_id": variant_id,
                    "get_product": None,
                    "get_variant": None,
                }
                if listing_id:
                    product_url = f"https://api.tiendanube.com/v1/{TN_PRODUCTS_STORE_ID}/products/{listing_id}"
                    rp = requests.get(product_url, headers=headers, timeout=45)
                    probe["get_product"] = {
                        "url": product_url,
                        "status_code": rp.status_code,
                        "ok": 200 <= rp.status_code < 300,
                        "body_preview": (rp.text or "")[:2000],
                    }

                if listing_id and variant_id:
                    variant_url = f"https://api.tiendanube.com/v1/{TN_PRODUCTS_STORE_ID}/products/{listing_id}/variants/{variant_id}"
                    rv = requests.get(variant_url, headers=headers, timeout=45)
                    probe["get_variant"] = {
                        "url": variant_url,
                        "status_code": rv.status_code,
                        "ok": 200 <= rv.status_code < 300,
                        "body_preview": (rv.text or "")[:2000],
                    }

                result["pending_job_probe"] = probe
            else:
                result["pending_job_probe"] = {"found": False, "message": "No hay job TN pendiente/fallido/manual_review para probar variant"}

        return result

    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result


@router.post("/admin/whatsapp/test")
def whatsapp_test_endpoint(
    text: str = "Prueba de aviso ERP Planeta Casa",
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    result = send_whatsapp_admin(text)
    return {
        "ok": bool(result.get("ok")),
        "configured": bool(HUMAN_NOTIFY_PHONE and WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
        "notify_result": result,
    }




def get_marketplace_listings_by_skus(skus: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Publicaciones TN/ML agrupadas por SKU para Inventario editable.
    Mantiene autosync operativo y agrega listings al search de inventario.
    """
    clean = sorted({norm_sku(s) for s in (skus or []) if norm_sku(s)})
    if not clean:
        return {}

    rows = (
        sb.table("marketplace_listings")
        .select("*")
        .in_("sku", clean)
        .execute()
        .data
        or []
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sku = norm_sku(r.get("sku"))
        if not sku:
            continue
        grouped.setdefault(sku, []).append(r)

    for sku, items in grouped.items():
        items.sort(key=lambda x: (
            str(x.get("marketplace") or ""),
            str(x.get("external_product_id") or x.get("external_full_id") or ""),
            str(x.get("external_variant_id") or ""),
        ))

    return grouped


@router.get("/admin/inventory/search")
def search_inventory(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    marketplace: str = "",
    include_listings: bool = True,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    qn = normalizar(q)
    limit = max(1, min(int(limit or 20), 500))
    offset = max(0, int(offset or 0))
    market = norm_sku(marketplace).upper()

    rows = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .order("sku")
        .limit(5000)
        .execute()
        .data
        or []
    )

    if qn:
        filtered = []
        tokens = qn.split()
        for r in rows:
            text = normalizar(f"{r.get('sku','')} {r.get('name','')} {r.get('variant_name','')} {r.get('category','')}")
            if all(t in text for t in tokens):
                filtered.append(r)
        rows = filtered

    # Si se pide marketplace, mostramos solo SKUs que tengan al menos una publicación de ese canal.
    if market:
        sku_rows = (
            sb.table("marketplace_listings")
            .select("sku")
            .eq("marketplace", market)
            .limit(10000)
            .execute()
            .data
            or []
        )
        market_skus = {norm_sku(r.get("sku")) for r in sku_rows if r.get("sku")}
        rows = [r for r in rows if norm_sku(r.get("sku")) in market_skus]

    total_items = len(rows)
    items = [dict(r) for r in rows[offset:offset + limit]]

    publications_visible = 0
    if include_listings and items:
        listings_by_sku = get_marketplace_listings_by_skus([r.get("sku") for r in items])
        for r in items:
            listings = listings_by_sku.get(norm_sku(r.get("sku")), [])
            if market:
                listings = [l for l in listings if norm_sku(l.get("marketplace")).upper() == market]
            r["listings"] = listings
            publications_visible += len(listings)

    return {
        "ok": True,
        "total": total_items,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total_items,
        "include_listings": include_listings,
        "marketplace": market,
        "publications_visible": publications_visible,
        "items": items,
    }


@router.get("/admin/inventory/{sku}")
def get_inventory_item(
    sku: str,
    include_listings: bool = True,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item = get_item_by_sku(sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"No existe SKU: {sku}")

    item = dict(item)
    if include_listings:
        item["listings"] = get_marketplace_listings_by_skus([sku]).get(norm_sku(sku), [])

    return {"ok": True, "item": item}


# ============================================================
# COMBOS
# ============================================================

@router.post("/admin/bundles/upsert-bundle")
def upsert_bundle_item(
    req: BundleUpsertIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    sku = norm_sku(req.sku)
    name = str(req.name or "").strip()
    if not sku:
        raise HTTPException(status_code=400, detail="SKU obligatorio")
    if not name:
        raise HTTPException(status_code=400, detail="Nombre obligatorio")

    existing = get_item_by_sku(sku)
    payload = {
        "name": name,
        "variant_name": (req.variant_name or None),
        "category": (req.category or "COMBOS"),
        "stock": int(req.stock or 0),
        "active": bool(req.active),
        "item_type": "bundle",
        "updated_at": now_iso(),
    }

    if existing:
        # Alta idempotente: si ya existe, lo convierte/actualiza como combo vendible.
        sb.table("inventory_items").update(payload).eq("id", existing["id"]).execute()
        item = get_item_by_sku(sku)
        return {"ok": True, "action": "updated_existing", "item": item}

    row = {"sku": sku, **payload}
    inserted = sb.table("inventory_items").insert(row).execute().data or []
    item = inserted[0] if inserted else get_item_by_sku(sku)
    return {"ok": True, "action": "inserted", "item": item}


@router.get("/admin/bundles")
def list_bundles(
    q: str = "",
    limit: int = 100,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    component_rows = (
        sb.table("bundle_components")
        .select("bundle_item_id")
        .execute()
        .data
        or []
    )
    bundle_ids = sorted({r["bundle_item_id"] for r in component_rows})

    if not bundle_ids:
        return {"ok": True, "total": 0, "items": []}

    bundles = (
        sb.table("inventory_items")
        .select(q_inventory_base())
        .in_("id", bundle_ids)
        .execute()
        .data
        or []
    )

    qn = normalizar(q)
    if qn:
        bundles = [
            b for b in bundles
            if qn in normalizar(f"{b.get('sku','')} {b.get('name','')} {b.get('variant_name','')}")
        ]

    out = []
    for b in bundles[:max(1, min(limit, 500))]:
        comps = get_bundle_components_by_bundle_id(b["id"])
        out.append({
            **b,
            "components_count": len(comps),
            "available_to_sell": calc_bundle_available_from_components(comps),
        })

    out.sort(key=lambda x: x.get("sku") or "")
    return {"ok": True, "total": len(out), "items": out}


@router.get("/admin/bundles/{bundle_sku}")
def get_bundle(
    bundle_sku: str,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    data = get_bundle_components_by_sku(bundle_sku)
    return {"ok": True, **data}


@router.post("/admin/bundles/component")
def add_bundle_component(
    req: BundleComponentIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    bundle_sku = norm_sku(req.bundle_sku)
    component_sku = norm_sku(req.component_sku)

    if bundle_sku == component_sku:
        raise HTTPException(status_code=400, detail="Un combo no puede componerse de sí mismo")

    bundle = get_item_by_sku(bundle_sku)
    if not bundle:
        raise HTTPException(status_code=404, detail=f"No existe SKU combo: {bundle_sku}")

    component = get_item_by_sku(component_sku)
    if not component:
        raise HTTPException(status_code=404, detail=f"No existe SKU componente: {component_sku}")

    existing = (
        sb.table("bundle_components")
        .select("*")
        .eq("bundle_item_id", bundle["id"])
        .eq("component_item_id", component["id"])
        .limit(1)
        .execute()
        .data
        or []
    )

    if existing:
        row_id = existing[0]["id"]
        sb.table("bundle_components").update({
            "quantity": req.quantity,
        }).eq("id", row_id).execute()
        action = "updated"
    else:
        sb.table("bundle_components").insert({
            "bundle_item_id": bundle["id"],
            "component_item_id": component["id"],
            "quantity": req.quantity,
            "created_at": now_iso(),
        }).execute()
        action = "inserted"

    mark_as_bundle_if_needed(bundle["id"])
    recalc = recalc_and_sync_bundle(bundle, dry_run=False)

    return {
        "ok": True,
        "action": action,
        "bundle_sku": bundle_sku,
        "component_sku": component_sku,
        "quantity": req.quantity,
        "recalc": recalc,
    }


@router.delete("/admin/bundles/component")
def delete_bundle_component(
    req: DeleteBundleComponentIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    bundle = get_item_by_sku(req.bundle_sku)
    if not bundle:
        raise HTTPException(status_code=404, detail=f"No existe SKU combo: {req.bundle_sku}")

    component = get_item_by_sku(req.component_sku)
    if not component:
        raise HTTPException(status_code=404, detail=f"No existe SKU componente: {req.component_sku}")

    rows = (
        sb.table("bundle_components")
        .select("*")
        .eq("bundle_item_id", bundle["id"])
        .eq("component_item_id", component["id"])
        .execute()
        .data
        or []
    )

    if not rows:
        return {"ok": True, "deleted": 0, "message": "No había componente para eliminar"}

    for r in rows:
        sb.table("bundle_components").delete().eq("id", r["id"]).execute()

    recalc = recalc_and_sync_bundle(bundle, dry_run=False)

    return {
        "ok": True,
        "deleted": len(rows),
        "bundle_sku": req.bundle_sku,
        "component_sku": req.component_sku,
        "recalc": recalc,
    }


@router.post("/admin/bundles/{bundle_sku}/recalc")
def recalc_bundle_endpoint(
    bundle_sku: str,
    dry_run: bool = False,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    bundle = get_item_by_sku(bundle_sku)
    if not bundle:
        raise HTTPException(status_code=404, detail=f"No existe SKU combo: {bundle_sku}")
    result = recalc_and_sync_bundle(bundle, dry_run=dry_run)
    return {"ok": True, "result": result}


# ============================================================
# STOCK / VENTAS
# ============================================================

@router.post("/admin/stock/set")
def set_stock(
    req: StockSetIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    item = get_item_by_sku(req.sku)
    if not item:
        raise HTTPException(status_code=404, detail=f"No existe SKU: {req.sku}")

    old_stock = int(item.get("stock") or 0)
    result = {
        "sku": item["sku"],
        "old_stock": old_stock,
        "new_stock": req.new_stock,
        "dry_run": req.dry_run,
    }

    if not req.dry_run:
        sb.table("inventory_items").update({
            "stock": req.new_stock,
            "updated_at": now_iso(),
        }).eq("id", item["id"]).execute()

        create_stock_movement(
            sku=item["sku"],
            movement_type="adjustment",
            channel=req.channel,
            quantity=req.new_stock - old_stock,
            previous_stock=old_stock,
            new_stock=req.new_stock,
            reference_id=str(item["id"]),
            reference_type="manual_stock_set",
            notes=req.notes or "Ajuste manual desde ERP Admin",
        )

        result["sync_jobs"] = create_sync_jobs_for_sku(item["sku"], req.new_stock)

        affected = affected_bundle_recalcs_for_component(item["id"], dry_run=False)
        result["affected_bundles"] = affected

    return {"ok": True, "result": result}


@router.post("/admin/orders/manual_sale_multi")
def manual_sale_multi(
    req: ManualSaleMultiIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    """
    Venta manual con varias líneas.
    Pensado para Local físico / WhatsApp: cliente obligatorio y N productos.
    El stock se descuenta por process_order_lines para reutilizar combos, movimientos y sync_jobs.
    """
    require_admin(token, x_admin_token)

    channel = norm_sku(req.channel or "MANUAL").upper()
    external_order_id = norm_sku(req.external_order_id or "")
    if not external_order_id:
        external_order_id = "MANUAL-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    customer_name = norm_sku(req.customer_name)
    if not customer_name:
        raise HTTPException(status_code=400, detail="Cliente obligatorio")

    lines = []
    raw_lines = []
    total = 0.0
    has_price = False

    for idx, line in enumerate(req.lines or [], start=1):
        sku = norm_sku(line.sku)
        if not sku:
            raise HTTPException(status_code=400, detail=f"Línea {idx} sin SKU")

        qty = float(line.quantity or 0)
        if qty <= 0:
            raise HTTPException(status_code=400, detail=f"Cantidad inválida para {sku}: {qty}")

        discount_pct = float(line.discount_pct or 0)
        unit_price_original = line.unit_price
        unit_price_net = None
        subtotal = None

        if unit_price_original is not None:
            has_price = True
            unit_price_original = float(unit_price_original)
            unit_price_net = round(unit_price_original * (1 - discount_pct / 100), 2)
            subtotal = round(unit_price_net * qty, 2)
            total += subtotal

        raw_line = {
            "line_index": idx,
            "sku": sku,
            "quantity": qty,
            "unit_price_original": unit_price_original,
            "discount_pct": discount_pct,
            "unit_price_net": unit_price_net,
            "subtotal": subtotal,
            "name": line.name,
        }
        raw_lines.append(raw_line)

        lines.append({
            "line_index": idx,
            "sku": sku,
            "quantity": qty,
            "unit_price": unit_price_net,
            "name": line.name,
            "raw": raw_line,
        })

    raw_payload = {
        "source": "erp_admin_manual_sale_multi",
        "manual_lines": raw_lines,
        "note": req.note,
        "customer_name": customer_name,
        "customer_phone": req.customer_phone,
        "total": round(total, 2) if has_price else None,
    }

    result = process_order_lines(
        channel=channel,
        external_order_id=external_order_id,
        lines=lines,
        raw_payload=raw_payload,
        dry_run=req.dry_run,
        customer_name=customer_name,
        customer_phone=req.customer_phone,
        total=round(total, 2) if has_price else None,
    )
    result["manual_sale"] = {
        "external_order_id": external_order_id,
        "lines_count": len(lines),
        "total": round(total, 2) if has_price else None,
    }
    return result


@router.post("/admin/orders/manual_sale")
def manual_sale(
    req: ManualSaleIn,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)

    channel = norm_sku(req.channel).upper()
    external_order_id = norm_sku(req.external_order_id)
    sku = norm_sku(req.sku)

    sold_item = get_item_by_sku(sku)
    if not sold_item:
        raise HTTPException(status_code=404, detail=f"No existe SKU vendido: {sku}")

    components = get_bundle_components_by_bundle_id(sold_item["id"])
    is_bundle = bool(components)

    # Si la orden ya existe, no se descuenta.
    existing = (
        sb.table("orders")
        .select("*")
        .eq("channel", channel)
        .eq("external_order_id", external_order_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "message": "La orden ya existía. No se descuenta stock otra vez.",
            "order": existing[0],
        }

    decrements = []

    if is_bundle:
        for row in components:
            comp = row.get("component")
            if not comp:
                raise HTTPException(status_code=400, detail=f"Componente inexistente en bundle_components id={row.get('id')}")
            qty = float(row["quantity"]) * float(req.quantity)
            decrements.append({
                "item": comp,
                "qty": qty,
                "notes": f"Venta combo {sku} x{req.quantity}. Componente {comp['sku']} x{qty}",
            })
    else:
        decrements.append({
            "item": sold_item,
            "qty": req.quantity,
            "notes": f"Venta producto simple {sku} x{req.quantity}",
        })

    # Validación previa: antes de escribir orden/stock, todos los ítems afectados
    # deben existir y tener cantidad válida. Evita ventas a medio aplicar.
    for d in decrements:
        item = d.get("item")
        qty = int(d.get("qty") or 0)
        if not item or not item.get("id") or not item.get("sku"):
            raise HTTPException(status_code=400, detail="Venta cancelada: componente inválido o inexistente")
        if qty <= 0:
            raise HTTPException(status_code=400, detail=f"Venta cancelada: cantidad inválida para {item.get('sku')}")

    preview = []
    for d in decrements:
        item = d["item"]
        old_stock = int(item.get("stock") or 0)
        qty = int(d["qty"])
        preview.append({
            "sku": item["sku"],
            "name": item.get("name"),
            "qty_decrement": qty,
            "old_stock": old_stock,
            "new_stock": old_stock - qty,
            "insufficient_stock": old_stock - qty < 0,
        })

    if req.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "is_bundle": is_bundle,
            "sold_sku": sku,
            "sold_quantity": req.quantity,
            "stock_preview": preview,
        }

    total = req.unit_price * req.quantity if req.unit_price is not None else None
    order_payload = {
        "source": "erp_admin_manual_sale",
        "sold_sku": sku,
        "quantity": req.quantity,
        "unit_price": req.unit_price,
        "total": total,
        "customer_name": req.customer_name,
        "customer_phone": req.customer_phone,
        "is_bundle": is_bundle,
    }
    order_result = safe_insert_order(channel, external_order_id, order_payload)

    if not order_result["inserted"]:
        return {
            "ok": True,
            "duplicate": True,
            "message": "La orden ya existía. No se descuenta stock otra vez.",
            "order": order_result["order"],
        }

    order = order_result["order"]
    order_id = order["id"]

    sb.table("order_items").insert({
        "id": str(uuid.uuid4()),
        "order_id": order_id,
        "inventory_item_id": sold_item["id"],
        "sku": sku,
        "quantity": req.quantity,
        "unit_price": req.unit_price,
        "created_at": now_iso(),
    }).execute()

    applied = []
    affected_bundle_ids = set()

    for d in decrements:
        item = d["item"]
        qty = int(d["qty"])
        res = decrement_item_stock(
            item=item,
            qty_to_decrement=qty,
            channel=channel,
            reference_id=str(order_id),
            reference_type="order",
            notes=d["notes"],
            dry_run=False,
        )
        applied.append(res)

        # Marco combos afectados, pero no recalculo todavía.
        # Así evitamos reportes repetidos y lecturas parciales durante la misma venta.
        for bundle in bundles_that_use_component(item["id"]):
            affected_bundle_ids.add(bundle["id"])

    if is_bundle:
        affected_bundle_ids.add(sold_item["id"])

    affected_bundle_results = []
    if affected_bundle_ids:
        bundles_to_recalc = (
            sb.table("inventory_items")
            .select(q_inventory_base())
            .in_("id", list(affected_bundle_ids))
            .execute()
            .data
            or []
        )

        for bundle in sorted(bundles_to_recalc, key=lambda b: b.get("sku") or ""):
            affected_bundle_results.append(recalc_and_sync_bundle(bundle, dry_run=False, source_meta=source_meta))

    return {
        "ok": True,
        "dry_run": False,
        "duplicate": False,
        "order": order,
        "is_bundle": is_bundle,
        "sold_sku": sku,
        "sold_quantity": req.quantity,
        "stock_applied": applied,
        "affected_bundles": affected_bundle_results,
    }


@router.get("/admin/stock/movements")
def stock_movements(
    sku: str = "",
    limit: int = 100,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    query = sb.table("stock_movements").select("*").order("created_at", desc=True).limit(max(1, min(limit, 500)))
    if sku:
        query = query.eq("sku", sku)
    rows = query.execute().data or []
    return {"ok": True, "total": len(rows), "items": rows}


@router.get("/admin/orders")
def orders(
    limit: int = 100,
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    rows = (
        sb.table("orders")
        .select("*")
        .order("created_at", desc=True)
        .limit(max(1, min(limit, 500)))
        .execute()
        .data
        or []
    )
    return {"ok": True, "total": len(rows), "items": rows}



def ml_extract_header_case_insensitive(headers: Dict[str, Any], names: List[str]) -> Optional[str]:
    if not isinstance(headers, dict):
        return None
    wanted = {n.lower() for n in names}
    for k, v in headers.items():
        if str(k).lower() in wanted and v not in [None, ""]:
            return str(v)
    return None


def build_ml_stock_header_version_debug(
    item_id: str,
    user_product_id: Optional[str] = None,
    quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Test no-op usando el X-Version real que devuelva GET /user-products/{id}/stock.

    Evidencia previa:
    - Sin X-Version: Missing X-Version header.
    - X-Version manual 1/2/3: version mismatch.
    Por eso ahora leemos headers del GET y usamos ese valor exacto.
    """
    item_id = str(item_id or "").strip()
    user_product_id = str(user_product_id or "").strip() or None
    if not item_id and not user_product_id:
        raise HTTPException(status_code=400, detail="Falta item_id o user_product_id")

    item_res = None
    item_json: Dict[str, Any] = {}
    if item_id:
        item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            user_product_id = user_product_id or item_json.get("user_product_id")

    if not user_product_id:
        raise HTTPException(status_code=400, detail="No pude resolver user_product_id")

    stock_before_res = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    stock_before_json = stock_before_res.get("json") or {}
    current_qty = ml_extract_location_quantity(stock_before_json, "selling_address")
    meli_qty = ml_extract_location_quantity(stock_before_json, "meli_facility")

    if quantity is None:
        if current_qty is None:
            raise HTTPException(status_code=400, detail="No pude leer quantity actual de selling_address")
        quantity_to_send = int(current_qty)
    else:
        quantity_to_send = int(quantity)

    headers_seen = stock_before_res.get("headers") or {}
    header_x_version = ml_extract_header_case_insensitive(
        headers_seen,
        ["X-Version", "x-version", "X-Meli-Version", "x-meli-version", "version"],
    )

    payload = {"quantity": quantity_to_send}
    path = f"/user-products/{user_product_id}/stock/type/selling_address"

    attempts = []

    if header_x_version:
        res = ml_debug_write(
            path,
            method="PUT",
            payload=payload,
            timeout=60,
            extra_headers={"X-Version": header_x_version},
        )
        attempts.append({
            "source": "GET /user-products/{id}/stock response header",
            "x_version": header_x_version,
            "payload_name": "quantity_only",
            "method": "PUT",
            "ok": res.get("ok"),
            "status_code": res.get("status_code"),
            "url": res.get("url"),
            "payload": payload,
            "response": res,
        })
    else:
        # No escribimos si no tenemos versión real: devolvemos todos los headers para descubrir el nombre exacto.
        pass

    stock_after = ml_debug_get(f"/user-products/{user_product_id}/stock", timeout=60)
    ok = bool(attempts and attempts[-1].get("ok"))

    return {
        "ok": ok,
        "version": APP_VERSION,
        "mode": "noop_header_xversion_test",
        "item_id": item_id,
        "user_product_id": user_product_id,
        "quantity_sent": quantity_to_send,
        "selling_address_before": current_qty,
        "meli_facility_before": meli_qty,
        "headers_seen_on_stock_get": headers_seen,
        "x_version_detected": header_x_version,
        "stock_before": stock_before_res,
        "attempts": attempts,
        "stock_after": stock_after,
        "note": (
            "Se usó el X-Version devuelto por el GET de stock. La cantidad enviada fue la misma que la actual."
            if header_x_version
            else "El GET de stock no devolvió un header X-Version visible; revisá headers_seen_on_stock_get."
        ),
    }


@router.post("/admin/ml/debug/stock-write-header-version")
def admin_ml_debug_stock_write_header_version_endpoint(
    item_id: Optional[str] = Query(default=None),
    user_product_id: Optional[str] = Query(default=None),
    quantity: Optional[int] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)
    return build_ml_stock_header_version_debug(
        item_id=item_id or "",
        user_product_id=user_product_id,
        quantity=quantity,
    )


@router.post("/admin/ml/debug/stock-write-header-version-by-sku")
def admin_ml_debug_stock_write_header_version_by_sku_endpoint(
    sku: str = Query(...),
    quantity: Optional[int] = Query(default=None),
    token: Optional[str] = Query(default=None),
    x_admin_token: Optional[str] = Header(default=None),
):
    require_admin(token, x_admin_token)
    ml_validate_config(require_user=False)

    rows = ml_find_listing_rows_for_sku(sku, limit=20)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No encontré listing ML para SKU {sku}")

    row = rows[0]
    item_id = str(row.get("external_product_id") or row.get("external_full_id") or row.get("listing_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail=f"Listing ML sin item_id para SKU {sku}")

    raw = row.get("raw_data") or {}
    raw_item = raw.get("item") if isinstance(raw, dict) else None
    if not isinstance(raw_item, dict):
        raw_item = {}

    user_product_id = raw_item.get("user_product_id")
    if not user_product_id and isinstance(raw, dict):
        user_product_id = ml_deep_find_first(raw, ["user_product_id", "userProductId", "user_product"])

    if not user_product_id:
        item_res = ml_debug_get(f"/items/{item_id}", timeout=60)
        item_json = item_res.get("json") or {}
        if isinstance(item_json, dict):
            user_product_id = item_json.get("user_product_id")

    if not user_product_id:
        raise HTTPException(status_code=400, detail=f"No pude resolver user_product_id para SKU {sku}")

    test = build_ml_stock_header_version_debug(
        item_id=item_id,
        user_product_id=str(user_product_id),
        quantity=quantity,
    )
    return {
        "ok": True,
        "version": APP_VERSION,
        "sku": sku,
        "listing_row_id": row.get("id"),
        "external_product_id": item_id,
        "external_variant_id": row.get("external_variant_id"),
        "test": test,
    }

# Cuando se ejecuta este archivo solo, expone los mismos endpoints.
app.include_router(router)
start_tn_auto_poller_once()
start_ml_auto_poller_once()
start_erp_autosync_once()

if __name__ == "__main__":
    uvicorn.run("erp_admin:app", host="0.0.0.0", port=PORT, reload=False)
