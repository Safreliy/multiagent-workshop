SELECT
    c.id AS customer_id,
    c.region,
    c.segment,
    COALESCE(cap.cnt, 0) AS captured_order_count,
    COALESCE(rev.rev, 0)::numeric(20,2) AS net_item_revenue,
    COALESCE(promo.disc, 0)::numeric(18,2) AS active_promotion_discount,
    COALESCE(late.cnt, 0) AS late_shipment_count,
    COALESCE(risky.cnt, 0) AS risky_supplier_count,
    last.last_at AS last_completed_at
FROM customers c
JOIN (
    SELECT DISTINCT o.customer_id
    FROM orders o
    JOIN order_items oi ON oi.order_id=o.id
    JOIN products p ON p.id=oi.product_id
    JOIN product_suppliers ps ON ps.product_id=p.id
    JOIN suppliers s ON s.id=ps.supplier_id
    JOIN shipments sh ON sh.order_id=o.id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND p.active AND s.risk_tier>=4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
) q ON q.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, count(*) AS cnt
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
    GROUP BY o.customer_id
) cap ON cap.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, sum((oi.quantity - COALESCE(r.quantity,0))*oi.unit_price) AS rev
    FROM orders o
    JOIN order_items oi ON oi.order_id=o.id
    LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
    GROUP BY o.customer_id
) rev ON rev.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, sum(op.discount_amount) AS disc
    FROM orders o
    JOIN order_promotions op ON op.order_id=o.id
    JOIN promotions pr ON pr.id=op.promotion_id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01' AND pr.active
    GROUP BY o.customer_id
) promo ON promo.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, count(*) AS cnt
    FROM orders o
    JOIN shipments sh ON sh.order_id=o.id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
    GROUP BY o.customer_id
) late ON late.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, count(DISTINCT ps.supplier_id) AS cnt
    FROM orders o
    JOIN order_items oi ON oi.order_id=o.id
    JOIN product_suppliers ps ON ps.product_id=oi.product_id
    JOIN suppliers s ON s.id=ps.supplier_id
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01' AND s.risk_tier>=4
    GROUP BY o.customer_id
) risky ON risky.customer_id = c.id
LEFT JOIN (
    SELECT o.customer_id, max(o.created_at) AS last_at
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
    GROUP BY o.customer_id
) last ON last.customer_id = c.id
WHERE c.active;