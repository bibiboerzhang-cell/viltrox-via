import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import { PageHeader, SectionLabel } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

export function SystemTab({ token: _token }: Props) {
  return (
    <div>
      <PageHeader
        title="System"
        subtitle="API keys · usage · models · restart · members"
        actions={<button type="button" className="ax-btn"><Icons.command /> System status</button>}
      />
      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        {["API Keys", "Usage", "Models", "Restart", "Members"].map((label) => (
          <div className="ax-card" key={label}>
            <SectionLabel>{label}</SectionLabel>
            <p style={{ margin: 0, color: "var(--ax-text-2)" }}>
              {label} 管理入口已挂载。高危写操作将走二次密码、audit log 和 owner-only 权限。
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default SystemTab;
