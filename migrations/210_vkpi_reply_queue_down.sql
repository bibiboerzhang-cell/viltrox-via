-- 210 回滚:删评论区销售员 ReplyQueue 待办表(纯待办队列,可随时重建,筛选可重跑无业务损失)。
BEGIN;
DROP TABLE IF EXISTS vkpi_reply_queue;
COMMIT;
