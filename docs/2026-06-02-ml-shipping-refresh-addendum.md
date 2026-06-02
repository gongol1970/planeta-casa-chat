# ERP Planeta Casa — Addendum ML Shipping Refresh

Fecha: 2026-06-02  
Relacionado con: `docs/2026-06-02-ml-shipping-canonico.md`

## Contexto

Durante las pruebas del listado de ventas ML se detectó que el modelo canónico era correcto, pero el refresco/enriquecimiento necesitaba reforzarse.

El problema no era siempre la ausencia real de datos en Mercado Libre, sino que algunas ventas se capturaban primero con datos incompletos y luego el refresh terminaba completando `shipping`, `logistic_type` y estados canónicos.

---

## Hallazgo 1 — FULL puede aparecer tarde

En algunos casos la venta entró con:

```text
ml_shipping_id presente
ml_shipping_status null
ml_shipping_logistic_type null
ml_shipping_type null
ml_shipping_subtype null
ml_shipping_ui_status null
```

pero dentro del raw ya había indicios de stock externo/FULL:

```text
ml_stock_mode.is_full_or_external_stock = true
ml_is_full_or_external_stock = true
order_items[].stock.node_id = ARBA01
```

Luego de refrescar/enriquecer, la misma venta terminó trayendo:

```text
ml_shipping_logistic_type = fulfillment
ml_shipping_subtype = full
```

Conclusión:

```text
No confiar únicamente en logistic_type inicial.
El refresh debe reforzar ventas con shipping_id pero campos canónicos incompletos.
```

Ejemplos observados:

```text
#2000013304745207
#2000013303606411
#2000013276957087
```

---

## Hallazgo 2 — Bug de labels por None

Se detectó en la respuesta del backend:

```text
ml_shipping_label_error = "'NoneType' object has no attribute 'lower'"
```

Eso hacía que el backend devolviera:

```text
ml_shipping_ui_status = ""
shipping_ui_status = ""
```

Entonces el HTML terminaba mostrando cosas incorrectas como:

```text
Sin envío
A coordinar con comprador
```

aunque `raw_data` sí tuviera datos de shipping.

Fix aplicado parcialmente:

```text
0.1.100-ml-shipping-ui-safe-labels
```

Regla de implementación:

```text
Ninguna función de labels/UI debe hacer .lower() sobre valores que pueden venir null.
Todo campo debe pasar antes por normalización segura a string.
```

---

## Hallazgo 3 — ready_to_print no es En camino

Caso confirmado:

```text
shipping_status = ready_to_ship
shipping_substatus = ready_to_print
logistic_type = self_service
```

Interpretación correcta:

```text
FLEX
Pendiente de envío
```

No debe mostrarse como:

```text
En camino
```

Regla UI:

```text
ready_to_ship + ready_to_print = Pendiente de envío
ready_to_ship solo = Pendiente de envío
```

`En camino` debe reservarse para estados/subestados reales de tránsito o despacho ya tomado.

---

## Regla reforzada de refresh

Al entrar a Ventas ML o al presionar Actualizar, el backend debe:

```text
1. Tomar ventas ML no finales.
2. Si hay shipping_id, consultar/enriquecer shipment.
3. Si no hay shipping_id pero es TO_AGREE, consultar/enriquecer order.
4. Recalcular siempre campos canónicos.
5. Si status llega a delivered, marcar ml_shipping_is_final = true.
6. No volver a refrescar delivered en circuito normal.
```

Además, debe refrescar especialmente cuando encuentre:

```text
ml_shipping_id presente pero campos canónicos incompletos
ml_shipping_status null
ml_shipping_logistic_type null
ml_shipping_type null
ml_shipping_subtype null
ml_shipping_ui_status null
ml_shipping_is_final null
```

---

## Regla FULL reforzada

Clasificar como FULL si se confirma cualquiera de estos indicios:

```text
ml_shipping_logistic_type = fulfillment
ml_shipping_last_snapshot.logistic_type = fulfillment
ml_stock_mode.is_full_or_external_stock = true
ml_is_full_or_external_stock = true
order_items[].stock.node_id distinto del nodo propio
order_items[].stock.node_id empieza con ARBA
```

Si luego el refresh trae `fulfillment`, ese dato confirma y normaliza la clasificación.

---

## Conclusión

El comportamiento observado indica que Mercado Libre puede entregar datos incompletos en un primer momento y completarlos luego. Por eso el ERP no debe tomar una clasificación visual inicial como definitiva si todavía faltan campos canónicos.

Regla práctica:

```text
Primero reforzar refresh/enriquecimiento.
Después evaluar la UI.
No cambiar reglas de negocio solo por una vista parcial recién capturada.
```
