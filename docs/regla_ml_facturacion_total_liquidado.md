# Regla ML - Total liquidado no es total facturable

Fecha: 2026-05-29

## Regla crítica

En Mercado Libre, **NO inferir envío facturable por diferencia entre total liquidado y suma de productos**.

El total liquidado / detalle de cobro de ML puede incluir:

- precio de productos,
- cargo por venta,
- bonificaciones,
- bonificación por envío,
- impuestos / retenciones,
- ajustes propios de Mercado Pago / Mercado Libre.

Por eso, el total liquidado puede ser mayor o menor que la suma de productos y **no representa necesariamente lo que debe facturarse al comprador**.

## Regla para ARCA

Para facturar al cliente desde una venta ML:

1. Usar las líneas reales de productos.
2. Agregar envío como ítem facturable **solo si existe explícitamente un envío pagado por el comprador**.
3. No crear `envio_me_buyer` por diferencia entre:

```text
Total liquidado ML - suma de productos
```

4. No usar cargos, comisiones, retenciones, impuestos ni bonificaciones de ML como base de factura al cliente.

## Ejemplo real observado

Venta ML visible: `#2000013210719597`

Detalle de cobro ML:

```text
Precio de productos:       $17.529,00
Cargo por venta total:     -$6.306,70
Bonificación por envío:     $8.490,00
Impuestos:                   -$37,69
Total liquidado:           $19.674,61
```

Interpretación correcta:

```text
Total facturable al cliente: productos reales + envío pagado explícitamente por comprador.
No facturar $19.674,61 solo porque sea el total liquidado.
No facturar la diferencia $2.145,61 como envío.
```

En este ejemplo, si no hay envío pagado explícito por el comprador, la factura al cliente debe salir por:

```text
Productos: $17.529,00
Envío facturable: $0,00
Total factura: $17.529,00
```

## Implementación pendiente

Revisar lógica de generación de factura ML en `erp_admin.py` para asegurar que:

- no use `orders.total` si ese total representa liquidación ML / Mercado Pago;
- no infiera envío desde diferencia de totales;
- busque primero una señal explícita de envío pagado por comprador dentro del raw de order/shipment/payment;
- guarde en `arca_invoices.lines` solo productos reales y envío explícito si corresponde.

## Relación con otros documentos

Este documento debe considerarse anexo de:

- `docs/erp_contexto_maestro.md`
- `docs/pendientes_2026_05_29_facturacion_arca.md`
