WITH filtered_orders AS MATERIALIZED (
 SELECT o.id,o.customer_id,o.created_at FROM orders o
 WHERE lower(o.status)='completed' AND o.created_at::date>=DATE '2024-01-01'
), captured_orders AS MATERIALIZED (
 SELECT fo.id,fo.customer_id FROM filtered_orders fo
 WHERE EXISTS (SELECT 1 FROM payments pay WHERE pay.order_id=fo.id AND pay.status='captured')
), metrics AS MATERIALIZED (
 SELECT customer_id, count(*)::bigint captured_order_count, NULL::numeric net_item_revenue,
 NULL::numeric active_promotion_discount, NULL::bigint late_shipment_count,
 NULL::bigint risky_supplier_count, NULL::timestamp without time zone last_completed_at
 FROM captured_orders GROUP BY customer_id
 UNION ALL
 SELECT co.customer_id,NULL::bigint,sum((oi.quantity-COALESCE(r.quantity,0))*oi.unit_price),NULL::numeric,NULL::bigint,NULL::bigint,NULL::timestamp without time zone
 FROM captured_orders co JOIN order_items oi ON oi.order_id=co.id LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no GROUP BY co.customer_id
 UNION ALL
 SELECT fo.customer_id,NULL::bigint,NULL::numeric,sum(op.discount_amount),NULL::bigint,NULL::bigint,NULL::timestamp without time zone
 FROM filtered_orders fo JOIN order_promotions op ON op.order_id=fo.id JOIN promotions pr ON pr.id=op.promotion_id WHERE pr.active GROUP BY fo.customer_id
 UNION ALL
 SELECT fo.customer_id,NULL::bigint,NULL::numeric,NULL::numeric,count(*)::bigint,NULL::bigint,NULL::timestamp without time zone
 FROM filtered_orders fo JOIN shipments sh ON sh.order_id=fo.id WHERE sh.delivered_at IS NULL OR sh.delivered_at>sh.shipped_at+INTERVAL '10 days' GROUP BY fo.customer_id
 UNION ALL
 SELECT fo.customer_id,NULL::bigint,NULL::numeric,NULL::numeric,NULL::bigint,count(DISTINCT ps.supplier_id)::bigint,NULL::timestamp without time zone
 FROM filtered_orders fo JOIN order_items oi ON oi.order_id=fo.id JOIN product_suppliers ps ON ps.product_id=oi.product_id JOIN suppliers s ON s.id=ps.supplier_id WHERE s.risk_tier>=4 GROUP BY fo.customer_id
 UNION ALL
 SELECT customer_id,NULL::bigint,NULL::numeric,NULL::numeric,NULL::bigint,NULL::bigint,max(created_at) FROM filtered_orders GROUP BY customer_id
), summary AS (
 SELECT customer_id,max(captured_order_count) captured_order_count,max(net_item_revenue) net_item_revenue,
 max(active_promotion_discount) active_promotion_discount,max(late_shipment_count) late_shipment_count,
 max(risky_supplier_count) risky_supplier_count,max(last_completed_at) last_completed_at
 FROM metrics GROUP BY customer_id
), eligible AS (
 SELECT DISTINCT fo.customer_id FROM filtered_orders fo
 WHERE EXISTS (SELECT 1 FROM shipments sh WHERE sh.order_id=fo.id AND (sh.delivered_at IS NULL OR sh.delivered_at>sh.shipped_at+INTERVAL '10 days'))
 AND EXISTS (SELECT 1 FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN product_suppliers ps ON ps.product_id=p.id JOIN suppliers s ON s.id=ps.supplier_id WHERE oi.order_id=fo.id AND p.active AND s.risk_tier>=4)
)
SELECT c.id AS customer_id,c.region,c.segment,
 COALESCE(sm.captured_order_count,0)::bigint AS captured_order_count,
 COALESCE(sm.net_item_revenue,0)::numeric(20,2) AS net_item_revenue,
 COALESCE(sm.active_promotion_discount,0)::numeric(18,2) AS active_promotion_discount,
 COALESCE(sm.late_shipment_count,0)::bigint AS late_shipment_count,
 COALESCE(sm.risky_supplier_count,0)::bigint AS risky_supplier_count,
 sm.last_completed_at
FROM eligible e JOIN customers c ON c.id=e.customer_id AND c.active JOIN summary sm ON sm.customer_id=c.id