WITH fo AS MATERIALIZED (
    SELECT id, customer_id, created_at
    FROM orders
    WHERE lower(status) = 'completed'
      AND created_at::date >= DATE '2024-01-01'
),
cap_orders AS MATERIALIZED (
    SELECT o.id, o.customer_id
    FROM fo AS o
    WHERE EXISTS (SELECT 1 FROM payments AS pay WHERE pay.order_id=o.id AND pay.status='captured')
),
eligible AS (
    SELECT DISTINCT o.customer_id
    FROM fo AS o
    JOIN order_items AS oi ON oi.order_id=o.id
    JOIN products AS p ON p.id=oi.product_id
    JOIN product_suppliers AS ps ON ps.product_id=p.id
    JOIN suppliers AS s ON s.id=ps.supplier_id
    JOIN shipments AS sh ON sh.order_id=o.id
    WHERE p.active AND s.risk_tier>=4
      AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
),
cap AS (SELECT customer_id,count(*) AS n FROM cap_orders GROUP BY customer_id),
rev AS (
 SELECT o.customer_id,sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price) AS n
 FROM cap_orders AS o JOIN order_items AS oi ON oi.order_id=o.id
 LEFT JOIN returns AS r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
 GROUP BY o.customer_id
),
promo AS (
 SELECT o.customer_id,sum(op.discount_amount) AS n FROM fo AS o
 JOIN order_promotions AS op ON op.order_id=o.id JOIN promotions AS pr ON pr.id=op.promotion_id
 WHERE pr.active GROUP BY o.customer_id
),
late AS (
 SELECT o.customer_id,count(*) AS n FROM fo AS o JOIN shipments AS sh ON sh.order_id=o.id
 WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
 GROUP BY o.customer_id
),
risky AS (
 SELECT o.customer_id,count(DISTINCT ps.supplier_id) AS n FROM fo AS o
 JOIN order_items AS oi ON oi.order_id=o.id JOIN product_suppliers AS ps ON ps.product_id=oi.product_id
 JOIN suppliers AS s ON s.id=ps.supplier_id WHERE s.risk_tier>=4 GROUP BY o.customer_id
),
last_o AS (SELECT customer_id,max(created_at) AS n FROM fo GROUP BY customer_id)
SELECT c.id AS customer_id,c.region,c.segment,
 COALESCE(cap.n,0) AS captured_order_count,
 COALESCE(rev.n,0)::numeric(20,2) AS net_item_revenue,
 COALESCE(promo.n,0)::numeric(18,2) AS active_promotion_discount,
 COALESCE(late.n,0) AS late_shipment_count,
 COALESCE(risky.n,0) AS risky_supplier_count,
 last_o.n AS last_completed_at
FROM customers AS c JOIN eligible AS e ON e.customer_id=c.id
LEFT JOIN cap ON cap.customer_id=c.id LEFT JOIN rev ON rev.customer_id=c.id
LEFT JOIN promo ON promo.customer_id=c.id LEFT JOIN late ON late.customer_id=c.id
LEFT JOIN risky ON risky.customer_id=c.id LEFT JOIN last_o ON last_o.customer_id=c.id
WHERE c.active;