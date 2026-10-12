WITH
completed_orders AS MATERIALIZED (
    SELECT o.id, o.customer_id, o.created_at
    FROM orders AS o
    WHERE lower(o.status) = 'completed'
      AND o.created_at::date >= DATE '2024-01-01'
),
captured_orders AS MATERIALIZED (
    SELECT o.id, o.customer_id
    FROM completed_orders AS o
    WHERE EXISTS (
        SELECT 1 FROM payments AS pay
        WHERE pay.order_id = o.id AND pay.status = 'captured'
    )
),
order_summary AS (
    SELECT customer_id, max(created_at) AS last_completed_at
    FROM completed_orders GROUP BY customer_id
),
captured_summary AS (
    SELECT customer_id, count(*) AS captured_order_count
    FROM captured_orders GROUP BY customer_id
),
revenue_summary AS (
    SELECT o.customer_id,
           sum((oi.quantity - COALESCE(r.quantity, 0)) * oi.unit_price) AS net_item_revenue
    FROM captured_orders AS o
    JOIN order_items AS oi ON oi.order_id = o.id
    LEFT JOIN returns AS r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
    GROUP BY o.customer_id
),
promotion_summary AS (
    SELECT o.customer_id, sum(op.discount_amount) AS active_promotion_discount
    FROM completed_orders AS o
    JOIN order_promotions AS op ON op.order_id = o.id
    JOIN promotions AS pr ON pr.id = op.promotion_id
    WHERE pr.active GROUP BY o.customer_id
),
late_summary AS (
    SELECT o.customer_id, count(*) AS late_shipment_count
    FROM completed_orders AS o
    JOIN shipments AS sh ON sh.order_id = o.id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
    GROUP BY o.customer_id
),
risk_summary AS (
    SELECT o.customer_id, count(DISTINCT ps.supplier_id) AS risky_supplier_count
    FROM completed_orders AS o
    JOIN order_items AS oi ON oi.order_id = o.id
    JOIN product_suppliers AS ps ON ps.product_id = oi.product_id
    JOIN suppliers AS s ON s.id = ps.supplier_id
    WHERE s.risk_tier >= 4 GROUP BY o.customer_id
),
eligible AS (
    SELECT DISTINCT o.customer_id
    FROM completed_orders AS o
    JOIN shipments AS sh ON sh.order_id = o.id
    JOIN order_items AS oi ON oi.order_id = o.id
    JOIN products AS p ON p.id = oi.product_id
    JOIN product_suppliers AS ps ON ps.product_id = p.id
    JOIN suppliers AS s ON s.id = ps.supplier_id
    WHERE p.active AND s.risk_tier >= 4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
)
SELECT c.id AS customer_id, c.region, c.segment,
       COALESCE(cs.captured_order_count, 0::bigint) AS captured_order_count,
       COALESCE(rs.net_item_revenue, 0)::numeric(20, 2) AS net_item_revenue,
       COALESCE(ps.active_promotion_discount, 0)::numeric(18, 2) AS active_promotion_discount,
       COALESCE(ls.late_shipment_count, 0::bigint) AS late_shipment_count,
       COALESCE(rks.risky_supplier_count, 0::bigint) AS risky_supplier_count,
       os.last_completed_at
FROM customers AS c
JOIN eligible AS e ON e.customer_id = c.id
JOIN order_summary AS os ON os.customer_id = c.id
LEFT JOIN captured_summary AS cs ON cs.customer_id = c.id
LEFT JOIN revenue_summary AS rs ON rs.customer_id = c.id
LEFT JOIN promotion_summary AS ps ON ps.customer_id = c.id
LEFT JOIN late_summary AS ls ON ls.customer_id = c.id
LEFT JOIN risk_summary AS rks ON rks.customer_id = c.id
WHERE c.active