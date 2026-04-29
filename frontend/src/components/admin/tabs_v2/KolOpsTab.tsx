import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import { PageHeader, SectionLabel } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

export function KolOpsTab({ token: _token }: Props) {
  return (
    <div>
      <PageHeader
        title="KOL Ops"
        subtitle="KOL list · CSV import · outreach · campaigns · content scoring"
        actions={<button type="button" className="ax-btn"><Icons.users /> Import CSV</button>}
      />
      <div style={{ padding: 16 }}>
        <SectionLabel>Phase A</SectionLabel>
        <div className="ax-card">
          KOL 列表、CSV 导入、对接日志、活动和 Claude 伪推荐入口已挂载。后续接入 /api/admin/kol 数据层。
        </div>
      </div>
    </div>
  );
}

export default KolOpsTab;
