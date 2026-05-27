# Uso de APIs - Planeta Casa ERP

Última actualización: 2026-05-27

Este documento resume qué se usa de cada API y para qué sirve dentro del ERP / bot de Planeta Casa.

## Idea operativa central

El ERP tiene que trabajar con una diferencia importante:

- **Pedido / venta comercial visible**: lo que el usuario de ecommerce reconoce como una venta.
- **Orden API**: unidad técnica que devuelve cada marketplace. No siempre equivale a una venta completa.
- **Envío / shipment**: unidad logística. En Mercado Libre puede agrupar varias órdenes.
- **Línea / ítem vendido**: producto o variante que impacta stock.

Aprendizaje clave: en Mercado Libre, **orders no son necesariamente ventas completas**. Una venta visible puede llegar como varias órdenes API, especialmente si hay varios ítems, pack o envío agrupado.

---

## Mercado Libre API

### Para qué se usa

1. Capturar ventas pagadas.
2. Agrupar correctamente órdenes API en una venta comercial.
3. Identificar logística: Mercado Envíos, Flex o Full.
4. Descontar stock propio cuando corresponde.
5. Sincronizar stock hacia Mercado Libre.
6. Evitar descontar stock propio en ventas Full.

### Conceptos aprendidos

#### Orders

Las órdenes API de ML son unidades técnicas. Sirven para obtener:

- `order_id`
- comprador
- ítems vendidos
- cantidades
- precios
- estado de pago
- `pack_id` cuando existe
- `shipping.id` cuando existe

Pero **no deben usarse solas como número de venta comercial**.

Ejemplo real observado:

- Envío ML: `47103595974`
- Órdenes API: `2000016514240894`, `2000016514240892`, `2000016514239472`, etc.
- Venta visible real: `#2000013067289007`

Conclusión: si se lista por orden API, una sola venta queda “splitteada” en subventas.

#### Pack / venta

Cuando existe `pack_id`, se usa como agrupador comercial preferente.

Prioridad de agrupación para mostrar ventas ML:

1. `pack_id` / número de venta real si está disponible.
2. `shipping_id` si no hay pack.
3. `order_id` solo como último fallback.

#### Shipment

El shipment sirve para logística y para agrupar ventas cuando ML no entrega pack claro.

Se usa para mostrar:

- Envío ML `shipping_id`
- tipo de logística
- órdenes API asociadas

Tipos operativos:

- **Mercado Envíos**: sale desde Planeta Casa hacia un punto de despacho; la logística posterior la maneja Mercado Libre.
- **Flex**: sale desde Planeta Casa, pero la logística la hace Planeta Casa o un operador propio dentro de AMBA.
- **Full**: el stock ya está en depósito de Mercado Libre; no se debe descontar stock master al capturar la venta.
- **A coordinar con comprador**: fallback cuando ML no da datos de envío suficientes o es una modalidad fuera de ME.

### Endpoints / recursos usados

#### Lectura de órdenes

Uso esperado:

- Buscar órdenes recientes del vendedor.
- Filtrar pagadas.
- Leer detalle de cada orden.
- Extraer líneas vendidas.

Datos importantes a conservar en `raw_data`:

- `order_id`
- `pack_id`
- `shipping.id`
- `date_created`
- `date_closed`
- `buyer`
- `order_items`
- `payment_status` o equivalente

#### Lectura de shipment

Uso esperado:

- Resolver logística real.
- Distinguir Mercado Envíos, Flex y Full.
- Obtener agrupación por envío.

Datos importantes:

- `shipping_id`
- `logistic_type`
- `shipping_mode`
- `status`
- `substatus`
- cualquier dato que permita inferir Full/Flex/Mercado Envíos.

#### Items / publicaciones

Uso esperado:

- Leer publicaciones.
- Vincular publicación + variante + SKU contra `marketplace_listings`.
- Mantener precio y stock de publicación.

#### User products / stock

Aprendizaje importante para stock ML:

- `item.available_quantity` puede no ser editable o puede representar una vista agregada.
- Para stock propio hay que trabajar con `user-products/{user_product_id}/stock`.
- Para escribir stock se necesita leer primero el header `x-version`.
- La actualización correcta del stock propio usa el tipo `selling_address`.

Flujo:

1. Resolver `user_product_id`.
2. `GET /user-products/{user_product_id}/stock`.
3. Leer header `x-version`.
4. `PUT /user-products/{user_product_id}/stock/type/selling_address` con `X-Version`.
5. Payload: `quantity` con el stock objetivo.

No tocar `meli_facility` para ventas Full.

### Reglas de stock ML

- Venta normal / Mercado Envíos: descuenta stock master.
- Venta Flex: descuenta stock master.
- Venta Full: se registra para control, pero **no descuenta stock master**, porque el descuento operativo ya ocurrió cuando se envió mercadería al depósito de ML.

---

## Tienda Nube API

### Para qué se usa

