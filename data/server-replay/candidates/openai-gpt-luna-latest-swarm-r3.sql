WITH completed_orders AS MATERIALIZED (
    SELECT o.*
    FROM orders o
    WHERE lower(o.status) = 'completed'
      AND o.created_at::date >= DATE '2024-01-01'
),
captured_orders AS MATERIALIZED (
    SELECT co.*
    FROM completed_orders co
    WHERE EXISTS (
        SELECT 1 FROM payments pay
        WHERE pay.order_id = co.id AND pay.status = 'captured'
    )
),
captured AS (
    SELECT customer_id, count(*) AS captured_order_count
    FROM captured_orders GROUP BY customer_id
),
revenue AS (
    SELECT co.customer_id,
           sum((oi.quantity - COALESCE(r.quantity, 0)) * oi.unit_price) AS net_item_revenue
    FROM captured_orders co
    JOIN order_items oi ON oi.order_id = co.id
    LEFT JOIN returns r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
    GROUP BY co.customer_id
),
promo AS (
    SELECT co.customer_id, sum(op.discount_amount) AS active_promotion_discount
    FROM completed_orders co
    JOIN order_promotions op ON op.order_id = co.id
    JOIN promotions pr ON pr.id = op.promotion_id
    WHERE pr.active GROUP BY co.customer_id
),
late AS (
    SELECT co.customer_id, count(*) AS late_shipment_count
    FROM completed_orders co JOIN shipments sh ON sh.order_id = co.id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
    GROUP BY co.customer_id
),
risky AS (
    SELECT co.customer_id, count(DISTINCT ps.supplier_id) AS risky_supplier_count
    FROM completed_orders co
    JOIN order_items oi ON oi.order_id = co.id
    JOIN product_suppliers ps ON ps.product_id = oi.product_id
    JOIN suppliers s ON s.id = ps.supplier_id
    WHERE s.risk_tier >= 4 GROUP BY co.customer_id
),
last_completed AS (
    SELECT customer_id, max(created_at) AS last_completed_at
    FROM completed_orders GROUP BY customer_id
),
eligible AS (
    SELECT DISTINCT co.customer_id
    FROM completed_orders co
    JOIN order_items oi ON oi.order_id = co.id
    JOIN products p ON p.id = oi.product_id
    JOIN product_suppliers ps ON ps.product_id = p.id
    JOIN suppliers s ON s.id = ps.supplier_id
    JOIN shipments sh ON sh.order_id = co.id
    WHERE p.active AND s.risk_tier >= 4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
)
SELECT c.id AS customer_id, c.region, c.segment,
       cap.captured_order_count,
       COALESCE(rv.net_item_revenue, 0)::numeric(20,2) AS net_item_revenue,
       COALESCE(pm.active_promotion_discount, 0)::numeric(18,2) AS active_promotion_discount,
       lt.late_shipment_count,
       rk.risky_supplier_count,
       lc.last_completed_at
FROM customers c
JOIN eligible e ON e.customer_id = c.id
LEFT JOIN captured cap ON cap.customer_id = c.id
LEFT JOIN revenue rv ON rv.customer_id = c.id
LEFT JOIN promo pm ON pm.customer_id = c.id
LEFT JOIN late lt ON lt.customer_id = c.id
LEFT JOIN risky rk ON rk.customer_id = c.id
LEFT JOIN last_completed lc ON lc.customer_id = c.id
WHERE c.active