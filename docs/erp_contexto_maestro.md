# Planeta Casa ERP - Contexto maestro

Última actualización: 2026-05-28

Este documento existe para poder retomar el proyecto aunque se pierda el historial de chats. Si alguien dice “Nina, desarrollame un ERP para Planeta Casa”, este es el primer archivo que hay que leer.

## 1. Objetivo del proyecto

Planeta Casa necesita un ERP operativo propio para reemplazar gradualmente EcommApp.

El objetivo no es construir un SaaS genérico en este repositorio. Este repo es el ERP real de Planeta Casa. Cuando se construya una versión SaaS/multiusuario, debe ir en otro repositorio y diseñarse multi-tenant desde cero.

El ERP debe resolver:

- Stock master propio.
- Ventas capturadas desde Tienda Nube, Mercado Libre y ventas manuales.
- Combos/kits con componentes.
- Sincronización de stock hacia marketplaces.
- Bot/operación WhatsApp.
- Facturación ARCA.
- Reportes operativos.

## 2. Principio operativo central

Supabase es la fuente de verdad operativa.

Los marketplaces no son la verdad del stock. Mercado Libre y Tienda Nube son canales que se sincronizan desde el ERP.

El flujo correcto es:

1. Capturar venta.
2. Registrar orden en `orders` y líneas en `order_items` cuando aplique.
3. Descontar stock master en `inventory_items` si corresponde.
4. Registrar movimientos en `stock_movements`.
5. Crear `sync_jobs` para marketplaces vinculados.
6. Autosync procesa jobs pendientes.
7. Facturación ARCA se emite o se prepara desde ventas capturadas.

## 3. Arquitectura actual

### Backend

Archivo principal:

- `erp_admin.py`

Tecnología:

- Python.
- FastAPI.
- Supabase Python client.
- Requests para APIs externas.

Hosting:

- Render.
- Servicio: `planeta-casa-erp.onrender.com`.

Importante:

- No tocar `app_v2.py` salvo necesidad real.
- `app_v2.py` es el bot / motor principal del WhatsApp.
- El ERP debe vivir en `erp_admin.py` como router o servicio operativo separado.

### Frontend

Archivo principal:

- `admin_erp.html`

Hosting:

- GitHub Pages.
- Repo: `gongol1970/planeta-casa-chat`.

Estética actual:

- Beige / negro / dorado suave.
- Menú lateral fijo.
- Cards operativas.
- Versionado chico abajo: `Frontend` y `Backend`.

### Base de datos

Supabase.

Tablas clave:

- `inventory_items`
- `marketplace_listings`
- `bundle_components`
- `orders`
- `order_items`
- `stock_movements`
- `sync_jobs`
- `arca_invoices`
- tablas de WhatsApp / CRM cuando aplique

## 4. Tablas clave y rol

### `inventory_items`

Catálogo operativo y stock master.

Campos relevantes:

- `id`
- `sku`
- `name`
- `stock`
- `active`
- `item_type`
- `category`
- `updated_at`

Regla:

- El stock real operativo vive acá.
- El usuario puede subdeclarar stock manualmente como decisión comercial. No modelar colchón separado por ahora.

### `marketplace_listings`

Vincula SKU master con publicaciones/variantes por marketplace.

Campos relevantes:

- `sku`
- `marketplace`
- `external_product_id`
- `external_variant_id`
- `external_full_id`
- `price`
- `stock`
- `status`
- `url`
- `raw_data`

Regla:

- La combinación marketplace + publicación + variante + SKU identifica el vínculo operativo.
- Se usa para crear `sync_jobs` cuando cambia stock/precio.

### `bundle_components`

Define combos/kits.

Regla:

- Un bundle tiene componentes y cantidades.
- El stock vendible del bundle se recalcula como el mínimo posible según stock de sus componentes.
- Si se vende un componente, los bundles que lo usan deben recalcularse.
- Si se vende un bundle, se descuentan sus componentes, no una unidad física independiente.

Caso real aprendido:

- `PCMULTICUEROIMP4x5_M12` estaba mal compuesto apuntando a `PCMULTICUEROIMP4x1_H12`.
- Lo correcto era: `PCMULTICUEROIMP4x5_M12 = PCMULTICUEROIMP4x1_M12 * 5`.
- El ERP se comportó correctamente: recalculó según la composición cargada. El error era de datos, no de motor.

