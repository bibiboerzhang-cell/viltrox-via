// KOL 自助门户(只读·token 隔离)— standalone 页面,不进 admin V615 壳。
//
// 从 URL 取 token(支持 /portal/kol/{token} 路径、#token、或 ?token=),调公开端点,
// 渲染「该 KOL 自己的」点击/订单/GMV/被分配项目。无 token / 404 → 友好提示,
// 不泄露该 KOL 是否存在。纯只读 UI,无任何写/删/改控件。
import { useEffect, useMemo, useState } from "react";
import {
  fetchPortal,
  PortalInvalidError,
  type PortalData,
} from "../../../services/vkpi/kolPortal-api";

function readTokenFromUrl(): string {
  if (typeof window === "undefined") return "";
  const { pathname, hash, search } = window.location;

  // 1) 路径形式 /portal/kol/{token}
  const pathMatch = pathname.match(/\/portal\/kol\/([^/?#]+)/);
  if (pathMatch && pathMatch[1]) {
    return decodeURIComponent(pathMatch[1]);
  }

  // 2) query ?token=...
  try {
    const qp = new URLSearchParams(search).get("token");
    if (qp) return qp.trim();
  } catch {
    /* ignore */
  }

  // 3) hash(#token 或 #/portal/kol/{token} 或 #token=...)
  const rawHash = hash.replace(/^#\/?/, "");
  const hashPathMatch = rawHash.match(/portal\/kol\/([^/?#]+)/);
  if (hashPathMatch && hashPathMatch[1]) {
    return decodeURIComponent(hashPathMatch[1]);
  }
  if (rawHash.startsWith("token=")) {
    return decodeURIComponent(rawHash.slice("token=".length));
  }
  if (rawHash && !rawHash.includes("=") && !rawHash.includes("/")) {
    return decodeURIComponent(rawHash);
  }
  return "";
}

function formatMoneyCents(cents: number, currency: string): string {
  const amount = (Number(cents) || 0) / 100;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${(currency || "USD")} ${amount.toFixed(2)}`;
  }
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

const wrapStyle: React.CSSProperties = {
  maxWidth: 960,
  margin: "0 auto",
  padding: "32px 20px",
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
  color: "#1f2933",
};

const cardStyle: React.CSSProperties = {
  border: "1px solid #e4e7eb",
  borderRadius: 12,
  padding: 20,
  background: "#fff",
  boxShadow: "0 1px 3px rgba(15, 23, 42, 0.06)",
};

const kpiGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 16,
  margin: "24px 0",
};

const kpiValueStyle: React.CSSProperties = {
  fontSize: 28,
  fontWeight: 700,
  margin: "4px 0 0",
};

const kpiLabelStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#7b8794",
  textTransform: "uppercase",
  letterSpacing: 0.4,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 14,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "10px 12px",
  borderBottom: "2px solid #e4e7eb",
  color: "#52606d",
  fontWeight: 600,
};

const tdStyle: React.CSSProperties = {
  padding: "10px 12px",
  borderBottom: "1px solid #f0f2f4",
};

export default function KolPortalPage(): JSX.Element {
  const token = useMemo(() => readTokenFromUrl(), []);
  const [data, setData] = useState<PortalData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [invalid, setInvalid] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setLoading(false);
      setInvalid(true);
      return;
    }
    setLoading(true);
    setInvalid(false);
    setErrorMessage("");
    fetchPortal(token)
      .then((result) => {
        if (cancelled) return;
        setData(result);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof PortalInvalidError) {
          setInvalid(true);
        } else {
          setErrorMessage(
            err instanceof Error ? err.message : "加载失败，请稍后再试",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (loading) {
    return (
      <div style={wrapStyle}>
        <p style={{ color: "#7b8794" }}>正在加载…</p>
      </div>
    );
  }

  if (invalid) {
    return (
      <div style={wrapStyle}>
        <div style={{ ...cardStyle, textAlign: "center", padding: 40 }}>
          <h1 style={{ fontSize: 22, marginBottom: 8 }}>链接无效或已过期</h1>
          <p style={{ color: "#7b8794", margin: 0 }}>
            请向您的对接人索取一个新的专属链接。
          </p>
        </div>
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div style={wrapStyle}>
        <div style={{ ...cardStyle, textAlign: "center", padding: 40 }}>
          <h1 style={{ fontSize: 22, marginBottom: 8 }}>暂时无法加载</h1>
          <p style={{ color: "#7b8794", margin: 0 }}>{errorMessage}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={wrapStyle}>
        <div style={{ ...cardStyle, textAlign: "center", padding: 40 }}>
          <h1 style={{ fontSize: 22, marginBottom: 8 }}>链接无效或已过期</h1>
        </div>
      </div>
    );
  }

  const { kol, projects, gmv, clicks, attributions } = data;

  return (
    <div style={wrapStyle}>
      <header style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8 }}>
        {kol.avatar_url ? (
          <img
            src={kol.avatar_url}
            alt=""
            width={64}
            height={64}
            style={{ borderRadius: "50%", objectFit: "cover", flexShrink: 0 }}
          />
        ) : null}
        <div>
          <h1 style={{ fontSize: 24, margin: 0 }}>
            {kol.display_name || kol.handle || "我的数据"}
          </h1>
          <p style={{ color: "#7b8794", margin: "4px 0 0", fontSize: 14 }}>
            {[kol.platform, kol.handle && `@${kol.handle}`, kol.country]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      </header>

      <section style={kpiGridStyle} aria-label="KPI 概览">
        <div style={cardStyle}>
          <div style={kpiLabelStyle}>归因 GMV</div>
          <p style={kpiValueStyle}>{formatMoneyCents(gmv.gmv_cents, gmv.currency)}</p>
          <div style={{ fontSize: 13, color: "#7b8794" }}>
            {formatNumber(gmv.orders_count)} 笔订单
          </div>
        </div>
        <div style={cardStyle}>
          <div style={kpiLabelStyle}>总点击</div>
          <p style={kpiValueStyle}>{formatNumber(clicks.total)}</p>
          <div style={{ fontSize: 13, color: "#7b8794" }}>
            有效 {formatNumber(clicks.valid)}
          </div>
        </div>
        <div style={cardStyle}>
          <div style={kpiLabelStyle}>粉丝数</div>
          <p style={kpiValueStyle}>{formatNumber(kol.followers)}</p>
        </div>
        <div style={cardStyle}>
          <div style={kpiLabelStyle}>合作项目</div>
          <p style={kpiValueStyle}>{formatNumber(projects.length)}</p>
        </div>
      </section>

      <section style={{ ...cardStyle, marginBottom: 24 }}>
        <h2 style={{ fontSize: 18, marginTop: 0 }}>我的合作项目</h2>
        {projects.length === 0 ? (
          <p style={{ color: "#7b8794", margin: 0 }}>暂无被分配的项目。</p>
        ) : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>项目</th>
                <th style={thStyle}>产品</th>
                <th style={thStyle}>平台</th>
                <th style={thStyle}>阶段</th>
                <th style={thStyle}>状态</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.project_id}>
                  <td style={tdStyle}>{p.project_name || `#${p.project_id}`}</td>
                  <td style={tdStyle}>
                    {p.product_name || p.product_sku || "—"}
                  </td>
                  <td style={tdStyle}>{p.platform || "—"}</td>
                  <td style={tdStyle}>{p.stage || "—"}</td>
                  <td style={tdStyle}>{p.stage_status || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {attributions.length > 0 ? (
        <section style={cardStyle}>
          <h2 style={{ fontSize: 18, marginTop: 0 }}>最近归因订单</h2>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>时间</th>
                <th style={thStyle}>营收</th>
                <th style={thStyle}>佣金</th>
              </tr>
            </thead>
            <tbody>
              {attributions.map((a, idx) => (
                <tr key={`${a.occurred_at ?? "na"}-${idx}`}>
                  <td style={tdStyle}>
                    {a.occurred_at
                      ? new Date(a.occurred_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td style={tdStyle}>
                    {formatMoneyCents(a.revenue_cents, a.currency)}
                  </td>
                  <td style={tdStyle}>
                    {formatMoneyCents(a.commission_cents, a.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      <footer
        style={{ marginTop: 32, textAlign: "center", color: "#9aa5b1", fontSize: 12 }}
      >
        这是您的只读专属页面，仅展示您自己的数据。
      </footer>
    </div>
  );
}
