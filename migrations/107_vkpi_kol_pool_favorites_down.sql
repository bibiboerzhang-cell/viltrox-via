-- 107 down: 收藏持久层回滚。
-- 注意:DROP TABLE 连收藏数据一起删(C5 backfill 721 行可由脚本幂等重建,无不可逆损失);
-- 不触碰 vkpi_kol_pool / staff 本体。

DROP INDEX IF EXISTS idx_vkpi_kol_pool_favorites_staff;
DROP TABLE IF EXISTS vkpi_kol_pool_favorites;
