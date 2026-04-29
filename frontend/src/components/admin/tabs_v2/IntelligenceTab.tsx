import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import { PageHeader, SectionLabel } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

export function IntelligenceTab({ token: _token }: Props) {
  return (
    <div>
      <PageHeader
        title="Intelligence"
        subtitle="Account matrix · lens monitor · comparisons · learning"
        actions={<button type="button" className="ax-btn"><Icons.trending /> Refresh</button>}
      />
      <div style={{ padding: 16 }}>
        <SectionLabel>Phase A Entry</SectionLabel>
        <div className="ax-card">
          多平台扫描、镜头监控、双镜头对比和 URL 学习入口已挂载。后续接入 scan-account、scan-matrix、monitor、compare、learn/url。
        </div>
      </div>
    </div>
  );
}

export default IntelligenceTab;
