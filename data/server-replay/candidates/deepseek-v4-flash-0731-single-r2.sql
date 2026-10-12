WITH base AS (
    SELECT o.id AS order_id, o.customer_id, o.created_at
    FROM orders o
    WHERE lower(o.status) = 'completed'
      AND o.created_at::date >= DATE '2024-01-01'
),
captured AS (
    SELECT b.order_id, b.customer_id
    FROM base b
    WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id = b.order_id AND pay.status = 'captured')
),
cap AS (
    SELECT customer_id, count(*) AS captured_order_count
    FROM captured
    GROUP BY customer_id
),
gross AS (
    SELECT b.customer_id, sum(oi.quantity * oi.unit_price) AS gross_rev
    FROM captured b
    JOIN order_items oi ON oi.order_id = b.order_id
    GROUP BY b.customer_id
),
returned AS (
    SELECT b.customer_id, sum(r.quantity * oi.unit_price) AS returned_rev
    FROM captured b
    JOIN order_items oi ON oi.order_id = b.order_id
    JOIN returns r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
    GROUP BY b.customer_id
),
promo AS (
    SELECT b.customer_id, sum(op.discount_amount) AS active_promotion_discount
    FROM base b
    JOIN order_promotions op ON op.order_id = b.order_id
    JOIN promotions pr ON pr.id = op.promotion_id
    WHERE pr.active
    GROUP BY b.customer_id
),
late_shipments AS (
    SELECT b.order_id, b.customer_id
    FROM base b
    JOIN shipments sh ON sh.order_id = b.order_id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
),
late AS (
    SELECT customer_id, count(*) AS late_shipment_count
    FROM late_shipments
    GROUP BY customer_id
),
risky_pairs AS (
    SELECT DISTINCT b.customer_id, ps.supplier_id
    FROM base b
    JOIN order_items oi ON oi.order_id = b.order_id
    JOIN product_suppliers ps ON ps.product_id = oi.product_id
    JOIN suppliers s ON s.id = ps.supplier_id
    WHERE s.risk_tier >= 4
),
risky AS (
    SELECT customer_id, count(*) AS risky_supplier_count
    FROM risky_pairs
    GROUP BY customer_id
),
lastc AS (
    SELECT b.customer_id, max(b.created_at) AS last_completed_at
    FROM base b
    GROUP BY b.customer_id
),
qual AS (
    SELECT DISTINCT ls.customer_id
    FROM late_shipments ls
    JOIN order_items oi ON oi.order_id = ls.order_id
    JOIN products p ON p.id = oi.product_id
    JOIN product_suppliers ps ON ps.product_id = p.id
    JOIN suppliers s ON s.id = ps.supplier_id
    WHERE p.active AND s.risk_tier >= 4
)
SELECT c.id AS customer_id, c.region, c.segment,
    COALESCE(cap.captured_order_count, 0) AS captured_order_count,
    COALESCE(gross.gross_rev - COALESCE(returned.returned_rev, 0), 0)::numeric(20,2) AS net_item_revenue,
    COALESCE(promo.active_promotion_discount, 0)::numeric(18,2) AS active_promotion_discount,
    COALESCE(late.late_shipment_count, 0) AS late_shipment_count,
    COALESCE(risky.risky_supplier_count, 0) AS risky_supplier_count,
    lastc.last_completed_at AS last_completed_at
FROM customers c
JOIN qual q ON q.customer_id = c.id
LEFT JOIN cap ON cap.customer_id = c.id
LEFT JOIN gross ON gross.customer_id = c.id
LEFT JOIN returned ON returned.customer_id = c.id
LEFT JOIN promo ON promo.customer_id = c.id
LEFT JOIN late ON late.customer_id = c.id
LEFT JOIN risky ON risky.customer_id = c.id
LEFT JOIN lastc ON lastc.customer_id = c.id
WHERE c.active;