### `orders`

Ventas capturadas por canal.

Campos relevantes:

- `channel`
- `external_order_id`
- `status`
- `customer_name`
- `customer_phone`
- `total`
- `raw_data`

Regla:

- Evitar duplicados por canal + external_order_id.
- En Mercado Libre, una order API no siempre equivale a una venta real completa.

### `order_items`

Líneas vendidas cuando se registran separadas.

Regla:

- Para facturación y reportes, preferir líneas reales cuando existan.
- Si no hay líneas, reconstruir desde `raw_data` o usar fallback controlado.

### `stock_movements`

Bitácora de stock.

Registra:

- ventas,
- ajustes manuales,
- recalculo de bundles,
- referencia de origen.

Regla:

- Sirve para auditoría.
- Las horas deben mostrarse en 24 hs en UI para evitar confusión.

### `sync_jobs`

Cola de sincronización hacia marketplaces.

Estados típicos:

- `pending`
- `failed_retry`
- `done`
- `manual_review`

Regla:

- Si ya existe un job abierto para marketplace/listing/variant, actualizarlo al último stock objetivo en vez de duplicarlo.
- El autosync procesa pendientes. La UI de jobs debe funcionar como monitor, no como consola manual.

### `arca_invoices`

Tabla de facturas / comprobantes ARCA.

Uso actual:

- Facturas emitidas reales.
- Borradores/test sin CAE para validar layout, importes e ítems.

Reglas:

- Las filas `test`, `preview` o `draft` sin CAE se pueden borrar.
- Las emitidas con CAE no se borran.
- Para pruebas sin riesgo fiscal usar `status = test` y `cae = null`.

## 5. Mercado Libre: reglas críticas

### Orders no son ventas

Aprendizaje clave:

- En Mercado Libre, `orders` son unidades API.
- Una venta comercial visible puede llegar en varias órdenes API.
- Puede haber agrupación por `pack_id` o por `shipping_id`.

Prioridad de agrupación para mostrar ventas:

1. `pack_id` / número visible si está disponible.
2. `shipping_id`.
3. `order_id` como último fallback.

La vista de ventas ML debe mostrar:

- número de venta visible cuando exista,
- envío ML,
- órdenes API asociadas,
- cliente,
- método de envío,
- estado de pago normalizado,
- total,
- líneas de productos desplegadas.

### Logística ML

Tipos operativos:

- Mercado Envíos: sale desde Planeta Casa hacia punto de despacho.
- Flex: logística propia o operador propio dentro de AMBA.
- Full: stock ya está en depósito ML.
- A coordinar con comprador: fallback.

Regla crítica:

- Ventas Full no descuentan stock master al capturarse.
- Ese stock ya se descontó cuando se envió mercadería al warehouse de Mercado Libre.

### Stock ML

No confiar en `item.available_quantity` como escritura directa.

Flujo correcto para stock propio:

1. Resolver `user_product_id`.
2. `GET /user-products/{user_product_id}/stock`.
3. Leer header `x-version`.
4. `PUT /user-products/{user_product_id}/stock/type/selling_address` con `X-Version`.
5. Payload: `quantity` con stock objetivo.

No tocar `meli_facility` para Full.

## 6. Tienda Nube: reglas críticas

No asumir consistencia interna absoluta entre endpoints.

Variables usadas:

- `TN_USER_ID`
- `TN_STORE_ID`
- `TN_ORDERS_STORE_ID = TN_USER_ID or TN_STORE_ID`
- `TN_PRODUCTS_STORE_ID`

Aprendizaje real:

- Algunos endpoints funcionaron con un ID y otros con otro.
- Antes de culpar token/copy-paste, verificar qué contexto espera cada endpoint.

Órdenes TN:

- Capturar pagadas.
- Evitar duplicados.
- Registrar orden.
- Descontar stock master.
- Crear sync jobs.

Productos/variantes TN:

- Usar `marketplace_listings` para mapear SKU, publicación, variante, precio, stock y URL.
- El push de stock/precio sale por `sync_jobs`.

## 7. WhatsApp / bot

El bot de WhatsApp no debe mezclarse con el ERP más de lo necesario.

Reglas:

- `app_v2.py` maneja bot / WhatsApp.
- `erp_admin.py` maneja ERP.
- Avisos internos a Gonzalo pueden enviarse por WhatsApp sin contaminar conversaciones de clientes.
- Si el cliente pide humano, derivar.

