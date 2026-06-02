# ERP Planeta Casa — Mercado Libre Shipping canónico

Fecha: 2026-06-02  
Estado: documentación operativa / base para manual definitivo

## Objetivo

Definir una regla sustentable para interpretar, guardar y refrescar la información logística de ventas de Mercado Libre dentro del ERP Planeta Casa.

La idea central es separar:

- `raw_data`: lo que viene bruto desde Mercado Libre, sin modificar, como respaldo/auditoría.
- Campos canónicos del ERP: interpretación operativa estable, usada por la pantalla, stock, facturación y refrescos.

Mercado Libre entrega campos técnicos como `shipping.id`, `pack_id`, `status`, `substatus`, `logistic_type`, `order_id`, `stock.node_id`, tags, etc. El ERP debe traducir esos datos a reglas de negocio propias.

---

## 1. Tipos principales de envío ML

### 1.1 `TO_AGREE`

Corresponde a:

```text
Entrega a acordar con el comprador
```

Regla:

```text
ml_shipping_id = null
logistic_type = null
```

Ejemplo confirmado:

```text
ML order 2000016690414318
```

Este caso no es un error ni un dato faltante. No debe mostrarse como “sin envío”. Debe mostrarse como:

```text
Entrega a acordar
```

No se consulta `/shipments/{id}` porque no existe shipment.

Operativamente:

- Registra venta.
- Registra cliente.
- Puede facturarse.
- Descuenta stock master propio.
- No entra en refresco normal de shipping ML porque no tiene `shipping_id`.

---

### 1.2 `ENVIO_ML`

Corresponde a ventas con circuito logístico de Mercado Libre.

Regla:

```text
ml_shipping_id existe
```

Dentro de `ENVIO_ML` existen tres subtipos operativos: `FULL`, `FLEX` y `NORMAL`.

---

## 2. Subtipos de `ENVIO_ML`

### 2.1 `FULL`

Regla:

```text
logistic_type = fulfillment
```

Significado operativo:

```text
Sale del depósito de Mercado Libre.
No descuenta stock master propio.
Se registra venta, cliente y facturación.
No requiere acción logística de Planeta Casa.
```

Importante: las ventas Full no deben descontar stock propio porque ese stock ya fue descontado manualmente cuando se envió mercadería al warehouse de Mercado Libre.

---

### 2.2 `FLEX`

Regla:

```text
logistic_type = self_service
```

Significado operativo:

```text
La logística corre por Planeta Casa.
Mercado Libre la mide/controla.
Sí descuenta stock master propio.
```

---

### 2.3 `NORMAL`

Caso típico:

```text
logistic_type = xd_drop_off
```

También entran acá otros tipos equivalentes de correo/punto de despacho de Mercado Libre.

Significado operativo:

```text
Mercado Envíos normal.
Planeta Casa prepara el paquete y lo deja en un punto de despacho ML.
Sí descuenta stock master propio.
```

---

## 3. Reglas de stock

```text
FULL      -> no descuenta stock master propio
FLEX      -> descuenta stock master
NORMAL    -> descuenta stock master
TO_AGREE  -> descuenta stock master
```

---

## 4. Refresco de estados de envío

Al entrar al menú de ventas ML o al presionar el botón Actualizar, el ERP debe refrescar solo las ventas que todavía no tengan estado final.

Regla operativa:

```text
1. Buscar ventas ML cuyo shipping no sea final.
2. Excluir TO_AGREE porque no tiene shipment.
3. Excluir ventas ya delivered/finales.
4. Para ENVIO_ML con shipping_id, consultar Mercado Libre.
5. Guardar el raw nuevo devuelto por ML.
6. Actualizar los campos canónicos del ERP.
7. Si el estado llega a delivered, marcar la venta como final.
```

Condición para consultar ML:

```text
channel = ML
ml_shipping_type = ENVIO_ML
ml_shipping_id IS NOT NULL
ml_shipping_is_final != true
```

Condición para no consultar en refrescos normales:

```text
TO_AGREE
FULL/FLEX/NORMAL ya delivered
ventas sin shipping_id
ventas marcadas como shipping final
```

Regla final:

```text
Si ml_shipping_status = delivered
-> ml_shipping_is_final = true
-> no se consulta más en refrescos normales
```

Futuro circuito separado:

```text
Reclamos / devoluciones / incidencias
```

---

## 5. Campos canónicos sugeridos

Aunque por ahora algunos datos puedan vivir dentro de `orders.raw_data`, el modelo sustentable debería tender a campos canónicos propios del ERP:

```text
ml_shipping_type
  to_agree | envio_ml

ml_shipping_subtype
  full | flex | normal | null

ml_shipping_id

ml_shipping_status

ml_shipping_substatus

ml_shipping_logistic_type

ml_shipping_ui_status

ml_shipping_is_final

ml_shipping_refreshed_at

ml_stock_affects_master
```

---

## 6. Número visible / agrupación de ventas

Mercado Libre puede devolver varias órdenes API (`external_order_id`) dentro de un mismo pack/envío.

Para vista operativa, la agrupación debería usar:

```text
coalesce(ml_pack_id, external_order_id)
```

Regla:

- Si existe `pack_id`, el número operativo visible de la venta debería ser el `pack_id`.
- Si no existe `pack_id`, usar el `order_id` / `external_order_id`.

Esto evita mostrar varias tarjetas separadas cuando Mercado Libre representa una misma compra visual como varias órdenes técnicas.

---

## 7. Raw de ML vs campos del ERP

El ERP debe conservar siempre el raw de Mercado Libre para auditoría:

```text
orders.raw_data
```

Pero la pantalla y la lógica operativa no deben depender directamente del raw, sino de la interpretación canónica.

Ejemplo:

```text
ML bruto:
ml_shipping_logistic_type = xd_drop_off

ERP canónico:
ml_shipping_type = envio_ml
ml_shipping_subtype = normal
ml_stock_affects_master = true
ml_shipping_label/ui = Mercado Envíos
```

Otro ejemplo:

```text
ML bruto:
ml_shipping_id = null
logistic_type = null

ERP canónico:
ml_shipping_type = to_agree
ml_shipping_subtype = null
ml_shipping_label/ui = Entrega a acordar
ml_stock_affects_master = true
```

---

## 8. Caso confirmado especial

```text
ML order 2000016690414318
```

Clasificación correcta:

```text
TO_AGREE / Entrega a acordar con el comprador
```

No debe tratarse como error, dato faltante ni envío roto.
