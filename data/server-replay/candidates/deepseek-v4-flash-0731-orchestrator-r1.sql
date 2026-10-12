SELECT c.id AS customer_id, c.region, c.segment,
       COALESCE(m1.cnt,0) AS captured_order_count,
       COALESCE(m2.rev,0)::numeric(20,2) AS net_item_revenue,
       COALESCE(m3.disc,0)::numeric(18,2) AS active_promotion_discount,
       COALESCE(m4.late,0) AS late_shipment_count,
       COALESCE(m5.risky,0) AS risky_supplier_count,
       m6.last_ts AS last_completed_at
FROM customers c
LEFT JOIN (
    SELECT o.customer_id, count(*) AS cnt
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
    GROUP BY o.customer_id
) m1 ON m1.customer_id=c.id
LEFT JOIN (
    SELECT o.customer_id, sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price) AS rev
    FROM orders o
    JOIN order_items oi ON oi.order_id=o.id
    LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
    GROUP BY o.customer_id
) m2 ON m2.customer_id=c.id
LEFT JOIN (
    SELECT o.customer_id, sum(op.discount_amount) AS disc
    FROM orders o
    JOIN order_promotions op ON op.order_id=o.id
    JOIN promotions pr ON pr.id=op.promotion_id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01' AND pr.active
    GROUP BY o.customer_id
) m3 ON m3.customer_id=c.id
LEFT JOIN (
    SELECT o.customer_id, count(*) AS late
    FROM orders o
    JOIN shipments sh ON sh.order_id=o.id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
    GROUP BY o.customer_id
) m4 ON m4.customer_id=c.id
LEFT JOIN (
    SELECT o.customer_id, count(DISTINCT ps.supplier_id) AS risky
    FROM orders o
    JOIN order_items oi ON oi.order_id=o.id
    JOIN product_suppliers ps ON ps.product_id=oi.product_id
    JOIN suppliers s ON s.id=ps.supplier_id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01' AND s.risk_tier>=4
    GROUP BY o.customer_id
) m5 ON m5.customer_id=c.id
LEFT JOIN (
    SELECT o.customer_id, max(o.created_at) AS last_ts
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
    GROUP BY o.customer_id
) m6 ON m6.customer_id=c.id
WHERE c.active
  AND EXISTS (
      SELECT 1 FROM orders o
      JOIN order_items oi ON oi.order_id=o.id
      JOIN products p ON p.id=oi.product_id
      JOIN product_suppliers ps ON ps.product_id=p.id
      JOIN suppliers s ON s.id=ps.supplier_id
      JOIN shipments sh ON sh.order_id=o.id
      WHERE o.customer_id=c.id AND lower(o.status)='completed'
        AND o.created_at::date >= DATE '2024-01-01'
        AND p.active AND s.risk_tier>=4
        AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
  )