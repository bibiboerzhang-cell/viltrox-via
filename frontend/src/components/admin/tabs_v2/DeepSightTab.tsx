import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import { PageHeader, SectionLabel } from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

export function DeepSightTab({ token: _token }: Props) {
  return (
    <div>
      <PageHeader
        title="DeepSight"
        subtitle="Triad council · consensus · divergence · cache"
        actions={<button type="button" className="ax-btn"><Icons.via /> Run diagnosis</button>}
      />
      <div style={{ padding: 16 }}>
        <SectionLabel>Triad Council</SectionLabel>
        <div className="ax-card">
          Claude / GPT / Gemini 三脑议会、一致性评分和分歧点高亮入口已挂载。后续接入 deepsight evidence-pack 和 diagnose。
        </div>
      </div>
    </div>
  );
}

export default DeepSightTab;
