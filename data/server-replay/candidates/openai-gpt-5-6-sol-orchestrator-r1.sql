WITH cp AS MATERIALIZED (
 SELECT DISTINCT order_id FROM payments WHERE status='captured'
), fo AS MATERIALIZED (
 SELECT o.id,o.customer_id,o.created_at,(cp.order_id IS NOT NULL) AS captured
 FROM orders o LEFT JOIN cp ON cp.order_id=o.id
 WHERE lower(o.status)='completed' AND o.created_at::date>=DATE '2024-01-01'
), base AS (SELECT customer_id,max(created_at) AS last_completed_at FROM fo GROUP BY customer_id),
cap AS (SELECT customer_id,count(*) AS captured_order_count FROM fo WHERE captured GROUP BY customer_id),
rev AS (SELECT f.customer_id,sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price) AS net_item_revenue FROM fo f JOIN order_items oi ON oi.order_id=f.id LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no WHERE f.captured GROUP BY f.customer_id),
promo AS (SELECT f.customer_id,sum(op.discount_amount) AS active_promotion_discount FROM fo f JOIN order_promotions op ON op.order_id=f.id JOIN promotions pr ON pr.id=op.promotion_id WHERE pr.active GROUP BY f.customer_id),
late AS (SELECT f.customer_id,count(*) AS late_shipment_count FROM fo f JOIN shipments sh ON sh.order_id=f.id WHERE sh.delivered_at IS NULL OR sh.delivered_at>sh.shipped_at+INTERVAL '10 days' GROUP BY f.customer_id),
risky AS (SELECT f.customer_id,count(DISTINCT ps.supplier_id) AS risky_supplier_count FROM fo f JOIN order_items oi ON oi.order_id=f.id JOIN product_suppliers ps ON ps.product_id=oi.product_id JOIN suppliers s ON s.id=ps.supplier_id WHERE s.risk_tier>=4 GROUP BY f.customer_id),
eligible AS (SELECT DISTINCT f.customer_id FROM fo f JOIN order_items oi ON oi.order_id=f.id JOIN products p ON p.id=oi.product_id JOIN product_suppliers ps ON ps.product_id=p.id JOIN suppliers s ON s.id=ps.supplier_id JOIN shipments sh ON sh.order_id=f.id WHERE p.active AND s.risk_tier>=4 AND (sh.delivered_at IS NULL OR sh.delivered_at>sh.shipped_at+INTERVAL '10 days'))
SELECT c.id AS customer_id,c.region,c.segment,COALESCE(cap.captured_order_count,0) AS captured_order_count,COALESCE(rev.net_item_revenue,0)::numeric(20,2) AS net_item_revenue,COALESCE(promo.active_promotion_discount,0)::numeric(18,2) AS active_promotion_discount,COALESCE(late.late_shipment_count,0) AS late_shipment_count,COALESCE(risky.risky_supplier_count,0) AS risky_supplier_count,base.last_completed_at
FROM eligible e JOIN customers c ON c.id=e.customer_id JOIN base ON base.customer_id=c.id LEFT JOIN cap ON cap.customer_id=c.id LEFT JOIN rev ON rev.customer_id=c.id LEFT JOIN promo ON promo.customer_id=c.id LEFT JOIN late ON late.customer_id=c.id LEFT JOIN risky ON risky.customer_id=c.id WHERE c.active