# Pendientes facturación ARCA - 2026-05-29

Este documento registra el punto de arranque para continuar el trabajo de facturación ARCA del ERP Planeta Casa.

## Estado al cierre

La plantilla imprimible de factura quedó migrada a HTML externo en el repo backend:

- Repo backend: `gongol1970/planeta_casa_erp`
- Archivo: `templates/arca_factura.html`
- El backend lee la plantilla desde `/opt/render/project/src/templates/arca_factura.html`.
- Se confirmó que ya no está usando el fallback Python.
- La factura quedó visualmente mucho más robusta/gordita y equilibrada.
- El layout actual usa Tahoma, encabezado en dos bloques, letra A/B/C centrada, bloque cliente, tabla de ítems y pie con totales/ARCA/CAE.

## Próximo arranque obligatorio

Mañana continuar con facturación real, tanto para Tienda Nube como para Mercado Libre.

### 1. Completar datos faltantes para facturación

Objetivo: tomar desde TN y ML todos los datos que falten para facturar correctamente.

Revisar especialmente:

- Cliente / razón social.
- Documento: DNI/CUIT cuando esté disponible.
- Condición IVA si se puede inferir o completar.
- Domicilio / localidad.
- Email.
- Teléfono.
- Datos de envío.
- Costo de envío comprador.
- Líneas reales de productos.
- Envíos como ítem facturable cuando corresponda.

### 2. Conectar ERP a ARCA producción

Objetivo: pasar de borradores/test sin CAE a emisión real en producción.

No emitir masivamente sin validar antes:

1. Credenciales ARCA producción.
2. Punto de venta correcto.
3. Último número emitido.
4. Tipo de comprobante correcto.
5. Totales e IVA.
6. CAE y vencimiento CAE.
7. Registro en `arca_invoices`.

Después emitir pendientes reales post-EcommApp.

### 3. Agregar ON/OFF de facturación automática dentro del ERP

Debe existir control visible en el ERP para activar/desactivar facturación automática.

Idea de comportamiento:

- OFF: el sistema captura ventas, pero no emite factura automática.
- ON: el sistema puede facturar automáticamente ventas elegibles.
- Debe ser persistente, no solo localStorage si impacta operación real.

### 4. Quitar botón FACTURAR del listado de ventas

Motivo: evitar que al apretar FACTURAR desde ventas, luego de facturar, el usuario termine saltando al menú ARCA Reporte.

Revisar vistas:

- Ventas ML.
- Ventas TN.

### 5. Mostrar o habilitar FACTURAR según ON/OFF

Regla:

- Si facturación manual/automática está OFF, no mostrar o deshabilitar acciones de facturación.
- Si está ON, mostrar acción solo donde corresponda y con flujo seguro.

### 6. Menú de tres puntos en cada factura del Reporte ARCA

En cada línea del reporte ARCA agregar menú desplegable de acciones tipo tres puntos verticales.

Acciones previstas:

- Nota de Crédito.
- Nota de Débito.
- Factura manual al cliente.

Uso de factura manual al cliente: para casos en que el cliente compró luego por fuera algo adicional.

### 7. Enriquecer base de clientes desde ML/TN

Objetivo: aprovechar datos de plataformas para poblar clientes.

Revisar qué se puede obtener de ML:

- En datos de envío puede aparecer teléfono, especialmente en Flex.
- En Mercado Envíos común puede haber datos limitados.
- Buscar datos útiles en shipment, receiver_address, logistic_type, receiver_phone u otros campos disponibles.

Revisar TN:

- Datos de cliente.
- Billing/shipping.
- Teléfono.
- Email.
- Documento si está disponible.

La idea es poblar una base de clientes en Supabase con datos útiles para facturación y operación, sin inventar datos.

## Reglas importantes

- No asumir datos fiscales si no están.
- No facturar histórico ya facturado por EcommApp.
- Última factura EcommApp: 25/05/2026.
- Corte operativo estimado: 26/05/2026 02:00.
- Primero validar con pocas ventas pendientes reales.
- Las filas test/draft/preview sin CAE pueden limpiarse.
- Las emitidas con CAE no se borran.
- Mercado Libre: agrupar venta real por pack/shipping cuando corresponda; una order API no siempre es venta completa.
- Mercado Libre Full no debe descontar stock master.
- Tienda Nube y ML deben facturar el envío como ítem cuando corresponda.

## Nota visual factura

La plantilla actual quedó aceptada visualmente como base buena. No volver al layout Python/fallback. Seguir ajustando exclusivamente:

- `templates/arca_factura.html`

Si hay que tocar lógica de datos, recién ahí tocar:

- `erp_admin.py`
