SELECT
    c.id AS customer_id,
    c.region,
    c.segment,
    (
        SELECT count(*)
        FROM orders AS o
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
          AND EXISTS (
              SELECT 1
              FROM payments AS pay
              WHERE pay.order_id = o.id
                AND pay.status = 'captured'
          )
    ) AS captured_order_count,
    COALESCE((
        SELECT sum((oi.quantity - COALESCE(r.quantity, 0)) * oi.unit_price)
        FROM orders AS o
        JOIN order_items AS oi ON oi.order_id = o.id
        LEFT JOIN returns AS r
          ON r.order_id = oi.order_id AND r.line_no = oi.line_no
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
          AND EXISTS (
              SELECT 1 FROM payments AS pay
              WHERE pay.order_id = o.id AND pay.status = 'captured'
          )
    ), 0)::numeric(20, 2) AS net_item_revenue,
    COALESCE((
        SELECT sum(op.discount_amount)
        FROM orders AS o
        JOIN order_promotions AS op ON op.order_id = o.id
        JOIN promotions AS pr ON pr.id = op.promotion_id
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
          AND pr.active
    ), 0)::numeric(18, 2) AS active_promotion_discount,
    (
        SELECT count(*)
        FROM orders AS o
        JOIN shipments AS sh ON sh.order_id = o.id
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
          AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
    ) AS late_shipment_count,
    (
        SELECT count(DISTINCT ps.supplier_id)
        FROM orders AS o
        JOIN order_items AS oi ON oi.order_id = o.id
        JOIN product_suppliers AS ps ON ps.product_id = oi.product_id
        JOIN suppliers AS s ON s.id = ps.supplier_id
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
          AND s.risk_tier >= 4
    ) AS risky_supplier_count,
    (
        SELECT max(o.created_at)
        FROM orders AS o
        WHERE o.customer_id = c.id
          AND lower(o.status) = 'completed'
          AND o.created_at::date >= DATE '2024-01-01'
    ) AS last_completed_at
FROM customers AS c
WHERE c.active
  AND EXISTS (
      SELECT 1
      FROM orders AS o
      JOIN order_items AS oi ON oi.order_id = o.id
      JOIN products AS p ON p.id = oi.product_id
      JOIN product_suppliers AS ps ON ps.product_id = p.id
      JOIN suppliers AS s ON s.id = ps.supplier_id
      JOIN shipments AS sh ON sh.order_id = o.id
      WHERE o.customer_id = c.id
        AND lower(o.status) = 'completed'
        AND o.created_at::date >= DATE '2024-01-01'
        AND p.active
        AND s.risk_tier >= 4
        AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
  );

