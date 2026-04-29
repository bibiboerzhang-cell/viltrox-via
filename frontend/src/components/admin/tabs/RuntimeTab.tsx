import { useTranslation } from "react-i18next";

import type { AdminRuntimeSnapshot } from "../../../services/admin.service";
import { Panel } from "../../ui";
import { JsonInfoList } from "../shared";

interface RuntimeTabProps {
  runtime: AdminRuntimeSnapshot | null;
}

export function RuntimeTab({ runtime }: RuntimeTabProps) {
  const { t } = useTranslation();

  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.runtime.metrics.title")} kicker={t("admin.runtime.metrics.kicker")}>
        <JsonInfoList
          payload={runtime?.runtime || null}
          emptyTitle={t("admin.runtime.metrics.emptyTitle")}
          emptyBody={t("admin.runtime.metrics.emptyBody")}
        />
      </Panel>
      <Panel title={t("admin.runtime.cache.title")} kicker={t("admin.runtime.cache.kicker")}>
        <JsonInfoList
          payload={runtime?.cache || null}
          emptyTitle={t("admin.runtime.cache.emptyTitle")}
          emptyBody={t("admin.runtime.cache.emptyBody")}
        />
      </Panel>
      <Panel title={t("admin.runtime.rateLimit.title")} kicker={t("admin.runtime.rateLimit.kicker")}>
        <JsonInfoList
          payload={runtime?.rateLimit || null}
          emptyTitle={t("admin.runtime.rateLimit.emptyTitle")}
          emptyBody={t("admin.runtime.rateLimit.emptyBody")}
        />
      </Panel>
      <Panel title={t("admin.runtime.systemHealth.title")} kicker={t("admin.runtime.systemHealth.kicker")}>
        <JsonInfoList
          payload={runtime?.systemHealth || null}
          emptyTitle={t("admin.runtime.systemHealth.emptyTitle")}
          emptyBody={t("admin.runtime.systemHealth.emptyBody")}
        />
      </Panel>
    </div>
  );
}
