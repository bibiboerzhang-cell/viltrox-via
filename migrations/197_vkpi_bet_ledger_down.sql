-- 197_vkpi_bet_ledger_down.sql — 回滚押注账本。
BEGIN;
DROP TABLE IF EXISTS vkpi_bet_ledger;
COMMIT;
