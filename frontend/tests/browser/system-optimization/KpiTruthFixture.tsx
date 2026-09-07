import { useState } from "react";
import { StaffProfileDrawer } from "../../../src/components/vkpi/drawers/StaffProfileDrawer";
import { KolProfileDrawer } from "../../../src/components/vkpi/drawers/KolProfileDrawer";
import type { VkpiKolDetail, VkpiKolProfile, VkpiStaffMember, VkpiStaffProfile } from "../../../src/components/vkpi/vkpiTypes";
import drawerStyles from "../../../src/components/vkpi/styles/vkpi-settings-traffic.css?inline";
import profileStyles from "../../../src/components/vkpi/styles/vkpi-alerts-detail.css?inline";
import darkStyles from "../../../src/components/vkpi/styles/vkpi-settings-dark.css?inline";

const member: VkpiStaffMember = {
  id: "990071", name: "【合成】KPI 待核成员", email: "", role: "manager", active: true,
  employeeCode: "SYNTHETIC-KPI-ONLY", vkpiPermission: "read",
};
const staffProfile: VkpiStaffProfile = {
  staff: { staff_name: member.name }, summary: { workload_score: null, kpi_credit: 987654 },
  projects: [], claims: [], links: [], attributions: [], costs: [], channels: [], audit_events: [], kpi_ledger: [],
  kpi_breakdown: {
    grouped: [
      { metric_key: "synthetic.group.unknown", metric_label: "合成分组：待核工作量", total_value: null, aggregation_eligible: false, source_count: 1 },
      { metric_key: "synthetic.group.zero", metric_label: "合成分组：已核实零值", total_value: 0, aggregation_eligible: true, source_count: 1 },
    ],
    source_rows: [
      { id: "synthetic-source-unknown", metric_label: "合成来源：待核消息", metric_value: null, aggregation_eligible: false, source_type: "synthetic_fixture" },
      { id: "synthetic-source-zero", metric_label: "合成来源：已核实零值", metric_value: 0, aggregation_eligible: true, source_type: "synthetic_fixture" },
    ],
  },
};
const fallbackKol: VkpiKolDetail = {
  id: "990072", name: "【合成】消息待核 KOL", handle: "synthetic-no-account", platform: "YouTube",
  subscribersLabel: "待核验", videosLabel: "待核验", engagementLabel: "待核验", recentContent: [], messages: [],
  shortLink: { slug: "", destination: "", clicks: 0, orders: 0, gmv: 0, roi: 0 }, followUpNote: "合成夹具，不联系任何人",
};
const kolProfile: VkpiKolProfile = {
  kol: {}, projects: [], links: [], posts: [], claim_history: [], sales_attributions: [], costs: [],
  messages: [{ id: "synthetic-manual-message", source: "manual", direction: "inbound", captured_at: "2026-09-07T00:00:00Z",
    snippet: "【合成】手工回复说明：没有供应商收发回执。", communication_truth: {
      transport_status: "unverified", evidence_class: "manual_record", claim_status: "descriptive_only",
      sent: null, replied: null, transport_outcome_eligible: false,
    } }],
  kpi_ledger: [
    { id: "synthetic-kol-source-unknown", metric_key: "合成KOL来源：待核", metric_value: null, aggregation_eligible: false },
    { id: "synthetic-kol-source-zero", metric_key: "合成KOL来源：零值", metric_value: 0, aggregation_eligible: true },
  ],
  kpi_summary: [
    { metric_key: "合成KOL汇总：待核", total_value: null, aggregation_eligible: false, row_count: 1 },
    { metric_key: "合成KOL汇总：零值", total_value: 0, aggregation_eligible: true, row_count: 1 },
  ],
};

// Production styles are mounted only in this scenario. The scoped overrides put
// two otherwise fixed drawers in a fixture comparison frame; no data UI is copied.
const comparisonLayout = `
.fixture-kpi-truth { background: transparent; }
.fixture-kpi-truth .fixture-kpi-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.fixture-kpi-truth .vkpi-evidence-drawer { position: relative; inset: auto; z-index: auto; width: auto; height: 920px; box-sizing: border-box; border: 1px solid var(--ds-line); border-radius: 12px; box-shadow: none; min-width: 0; }
.fixture-kpi-truth .vkpi-evidence-drawer > header, .fixture-kpi-truth .vkpi-result-grid, .fixture-kpi-truth .vkpi-profile-card { flex-shrink: 0; }
.fixture-kpi-truth .vkpi-evidence-list { min-height: 0; flex: 1; }
.fixture-kpi-truth .vkpi-evidence-list > article { flex-shrink: 0; }
.fixture-kpi-truth .vkpi-evidence-list p { color: var(--ds-text-2); }
@media(max-width: 800px) { .fixture-kpi-truth .fixture-kpi-columns { grid-template-columns: 1fr; } }
`;
const keepOpen = () => {};

export function KpiTruthFixture() {
  const [knownZero, setKnownZero] = useState(false);
  const profile = { ...staffProfile, summary: { ...staffProfile.summary, workload_score: knownZero ? 0 : null } };
  return <section className="fixture-kpi-truth cockpit-settings-dark" aria-label="真实 KPI 与消息组件 · 合成数据">
    <style>{`${drawerStyles}\n${profileStyles}\n${darkStyles}\n${comparisonLayout}`}</style>
    <div className="fixture-controls">
      <button type="button" disabled={knownZero} onClick={() => setKnownZero(true)}>切换合成工作量为已核实 0</button>
      <p>本场景固定打开两个真实抽屉；只切换合成 props。关闭按钮不操作业务页面，不提供 token、头像或外链。</p>
    </div>
    <div className="fixture-kpi-columns">
      <StaffProfileDrawer member={member} profile={profile} onClose={keepOpen} onSelectProject={keepOpen} />
      <KolProfileDrawer profile={kolProfile} fallbackKol={fallbackKol} viewMode="employee" onClose={keepOpen} />
    </div>
  </section>;
}
