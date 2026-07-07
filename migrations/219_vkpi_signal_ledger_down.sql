-- 219 回滚:删外部/内部信号账本表。
-- 注意:入账信号会随表删除,回滚前如需留档请先自行导出;
-- summarize_for_preview 在表缺失时诚实回 data_missing,不炸接口。
BEGIN;
DROP TABLE IF EXISTS vkpi_signal_ledger;
COMMIT;