Importante:

- No mostrar direcciones sensibles.
- Planeta Casa es tienda online, sin showroom.
- Las reglas comerciales del bot viven preferentemente en `conocimiento.txt`, no hardcodeadas.

## 8. Facturación ARCA

### Objetivo

Reemplazar la facturación de EcommApp.

Fase actual:

- Validar layout.
- Validar importes.
- Validar ítems.
- Usar borradores/test sin CAE.
- Luego conectar ARCA real y emitir solo ventas pendientes reales.

### Tipos de comprobante iniciales

- Consumidor Final: Factura B, código 06.
- Monotributista: Factura A, código 01.
- Responsable Inscripto: Factura A, código 01.

### Concepto

Por ahora:

- Productos.

No usar todavía condición de pago. Eso corresponde a una etapa contable/conciliación posterior.

### Envío

El envío se factura como ítem cuando venga en la venta o cuando se detecte diferencia entre total de venta y suma de productos.

Ejemplos reales observados:

- `envio_tnube` para Tienda Nube.
- `envio_me_buyer` para Mercado Envíos.

### Layout factura

Formato deseado:

- A4 vertical.
- Sobrio, blanco y negro.
- Logo configurable más adelante.
- Bloque de letra A/B estilo factura argentina.
- Ítems compactos para que entren hasta 12.
- Sin columna `Importe` por ítem; el importe es final de factura.
- Precio unitario y subtotal con mismo ancho.
- Bonificación angosta.
- Totales y QR/CAE siempre como pie de página.
- Para borradores: mostrar claramente `BORRADOR ERP - NO FISCAL / SIN CAE`.

### Corte EcommApp

Dato operativo:

- Última factura de EcommApp: 25/05/2026.
- Corte operativo estimado: 26/05/2026 02:00.

Para pendientes reales:

- No tomar histórico previo ya facturado por EcommApp.
- Primero validar con ventas test/borrador.
- Luego emitir las ventas ML faltantes reales.

## 9. Pantallas ERP actuales

### Inicio

Debe convertirse progresivamente en dashboard operativo.

Ideas:

- Autosync ON/OFF.
- TN/ML poll ON/OFF.
- Publicaciones inactivas urgentes.
- Jobs fallidos.
- Ventas pendientes de acción logística.

### Inventario

Objetivo:

- Ver stock master.
- Editar stock ERP.
- Ver publicaciones vinculadas TN/ML.
- Guardar stock y generar sync jobs.

Reglas UI:

- Búsqueda vacía debe traer inventario paginado.
- Default: 20 por página.
- Sin barra horizontal innecesaria.
- Guardar stock siempre visible.
- Estados en español:
  - Activa.
  - Pausada.
  - Inactiva.
- Inactiva debe verse rojo fuerte con texto blanco.

### Publicaciones ML / TN

Por ahora pueden reutilizar la pantalla de inventario filtrada por marketplace.

### Ventas ML / TN

Debe permitir operar ventas capturadas y detectar cuáles requieren acción.

Debe mostrar:

- fecha real,
- canal,
- número visible,
- cliente,
- envío/logística,
- estado pago,
- total,
- líneas.

Pendiente:

- Mejor indicador visual para ventas que requieren acción logística.
- Usar estado de envío/entrega de TN y ML.

### Jobs

Debe ser monitor.

Reglas:

- No ejecutar jobs manuales desde la pantalla normal.
- Mostrar estado, SKU, marketplace, target, attempts, error.
- Selector de cantidad.
- Paginado atrás/siguiente.

### ARCA reportes

Debe mostrar:

- fecha desde/hasta.
- default: mes calendario anterior completo.
- filtros por tipo de comprobante, canal, concepto.
- tabla de comprobantes.
- ver/imprimir.

## 10. Automatizaciones actuales

### Pollers

- TN auto poll captura ventas TN.
- ML auto poll captura ventas ML.
- ERP autosync procesa `sync_jobs`.

Variables:

- `TN_AUTO_POLL_ENABLED`
- `TN_AUTO_POLL_INTERVAL_SECONDS`
- `TN_AUTO_POLL_LIMIT`
- `ML_AUTO_POLL_ENABLED`
- `ML_AUTO_POLL_INTERVAL_SECONDS`
- `ML_AUTO_POLL_LIMIT`
- `ERP_AUTO_SYNC_ENABLED`
- `ERP_AUTO_SYNC_INTERVAL_SECONDS`
- `ERP_AUTO_SYNC_LIMIT`

