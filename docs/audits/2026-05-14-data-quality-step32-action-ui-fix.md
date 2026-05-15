# P4 Step32 - Data Quality Action UI Fix

日期: 2026-05-14  
范围: Data Quality 页面动作治理小改  
性质: 前端小范围修复, 后端接口不变

## 本轮交付

- `DataQualityPage.tsx`
  - 将行内动作收进 `处理动作` 菜单。
  - 将动作分为 `处理 / 分派 / 补救` 三组。
  - `已处理 / 忽略` 增加 `window.confirm` 二次确认。
  - `重检 / 补证据` 增加 tooltip, 明确当前只是记录请求, 不自动修复数据。
  - 补出后端已支持的 `重新打开` 入口。
- `VkpiDashboard.css`
  - 增加 `.vkpi-dq-action-menu` 与 `.vkpi-dq-action-group` 样式。
- `scripts/smoke_vkpi_p4_32_data_quality_action_ui_contract.py`
  - 静态保护 Data Quality 动作治理文案和入口不被后续删掉。

## 验证

```bash
./scripts/run_smoke.sh smoke_vkpi_data_quality.py smoke_vkpi_p4_32_data_quality_action_ui_contract.py
```

结果:

```text
PASS=2 / FAIL=0 / TOTAL=2
```

```bash
cd frontend && npm run build
```

结果:

```text
PASS
```

## 当前状态

Data Quality 不再是“按钮堆在一行”的状态, 但仍不是完整 remediation center。

仍留给 P4/P5 的工作:

- `重检` 目前只记录请求, 后续可接入单问题重算 worker。
- `补证据` 目前只记录动作, 后续可打开项目/红人证据上传抽屉。
- `重新打开` 已有入口, 但尚未做按问题状态隐藏/显示。

## 备份

```text
/Users/bibiboer/Documents/V-KPI-backups/before-p4-step32-data-quality-action-ui-20260514-190404.tar.gz
```
