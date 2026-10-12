WITH base AS (
    SELECT o.id, o.customer_id, o.created_at
    FROM orders o
    WHERE o.status = 'completed'
      AND o.created_at >= '2024-01-01'::timestamp
),
captured_orders AS (
    SELECT DISTINCT o.customer_id, o.id
    FROM base o
    WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
),
captured_count AS (
    SELECT customer_id, count(*) AS cnt FROM captured_orders GROUP BY customer_id
),
revenue AS (
    SELECT o.customer_id, sum((oi.quantity - COALESCE(r.quantity,0)) * oi.unit_price) AS rev
    FROM base o
    JOIN order_items oi ON oi.order_id = o.id
    LEFT JOIN returns r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
    WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=o.id AND pay.status='captured')
    GROUP BY o.customer_id
),
discount AS (
    SELECT o.customer_id, sum(op.discount_amount) AS disc
    FROM base o
    JOIN order_promotions op ON op.order_id = o.id
    JOIN promotions pr ON pr.id = op.promotion_id
    WHERE pr.active
    GROUP BY o.customer_id
),
late AS (
    SELECT o.customer_id, count(*) AS late_cnt
    FROM base o
    JOIN shipments sh ON sh.order_id = o.id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
    GROUP BY o.customer_id
),
risky AS (
    SELECT o.customer_id, count(DISTINCT ps.supplier_id) AS risky_cnt
    FROM base o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN product_suppliers ps ON ps.product_id = oi.product_id
    JOIN suppliers s ON s.id = ps.supplier_id
    WHERE s.risk_tier >= 4
    GROUP BY o.customer_id
),
lastc AS (
    SELECT customer_id, max(created_at) AS last_at FROM base GROUP BY customer_id
),
qualifying AS (
    SELECT DISTINCT o.customer_id
    FROM base o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN products p ON p.id = oi.product_id
    JOIN product_suppliers ps ON ps.product_id = p.id
    JOIN suppliers s ON s.id = ps.supplier_id
    JOIN shipments sh ON sh.order_id = o.id
    WHERE p.active AND s.risk_tier >= 4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
)
SELECT
    c.id AS customer_id, c.region, c.segment,
    COALESCE(cc.cnt, 0) AS captured_order_count,
    COALESCE(rev.rev, 0)::numeric(20,2) AS net_item_revenue,
    COALESCE(d.disc, 0)::numeric(18,2) AS active_promotion_discount,
    COALESCE(l.late_cnt, 0) AS late_shipment_count,
    COALESCE(r.risky_cnt, 0) AS risky_supplier_count,
    lc.last_at AS last_completed_at
FROM customers c
LEFT JOIN captured_count cc ON cc.customer_id = c.id
LEFT JOIN revenue rev ON rev.customer_id = c.id
LEFT JOIN discount d ON d.customer_id = c.id
LEFT JOIN late l ON l.customer_id = c.id
LEFT JOIN risky r ON r.customer_id = c.id
LEFT JOIN lastc lc ON lc.customer_id = c.id
WHERE c.active
  AND EXISTS (SELECT 1 FROM qualifying q WHERE q.customer_id = c.id)