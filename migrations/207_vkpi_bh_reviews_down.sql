-- 207 回滚:删 B&H 产品用户评论表(评论可随时用 reviews actor 重抓重建,无不可恢复业务数据)。
BEGIN;
DROP TABLE IF EXISTS vkpi_bh_reviews;
COMMIT;