Regla:

- PowerShell debe quedar minimizado. Lo operativo debe vivir en HTML/ERP.

## 11. Preferencias del usuario / configuración futura

Pendiente estructural:

Crear preferencias por usuario para recordar límites y configuraciones.

Ejemplos:

- ventas: 20.
- jobs: 100.
- publicaciones: 20.

Idea de tabla:

- `erp_user_preferences`
- `user_key`
- `section`
- `preference_key`
- `preference_value`
- `updated_at`

También podrían vivir ahí:

- logo fiscal.
- datos fiscales.
- punto de venta.
- CUIT.
- ingresos brutos.
- inicio actividad.
- textos legales.

Por ahora algunas preferencias visuales pueden guardarse en navegador/localStorage.

## 12. Variables de entorno importantes

Backend / seguridad:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `ADMIN_TOKEN`

Tienda Nube:

- `TN_TOKEN` o `TN_ACCESS_TOKEN`
- `TN_USER_ID`
- `TN_STORE_ID`
- `TN_PRODUCTS_STORE_ID`

Mercado Libre:

- `ML_ACCESS_TOKEN`
- `ML_REFRESH_TOKEN`
- `ML_CLIENT_ID`
- `ML_CLIENT_SECRET`
- `ML_USER_ID`

WhatsApp:

- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `GRAPH_API_VERSION`
- `HUMAN_NOTIFY_PHONE`

Autosync/poll:

- `TN_AUTO_POLL_ENABLED`
- `ML_AUTO_POLL_ENABLED`
- `ERP_AUTO_SYNC_ENABLED`

## 13. Cosas que NO hay que hacer

- No asumir que Gonzalo pegó mal un token o ID sin evidencia.
- No tocar variables/IDs que ya funcionan para arreglar otro endpoint.
- No mezclar `TN_USER_ID`, `TN_STORE_ID` y `TN_PRODUCTS_STORE_ID` sin verificar contexto.
- No tratar orders ML como ventas finales sin agrupar.
- No descontar stock master en ventas Full.
- No tocar `app_v2.py` si el problema es del ERP.
- No entregar fragmentos si se pidió archivo completo.
- No meter lógica fiscal automática sin pruebas con borradores/test.
- No copiar diseño de EcommApp; solo replicar lógica fiscal/operativa necesaria.

## 14. Estilo de trabajo con Gonzalo

Preferencias del usuario:

- Respuestas cortas.
- En debugging, un solo paso inmediato.
- Código completo para reemplazar, no parches sueltos.
- No asumir errores básicos de copy/paste.
- Ser preciso con IDs, tokens y rutas.
- Antes de tocar producción, validar versión visible.
- Usar nombres de archivo con versión única para evitar caché/confusión.

Frase clave:

- Si Gonzalo dice `Zamba!`, responder festejando con complicidad: significa que se confirmó una hipótesis técnica contraintuitiva.

## 15. Estado actual resumido al 2026-05-28

Funciona o quedó avanzado:

- Inventario ERP con publicaciones TN/ML.
- Combos/kits y recalculo por componentes.
- Captura de ventas TN.
- Captura de ventas ML.
- Reglas ML Full vs stock propio.
- Sync jobs y autosync.
- Vista de ventas ML/TN.
- Monitor de jobs.
- Facturación ARCA en etapa test/layout.
- Reporte ARCA con facturas emitidas y test.
- Previews/imprimibles A4 en ajuste.

Pendiente principal:

- Conectar emisión ARCA real final para ventas pendientes.
- Resolver datos fiscales faltantes de ML.
- Limpiar pruebas `test` sin CAE.
- Emitir realmente las ventas ML faltantes post-EcommApp.
- Mejorar dashboard de inicio.
- Preferencias persistentes por usuario.
- Facturación automática posterior, solo cuando el circuito manual esté validado.

## 16. Documentación relacionada

Este documento es el contexto maestro.

El documento `docs/api-usage-marketplaces.md` queda como anexo técnico específico de APIs/marketplaces, especialmente Mercado Libre, Tienda Nube, WhatsApp, Supabase y sync jobs.

Si hay duplicación, este documento manda para contexto general; el anexo manda para detalles técnicos de APIs.
