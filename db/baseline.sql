SELECT
    c.id AS customer_id,
    c.region,
    c.segment,
    (
        SELECT count(*)
        FROM orders AS o
        WHERE o.customer_id = c.id
          AND o.status = 'completed'
          AND o.created_at >= TIMESTAMP '2024-01-01 00:00:00'
    ) AS completed_order_count,
    COALESCE((
        SELECT sum(oi.quantity * oi.unit_price)
        FROM orders AS o
        JOIN order_items AS oi ON oi.order_id = o.id
        WHERE o.customer_id = c.id
          AND o.status = 'completed'
          AND o.created_at >= TIMESTAMP '2024-01-01 00:00:00'
    ), 0)::numeric(18, 2) AS completed_revenue,
    (
        SELECT max(o.created_at)
        FROM orders AS o
        WHERE o.customer_id = c.id
          AND o.status = 'completed'
          AND o.created_at >= TIMESTAMP '2024-01-01 00:00:00'
    ) AS last_completed_at
FROM customers AS c
WHERE c.active
  AND EXISTS (
      SELECT 1
      FROM orders AS o
      JOIN order_items AS oi ON oi.order_id = o.id
      JOIN products AS p ON p.id = oi.product_id
      WHERE o.customer_id = c.id
        AND o.status = 'completed'
        AND o.created_at >= TIMESTAMP '2024-01-01 00:00:00'
        AND oi.quantity >= 2
        AND p.active
  );

