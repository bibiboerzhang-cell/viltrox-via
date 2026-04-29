import { AnimatePresence, motion } from "framer-motion";
import { lazy, Suspense, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

import type { SurfaceKey } from "../../lib/contracts.generated";
import { buildViaProgressCopy, isUploadProgressRecent } from "../../lib/submissionProgress";
import { useViaStore } from "../../stores/useViaStore";

const LazyCatConversation = lazy(async () => {
  const module = await import("./CatConversation");
  return { default: module.CatConversation };
});

function resolveSurface(pathname: string): SurfaceKey {
  if (pathname.startsWith("/admin")) return "admin";
  if (pathname.startsWith("/account")) return "account";
  if (pathname.startsWith("/redeem")) return "redeem";
  if (pathname.startsWith("/student-signup")) return "student";
  return "upload";
}

export function FloatingViaCat() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const { isPanelOpen, setPanelOpen, messages, progressSnapshot } = useViaStore();
  const surface = useMemo(() => resolveSurface(pathname), [pathname]);
  const latestViaMessage = [...messages].reverse().find((item) => item.role === "via");
  const progressBubble = progressSnapshot && (progressSnapshot.isActive || isUploadProgressRecent(progressSnapshot))
    ? buildViaProgressCopy(progressSnapshot, t)
    : "";
  const launcherBubble = progressBubble || latestViaMessage?.text || "";

  return (
    <>
      {!isPanelOpen ? (
        <motion.div
          className="via-sticker hidden md:flex"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.22, ease: "easeOut" }}
        >
          {launcherBubble ? <div className="via-sticker__bubble">{launcherBubble}</div> : null}
          <button
            type="button"
            className="via-sticker__button"
            onClick={() => setPanelOpen(true)}
            onDoubleClick={() => setPanelOpen(true)}
            aria-label={t("catographer.companion.launcher.open")}
          >
            <img src="/cat/via-front.png" alt="Via" className="via-sticker__image" />
          </button>
          <small>{t("catographer.companion.launcher.clickToChat")}</small>
        </motion.div>
      ) : null}

      {!isPanelOpen ? (
        <div className="via-mobile-launcher md:hidden">
          <button type="button" className="via-mobile-launcher__button" onClick={() => setPanelOpen(true)}>
            <img src="/cat/via-front.png" alt="Via" className="via-mobile-launcher__image" />
            <span className="via-mobile-launcher__copy">
              <strong>{t("catographer.companion.conversation.viaTitle")}</strong>
              <small>{launcherBubble || t(`catographer.companion.dock.surfaces.${surface}`)}</small>
            </span>
          </button>
        </div>
      ) : null}

      <AnimatePresence>
        {isPanelOpen ? (
          <motion.aside
            className="via-chat-popover"
            role="dialog"
            aria-modal="false"
            aria-label={t("catographer.companion.panel.title")}
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.96 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <div className="via-chat-popover__header">
              <div className="via-chat-popover__brand">
                <img src="/cat/via-front.png" alt="Via" className="via-chat-popover__avatar" />
                <div>
                  <strong>{t("catographer.companion.conversation.viaTitle")}</strong>
                  <small>{t("catographer.companion.conversation.eyebrow")}</small>
                </div>
              </div>
              <button type="button" className="via-chat-popover__close" onClick={() => setPanelOpen(false)}>
                ×
              </button>
            </div>
            <Suspense fallback={<div className="via-chat-popover__loading">{t("catographer.companion.panel.loading")}</div>}>
              <LazyCatConversation surface={surface} variant="popup" onClose={() => setPanelOpen(false)} />
            </Suspense>
          </motion.aside>
        ) : null}
      </AnimatePresence>
    </>
  );
}
