WITH base AS MATERIALIZED (
  SELECT o.id,o.customer_id,o.created_at FROM orders o
  WHERE lower(o.status)='completed' AND o.created_at::date>=DATE '2024-01-01'
), eligible AS MATERIALIZED (
  SELECT DISTINCT b.customer_id FROM base b
  JOIN order_items oi ON oi.order_id=b.id
  JOIN products p ON p.id=oi.product_id AND p.active
  JOIN product_suppliers ps ON ps.product_id=p.id
  JOIN suppliers s ON s.id=ps.supplier_id AND s.risk_tier>=4
  JOIN shipments sh ON sh.order_id=b.id
  WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
), captured AS MATERIALIZED (
  SELECT b.* FROM base b
  WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=b.id AND pay.status='captured')
), ca AS (
  SELECT customer_id,count(*) AS cnt FROM captured GROUP BY customer_id
), lastx AS (
  SELECT customer_id,max(created_at) AS last_at FROM base GROUP BY customer_id
), net AS (
  SELECT x.customer_id,sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price) AS val
  FROM captured x JOIN order_items oi ON oi.order_id=x.id
  LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
  GROUP BY x.customer_id
), promo AS (
  SELECT b.customer_id,sum(op.discount_amount) AS val
  FROM base b JOIN order_promotions op ON op.order_id=b.id
  JOIN promotions pr ON pr.id=op.promotion_id AND pr.active
  GROUP BY b.customer_id
), late AS (
  SELECT b.customer_id,count(*) AS cnt
  FROM base b JOIN shipments sh ON sh.order_id=b.id
  WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
  GROUP BY b.customer_id
), risky AS (
  SELECT b.customer_id,count(DISTINCT ps.supplier_id) AS cnt
  FROM base b JOIN order_items oi ON oi.order_id=b.id
  JOIN product_suppliers ps ON ps.product_id=oi.product_id
  JOIN suppliers s ON s.id=ps.supplier_id AND s.risk_tier>=4
  GROUP BY b.customer_id
)
SELECT c.id AS customer_id,c.region,c.segment,
       COALESCE(ca.cnt,0) AS captured_order_count,
       COALESCE(net.val,0)::numeric(20,2) AS net_item_revenue,
       COALESCE(promo.val,0)::numeric(18,2) AS active_promotion_discount,
       COALESCE(late.cnt,0) AS late_shipment_count,
       COALESCE(risky.cnt,0) AS risky_supplier_count,
       lastx.last_at AS last_completed_at
FROM customers c
JOIN eligible e ON e.customer_id=c.id
LEFT JOIN ca ON ca.customer_id=c.id
LEFT JOIN net ON net.customer_id=c.id
LEFT JOIN promo ON promo.customer_id=c.id
LEFT JOIN late ON late.customer_id=c.id
LEFT JOIN risky ON risky.customer_id=c.id
LEFT JOIN lastx ON lastx.customer_id=c.id
WHERE c.active