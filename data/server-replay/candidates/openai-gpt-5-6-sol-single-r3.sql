WITH
fo AS MATERIALIZED (
  SELECT id, customer_id, created_at
  FROM orders
  WHERE lower(status) = 'completed'
    AND created_at::date >= DATE '2024-01-01'
),
cp AS MATERIALIZED (
  SELECT DISTINCT pay.order_id
  FROM payments pay
  JOIN fo o ON o.id = pay.order_id
  WHERE pay.status = 'captured'
),
rp AS MATERIALIZED (
  SELECT ps.product_id, ps.supplier_id
  FROM product_suppliers ps
  JOIN suppliers s ON s.id = ps.supplier_id
  WHERE s.risk_tier >= 4
),
lo AS MATERIALIZED (
  SELECT o.id, o.customer_id
  FROM fo o
  JOIN shipments sh ON sh.order_id = o.id
  WHERE sh.delivered_at IS NULL
     OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
),
base AS (
  SELECT o.customer_id,
         count(cp.order_id) AS captured_order_count,
         max(o.created_at) AS last_completed_at
  FROM fo o
  LEFT JOIN cp ON cp.order_id = o.id
  GROUP BY o.customer_id
),
rev AS (
  SELECT o.customer_id,
         sum((oi.quantity - COALESCE(r.quantity, 0)) * oi.unit_price) AS v
  FROM fo o
  JOIN cp ON cp.order_id = o.id
  JOIN order_items oi ON oi.order_id = o.id
  LEFT JOIN returns r ON r.order_id = oi.order_id AND r.line_no = oi.line_no
  GROUP BY o.customer_id
),
promo AS (
  SELECT o.customer_id, sum(op.discount_amount) AS v
  FROM fo o
  JOIN order_promotions op ON op.order_id = o.id
  JOIN promotions pr ON pr.id = op.promotion_id AND pr.active
  GROUP BY o.customer_id
),
late AS (
  SELECT customer_id, count(*) AS n
  FROM lo
  GROUP BY customer_id
),
risky AS (
  SELECT o.customer_id, count(DISTINCT rp.supplier_id) AS n
  FROM fo o
  JOIN order_items oi ON oi.order_id = o.id
  JOIN rp ON rp.product_id = oi.product_id
  GROUP BY o.customer_id
),
qual AS (
  SELECT DISTINCT lo.customer_id
  FROM lo
  JOIN order_items oi ON oi.order_id = lo.id
  JOIN products p ON p.id = oi.product_id AND p.active
  JOIN rp ON rp.product_id = p.id
)
SELECT c.id AS customer_id,
       c.region,
       c.segment,
       base.captured_order_count,
       COALESCE(rev.v, 0)::numeric(20, 2) AS net_item_revenue,
       COALESCE(promo.v, 0)::numeric(18, 2) AS active_promotion_discount,
       COALESCE(late.n, 0::bigint) AS late_shipment_count,
       COALESCE(risky.n, 0::bigint) AS risky_supplier_count,
       base.last_completed_at
FROM qual
JOIN customers c ON c.id = qual.customer_id AND c.active
JOIN base ON base.customer_id = c.id
LEFT JOIN rev ON rev.customer_id = c.id
LEFT JOIN promo ON promo.customer_id = c.id
LEFT JOIN late ON late.customer_id = c.id
LEFT JOIN risky ON risky.customer_id = c.id;