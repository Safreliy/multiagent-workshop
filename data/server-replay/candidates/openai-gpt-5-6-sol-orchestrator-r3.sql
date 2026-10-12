WITH base_orders AS MATERIALIZED (
 SELECT o.id,o.customer_id,o.created_at FROM orders o
 WHERE lower(o.status)='completed' AND o.created_at::date>=DATE '2024-01-01'
), captured_orders AS MATERIALIZED (
 SELECT b.id,b.customer_id FROM base_orders b WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=b.id AND pay.status='captured')
), captured_counts AS (
 SELECT customer_id,count(*) captured_order_count FROM captured_orders GROUP BY customer_id
), net_revenue AS (
 SELECT b.customer_id,sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price) amount
 FROM captured_orders b JOIN order_items oi ON oi.order_id=b.id
 LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
 GROUP BY b.customer_id
), promotion_discounts AS (
 SELECT b.customer_id,sum(op.discount_amount) amount
 FROM base_orders b JOIN order_promotions op ON op.order_id=b.id
 JOIN promotions pr ON pr.id=op.promotion_id
 WHERE pr.active GROUP BY b.customer_id
), late_shipments AS MATERIALIZED (
 SELECT b.customer_id,b.id order_id FROM base_orders b JOIN shipments sh ON sh.order_id=b.id
 WHERE sh.delivered_at IS NULL OR sh.delivered_at>sh.shipped_at+INTERVAL '10 days'
), late_counts AS (
 SELECT customer_id,count(*) late_shipment_count FROM late_shipments GROUP BY customer_id
), risky_counts AS (
 SELECT b.customer_id,count(DISTINCT ps.supplier_id) risky_supplier_count
 FROM base_orders b JOIN order_items oi ON oi.order_id=b.id
 JOIN product_suppliers ps ON ps.product_id=oi.product_id
 JOIN suppliers s ON s.id=ps.supplier_id
 WHERE s.risk_tier>=4 GROUP BY b.customer_id
), last_completed AS (
 SELECT customer_id,max(created_at) last_completed_at FROM base_orders GROUP BY customer_id
), eligible_customers AS (
 SELECT DISTINCT ls.customer_id FROM late_shipments ls
 WHERE EXISTS (
  SELECT 1 FROM order_items oi
  JOIN products p ON p.id=oi.product_id
  JOIN product_suppliers ps ON ps.product_id=p.id
  JOIN suppliers s ON s.id=ps.supplier_id
  WHERE oi.order_id=ls.order_id AND p.active AND s.risk_tier>=4
 )
)
SELECT c.id AS customer_id,c.region,c.segment,
 COALESCE(cc.captured_order_count,0) AS captured_order_count,
 COALESCE(nr.amount,0)::numeric(20,2) AS net_item_revenue,
 COALESCE(pd.amount,0)::numeric(18,2) AS active_promotion_discount,
 COALESCE(lc.late_shipment_count,0) AS late_shipment_count,
 COALESCE(rc.risky_supplier_count,0) AS risky_supplier_count,
 la.last_completed_at
FROM eligible_customers ec
JOIN customers c ON c.id=ec.customer_id
LEFT JOIN captured_counts cc ON cc.customer_id=c.id
LEFT JOIN net_revenue nr ON nr.customer_id=c.id
LEFT JOIN promotion_discounts pd ON pd.customer_id=c.id
LEFT JOIN late_counts lc ON lc.customer_id=c.id
LEFT JOIN risky_counts rc ON rc.customer_id=c.id
LEFT JOIN last_completed la ON la.customer_id=c.id
WHERE c.active