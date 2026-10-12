WITH fo AS MATERIALIZED (
  SELECT id, customer_id, created_at
  FROM orders
  WHERE lower(status) = 'completed'
    AND created_at::date >= DATE '2024-01-01'
),
cap AS MATERIALIZED (
  SELECT o.id, o.customer_id
  FROM fo o
  WHERE EXISTS (
    SELECT 1 FROM payments p
    WHERE p.order_id = o.id AND p.status = 'captured'
  )
),
risk_ps AS MATERIALIZED (
  SELECT ps.product_id, ps.supplier_id
  FROM product_suppliers ps
  JOIN suppliers s ON s.id = ps.supplier_id
  WHERE s.risk_tier >= 4
),
eligible AS MATERIALIZED (
  SELECT DISTINCT o.customer_id
  FROM fo o
  JOIN shipments sh ON sh.order_id = o.id
    AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
  JOIN order_items oi ON oi.order_id = o.id
  JOIN products p ON p.id = oi.product_id AND p.active
  JOIN (SELECT DISTINCT product_id FROM risk_ps) rp ON rp.product_id = p.id
),
captured AS (
  SELECT customer_id, count(*) AS n FROM cap GROUP BY customer_id
),
net AS (
  SELECT o.customer_id,
         sum((oi.quantity - COALESCE(r.quantity, 0)) * oi.unit_price) AS amount
  FROM cap o
  JOIN order_items oi ON oi.order_id = o.id
  LEFT JOIN returns r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
  GROUP BY o.customer_id
),
promo AS (
  SELECT o.customer_id, sum(op.discount_amount) AS amount
  FROM fo o
  JOIN order_promotions op ON op.order_id = o.id
  JOIN promotions pr ON pr.id = op.promotion_id
  WHERE pr.active
  GROUP BY o.customer_id
),
late AS (
  SELECT o.customer_id, count(*) AS n
  FROM fo o
  JOIN shipments sh ON sh.order_id = o.id
  WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
  GROUP BY o.customer_id
),
risky AS (
  SELECT o.customer_id, count(DISTINCT rp.supplier_id) AS n
  FROM fo o
  JOIN order_items oi ON oi.order_id = o.id
  JOIN risk_ps rp ON rp.product_id = oi.product_id
  GROUP BY o.customer_id
),
last_o AS (
  SELECT customer_id, max(created_at) AS at FROM fo GROUP BY customer_id
)
SELECT c.id AS customer_id,
       c.region,
       c.segment,
       COALESCE(captured.n, 0::bigint) AS captured_order_count,
       COALESCE(net.amount, 0)::numeric(20, 2) AS net_item_revenue,
       COALESCE(promo.amount, 0)::numeric(18, 2) AS active_promotion_discount,
       COALESCE(late.n, 0::bigint) AS late_shipment_count,
       COALESCE(risky.n, 0::bigint) AS risky_supplier_count,
       last_o.at AS last_completed_at
FROM eligible e
JOIN customers c ON c.id = e.customer_id
LEFT JOIN captured ON captured.customer_id = c.id
LEFT JOIN net ON net.customer_id = c.id
LEFT JOIN promo ON promo.customer_id = c.id
LEFT JOIN late ON late.customer_id = c.id
LEFT JOIN risky ON risky.customer_id = c.id
LEFT JOIN last_o ON last_o.customer_id = c.id
WHERE c.active