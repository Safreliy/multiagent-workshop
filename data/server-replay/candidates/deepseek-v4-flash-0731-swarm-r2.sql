WITH co AS (
    SELECT o.id, o.customer_id, o.created_at
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
),
cap AS (
    SELECT DISTINCT o.customer_id, o.id
    FROM co o
    WHERE EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.id AND p.status='captured')
),
agg_cap AS (
    SELECT customer_id, count(*) AS captured_order_count
    FROM cap GROUP BY customer_id
),
agg_rev AS (
    SELECT o.customer_id, sum((oi.quantity - COALESCE(r.quantity,0))*oi.unit_price) AS net
    FROM co o
    JOIN cap c ON c.id=o.id
    JOIN order_items oi ON oi.order_id=o.id
    LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
    GROUP BY o.customer_id
),
agg_promo AS (
    SELECT o.customer_id, sum(op.discount_amount) AS disc
    FROM co o
    JOIN order_promotions op ON op.order_id=o.id
    JOIN promotions pr ON pr.id=op.promotion_id
    WHERE pr.active
    GROUP BY o.customer_id
),
agg_late AS (
    SELECT o.customer_id, count(*) AS late_count
    FROM co o
    JOIN shipments sh ON sh.order_id=o.id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
    GROUP BY o.customer_id
),
agg_risky AS (
    SELECT o.customer_id, count(DISTINCT ps.supplier_id) AS risky
    FROM co o
    JOIN order_items oi ON oi.order_id=o.id
    JOIN product_suppliers ps ON ps.product_id=oi.product_id
    JOIN suppliers s ON s.id=ps.supplier_id
    WHERE s.risk_tier>=4
    GROUP BY o.customer_id
),
agg_last AS (
    SELECT customer_id, max(created_at) AS last_completed_at
    FROM co GROUP BY customer_id
),
has_outer AS (
    SELECT DISTINCT o.customer_id
    FROM co o
    JOIN order_items oi ON oi.order_id=o.id
    JOIN products p ON p.id=oi.product_id
    JOIN product_suppliers ps ON ps.product_id=p.id
    JOIN suppliers s ON s.id=ps.supplier_id
    JOIN shipments sh ON sh.order_id=o.id
    WHERE p.active AND s.risk_tier>=4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
)
SELECT
    c.id AS customer_id,
    c.region,
    c.segment,
    COALESCE(ac.captured_order_count,0) AS captured_order_count,
    COALESCE(ar.net,0)::numeric(20,2) AS net_item_revenue,
    COALESCE(ap.disc,0)::numeric(18,2) AS active_promotion_discount,
    COALESCE(al.late_count,0) AS late_shipment_count,
    COALESCE(ar2.risky,0) AS risky_supplier_count,
    al2.last_completed_at
FROM customers c
LEFT JOIN agg_cap ac ON ac.customer_id=c.id
LEFT JOIN agg_rev ar ON ar.customer_id=c.id
LEFT JOIN agg_promo ap ON ap.customer_id=c.id
LEFT JOIN agg_late al ON al.customer_id=c.id
LEFT JOIN agg_risky ar2 ON ar2.customer_id=c.id
LEFT JOIN agg_last al2 ON al2.customer_id=c.id
WHERE c.active AND EXISTS (SELECT 1 FROM has_outer h WHERE h.customer_id=c.id);