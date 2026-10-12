WITH co AS MATERIALIZED (
  SELECT id, customer_id, created_at
  FROM orders
  WHERE lower(status) = 'completed'
    AND created_at >= TIMESTAMP '2024-01-01'
), cap AS MATERIALIZED (
  SELECT DISTINCT order_id FROM payments WHERE status = 'captured'
), base AS (
  SELECT co.customer_id, count(cap.order_id) AS captured_order_count, max(co.created_at) AS last_completed_at
  FROM co LEFT JOIN cap ON cap.order_id = co.id GROUP BY co.customer_id
), net AS (
  SELECT co.customer_id, sum((oi.quantity - COALESCE(r.quantity,0))*oi.unit_price) AS amount
  FROM co JOIN cap ON cap.order_id=co.id JOIN order_items oi ON oi.order_id=co.id
  LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no GROUP BY co.customer_id
), disc AS (
  SELECT co.customer_id, sum(op.discount_amount) AS amount FROM co
  JOIN order_promotions op ON op.order_id=co.id JOIN promotions pr ON pr.id=op.promotion_id AND pr.active GROUP BY co.customer_id
), late AS (
  SELECT co.customer_id,count(*) AS cnt FROM co JOIN shipments sh ON sh.order_id=co.id
  WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days' GROUP BY co.customer_id
), risky AS (
  SELECT co.customer_id,count(DISTINCT ps.supplier_id) AS cnt FROM co JOIN order_items oi ON oi.order_id=co.id
  JOIN product_suppliers ps ON ps.product_id=oi.product_id JOIN suppliers s ON s.id=ps.supplier_id AND s.risk_tier>=4 GROUP BY co.customer_id
), eligible AS (
  SELECT DISTINCT co.customer_id FROM co JOIN shipments sh ON sh.order_id=co.id AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
  JOIN order_items oi ON oi.order_id=co.id JOIN products p ON p.id=oi.product_id AND p.active
  JOIN product_suppliers ps ON ps.product_id=p.id JOIN suppliers s ON s.id=ps.supplier_id AND s.risk_tier>=4
)
SELECT c.id AS customer_id,c.region,c.segment,b.captured_order_count,
 COALESCE(net.amount,0)::numeric(20,2) AS net_item_revenue,
 COALESCE(disc.amount,0)::numeric(18,2) AS active_promotion_discount,
 COALESCE(late.cnt,0::bigint) AS late_shipment_count,
 COALESCE(risky.cnt,0::bigint) AS risky_supplier_count,b.last_completed_at
FROM eligible e JOIN customers c ON c.id=e.customer_id AND c.active JOIN base b ON b.customer_id=c.id
LEFT JOIN net ON net.customer_id=c.id LEFT JOIN disc ON disc.customer_id=c.id LEFT JOIN late ON late.customer_id=c.id LEFT JOIN risky ON risky.customer_id=c.id