1. Capturar órdenes pagadas de Tienda Nube.
2. Descontar stock master en ERP.
3. Sincronizar stock/precio de productos y variantes.
4. Mantener vínculo SKU ↔ publicación/variante.

### Aprendizaje de IDs

Tienda Nube no se comporta igual en todos los módulos. No asumir que un único ID sirve para todo.

Variables usadas:

- `TN_USER_ID`: contexto que resultó necesario para órdenes.
- `TN_STORE_ID`: ID histórico de tienda usado en parte del circuito.
- `TN_ORDERS_STORE_ID = TN_USER_ID or TN_STORE_ID`: lectura/captura de órdenes.
- `TN_PRODUCTS_STORE_ID`: push de stock/precio a productos y variantes.

Aprendizaje real: antes de culpar token/copy-paste, verificar qué ID espera cada endpoint. Tienda Nube puede usar contextos distintos según ruta.

### Órdenes TN

Uso esperado:

- Listar órdenes recientes.
- Filtrar pagadas.
- Evitar duplicados con `external_order_id`.
- Extraer líneas vendidas por SKU.
- Registrar orden en ERP.
- Crear movimientos de stock.
- Crear `sync_jobs` para publicar el nuevo stock.

Datos importantes:

- ID interno/API de orden.
- Número visible de orden.
- estado de pago.
- estado de envío.
- fecha real de origen (`created_at`, `updated_at`, `paid_at` según corresponda).
- cliente.
- líneas / productos / variantes.

### Productos / variantes TN

Uso esperado:

- Leer productos y variantes para poblar `marketplace_listings`.
- Mantener:
  - `external_product_id`
  - `external_variant_id`
  - SKU
  - precio
  - stock
  - URL
  - estado activo/inactivo
- Actualizar stock en TN desde `sync_jobs`.

---

## WhatsApp Cloud API

### Para qué se usa

1. Recibir mensajes de clientes.
2. Responder desde el bot.
3. Notificar internamente ventas/eventos importantes a Gonzalo.
4. Permitir derivación humana.

### Recursos usados

- Webhook GET para verificación con `hub.mode`, `hub.verify_token`, `hub.challenge`.
- Webhook POST para mensajes entrantes.
- Endpoint de envío de mensajes usando `WHATSAPP_PHONE_NUMBER_ID`.
- Token permanente o de sistema configurado en Render.

### Reglas operativas

- No mezclar avisos internos con conversaciones de clientes.
- Las conversaciones del cliente van a tablas de CRM/conversación.
- Los avisos administrativos pueden enviarse directo al teléfono humano configurado.

---

## Supabase

### Para qué se usa

Supabase es la base operativa del ERP.

Tablas principales:

- `inventory_items`: stock master y catálogo operativo.
- `marketplace_listings`: vínculo marketplace/publicación/variante/SKU.
- `orders`: ventas capturadas por canal.
- `order_items`: líneas de venta.
- `stock_movements`: bitácora de stock.
- `sync_jobs`: cola de sincronización a marketplaces.
- `bundle_components`: composición de combos/kits.

### Regla central

El stock master vive en `inventory_items`. Los marketplaces no son la verdad: se sincronizan desde ERP.

---

## Sync jobs

### Para qué sirven

Cada venta o ajuste de stock genera trabajos para sincronizar el stock publicado en marketplaces.

Campos clave:

- marketplace
- listing_id
- variant_id
- sku
- target_stock
- status
- attempts
- last_error
- metadata de origen

### Estados típicos

- `pending`
- `failed_retry`
- `done`
- `manual_review`

### Regla aprendida

Si ya existe un job abierto para marketplace/listing/variant, actualizarlo al último stock objetivo en vez de crear duplicados o fallar por unique.

---

## Vista de ventas ERP

### Objetivo

La vista no debe mostrar detalles técnicos como si fueran ventas independientes.

Debe mostrar:

- número de venta visible cuando exista.
- envío ML cuando aplique.
- órdenes API asociadas como dato secundario.
- cliente.
- método de envío.
- estado de pago normalizado: `PAGADO`.
- total.
- cantidad de órdenes agrupadas.
- líneas de productos siempre desplegadas.

### Búsqueda

El filtro debe buscar por:

- número de venta.
- orden API.
- envío ML.
- cliente.
- SKU.
- producto.
- método de envío.

### Versionado visual

Mostrar chiquito abajo:

- `Frontend: x.y`
- `Backend: x.y.z`

Evitar mostrar versionado grande dentro de las tarjetas.

---

## Pendientes / mejoras futuras

- Seguir mejorando detección de número visible de venta ML cuando no venga claro en order/shipment.
- Auditar si existe un recurso de ML más genérico que represente “venta” por encima de orders, pack y shipment.
- Completar módulo de preguntas ML.
- Completar módulo de mensajes ML si la API permite hacerlo útil.
- Integrar facturación ARCA producción luego de validar circuito completo.
- Mantener EcommApp solo como respaldo hasta terminar transición.
