-- 213 回滚:删本地 worker 设备注册表与短期任务租约表。
-- 注意:租约 staging 结果(result_json)会随表删除,回滚前如需留档请先自行导出;
-- registry.py 在表缺失时诚实降级(注册/领活回明确错误,接口不炸),apify_jobs 不受影响
-- (claimed 标记只在 payload 的 local_lease_id 键上,可由人工清除后任务照常重派)。
BEGIN;
DROP TABLE IF EXISTS vkpi_local_task_leases;
DROP TABLE IF EXISTS vkpi_worker_devices;
COMMIT;
