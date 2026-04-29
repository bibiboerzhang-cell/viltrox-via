import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useViaSession } from "../../api/queries/useVIASession";
import { apiClient } from "../../api/client";
import { useSSE } from "../../hooks/useSSE";
import { buildApiUrl } from "../../lib/api";
import type { SurfaceKey } from "../../lib/contracts.generated";
import { useViaStore } from "../../stores/useViaStore";
import { Button } from "../ui/button";
import { CatAvatar } from "./CatAvatar";
import { activityLabel, normalizeViaActivityState, resolveFallbackActivity, type ViaActivityState } from "./viaActivity";
import { CatBubble } from "./CatBubble";
import { buildViaPromptDeck, DEFAULT_VIA_SCENE, VIA_SCENE_PLAYBOOK, matchViaSceneFromMessage, matchViaSceneFromText } from "./viaScenePlaybook";

interface ViaEventEnvelope {
  payload?: {
    title?: string;
    text?: string;
    quick_actions?: string[];
    behavior_mode?: string;
    product_subintent?: string;
    business_subintent?: string;
    provider?: string;
    activity_state?: ViaActivityState;
  };
}

interface ViaRespondResult {
  reply_event_id?: string;
  reply?: {
    title?: string;
    text?: string;
    payload?: {
      quick_actions?: string[];
      behavior_mode?: string;
      product_subintent?: string;
      business_subintent?: string;
      provider?: string;
      activity_state?: ViaActivityState;
    };
  };
}

export function CatConversation({
  surface = "upload",
  variant = "card",
  onClose,
}: {
  surface?: SurfaceKey;
  variant?: "card" | "workspace" | "sidebar" | "popup";
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [idleFrame, setIdleFrame] = useState(0);
  const [isAwake, setIsAwake] = useState(false);
  const [inlineError, setInlineError] = useState("");
  const threadRef = useRef<HTMLDivElement | null>(null);
  const { sessionKey, setSessionKey, messages, appendMessage, resetMessages } = useViaStore();
  const createSession = useViaSession(surface);
  const replyMutation = useMutation({
    mutationFn: ({ text, readyKey }: { text: string; readyKey: string }) =>
      apiClient<ViaRespondResult>(`/api/via/sessions/${readyKey}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, surface }),
      }),
    onSuccess: (result) => {
      setInlineError("");
      if (!result.reply?.text) {
        return;
      }
      appendMessage({
        id: result.reply_event_id || `via-http-${Date.now()}`,
        role: "via",
        title: result.reply.title || t("catographer.companion.conversation.viaTitle"),
        text: result.reply.text,
        quickActions: result.reply.payload?.quick_actions || [],
        behaviorMode: result.reply.payload?.behavior_mode || "",
        sceneHint: result.reply.payload?.product_subintent || result.reply.payload?.business_subintent || "",
        provider: result.reply.payload?.provider || "",
        activityState: result.reply.payload?.activity_state || null,
      });
    },
  });

  async function ensureSession() {
    if (sessionKey) {
      return sessionKey;
    }
    const bundle = await createSession.mutateAsync();
    const nextKey = bundle.session?.session_key || "";
    if (!nextKey) {
      throw new Error("Via session did not return a key");
    }
    setSessionKey(nextKey);
    resetMessages();
    setIsAwake(true);
    return nextKey;
  }

  const streamUrl = useMemo(
    () => (isAwake && sessionKey ? buildApiUrl(`/api/via/sessions/${sessionKey}/stream?after_id=0-0`) : null),
    [isAwake, sessionKey],
  );

  const streamHandlers = useMemo(
    () => ({
      session_ready: () => {
        setInlineError("");
      },
      via_reply: (event: ViaEventEnvelope) => {
        if (event.payload?.text) {
          appendMessage({
            id: `via-${Date.now()}`,
            role: "via",
            title: event.payload.title || t("catographer.companion.conversation.viaTitle"),
            text: event.payload.text,
            quickActions: event.payload.quick_actions || [],
            behaviorMode: event.payload.behavior_mode || "",
            sceneHint: event.payload.product_subintent || event.payload.business_subintent || "",
            provider: event.payload.provider || "",
            activityState: event.payload.activity_state || null,
          });
        }
      },
    }),
    [appendMessage, t],
  );

  useSSE<ViaEventEnvelope>(streamUrl, streamHandlers);

  useEffect(() => {
    if (sessionKey) {
      setIsAwake(true);
    }
  }, [sessionKey]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setIdleFrame((value) => (value + 1) % VIA_SCENE_PLAYBOOK.length);
    }, 5200);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!threadRef.current) {
      return;
    }
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages]);

  function handleSend(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) {
      return;
    }
    const send = async () => {
      const readyKey = await ensureSession();
      if (!readyKey) {
        return;
      }
      setInlineError("");
      appendMessage({ id: `user-${Date.now()}`, role: "user", text });
      setDraft("");
      replyMutation.mutate({ text, readyKey });
    };
    void send().catch((error) => {
      setInlineError(error instanceof Error ? error.message : t("catographer.companion.conversation.error"));
    });
  }

  const conversation = messages.slice(-8);
  const waking = createSession.isPending && !sessionKey;
  const latestViaMessage = [...conversation].reverse().find((item) => item.role === "via");
  const activeScene = useMemo(
    () =>
      matchViaSceneFromMessage(latestViaMessage) ??
      matchViaSceneFromText(draft) ??
      VIA_SCENE_PLAYBOOK[idleFrame % VIA_SCENE_PLAYBOOK.length] ??
      DEFAULT_VIA_SCENE,
    [draft, idleFrame, latestViaMessage],
  );
  const activeActivity = useMemo(
    () =>
      normalizeViaActivityState(latestViaMessage?.activityState) ??
      resolveFallbackActivity(
        [latestViaMessage?.title, latestViaMessage?.text, latestViaMessage?.sceneHint, draft, activeScene.id].filter(Boolean).join(" "),
      ),
    [activeScene.id, draft, latestViaMessage],
  );
  const promptDeck = useMemo(
    () => buildViaPromptDeck(activeScene, latestViaMessage?.quickActions || []),
    [activeScene, latestViaMessage?.quickActions],
  );
  const idleAction = waking ? "idle" : replyMutation.isPending ? "talking" : draft.trim() ? "thinking" : activeScene.action;
  const fallbackMessage = isAwake
    ? {
        id: "via-ready",
        role: "via" as const,
        title: t("catographer.companion.conversation.readyTitle"),
        text: activeActivity?.scene_line || t("catographer.companion.conversation.readyBody"),
      }
    : {
        id: "via-sleep",
        role: "via" as const,
        title: t("catographer.companion.conversation.sleepTitle"),
        text: t("catographer.companion.conversation.sleepBody"),
      };

  useEffect(() => {
    if (createSession.error) {
      setInlineError(createSession.error instanceof Error ? createSession.error.message : t("catographer.companion.conversation.error"));
    }
  }, [createSession.error, t]);

  useEffect(() => {
    if (replyMutation.error) {
      setInlineError(replyMutation.error instanceof Error ? replyMutation.error.message : t("catographer.companion.conversation.error"));
    }
  }, [replyMutation.error, t]);

  useEffect(() => {
    if (variant !== "popup" || isAwake || createSession.isPending || sessionKey) {
      return;
    }
    void ensureSession().catch((error) => {
      setInlineError(error instanceof Error ? error.message : t("catographer.companion.conversation.error"));
    });
  }, [createSession.isPending, isAwake, sessionKey, t, variant]);

  if (variant === "workspace") {
    const workspaceMessages = conversation.length ? conversation : [fallbackMessage];
    return (
      <section
        id="via-conversation-panel"
        data-via-conversation
        className="cat-conversation-card cat-conversation-card--workspace"
      >
        <div className="cat-conversation-card__workspace-header">
          <div className="cat-conversation-card__workspace-brand">
            <h3>{t("catographer.companion.conversation.viaTitle")}</h3>
            <p>{t("catographer.companion.conversation.eyebrow")}</p>
          </div>
          <div className="cat-conversation-card__workspace-badge">PAGE PATROL</div>
        </div>

        <div ref={threadRef} className="cat-conversation-card__workspace-thread">
          <div className="cat-conversation-card__workspace-stack">
            {workspaceMessages.map((item) => (
              <div
                key={item.id}
                className={`cat-conversation-card__workspace-row${item.role === "user" ? " is-user" : ""}`}
              >
                {item.role === "user" ? (
                  <div className="cat-conversation-card__workspace-userbubble">
                    {item.text}
                  </div>
                ) : (
                  <div className="cat-conversation-card__workspace-viabubble">
                    <small>
                      {item.title || t("catographer.companion.conversation.readyTitle")}
                    </small>
                    <p>{item.text}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <form className="cat-conversation-card__workspace-form" onSubmit={handleSend}>
          <input
            className="cat-conversation-card__workspace-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onFocus={() => {
              if (!isAwake && !createSession.isPending) {
                void ensureSession();
              }
            }}
            placeholder={t("catographer.companion.conversation.placeholder")}
          />
          <button
            className="cat-conversation-card__workspace-send"
            type="submit"
            disabled={replyMutation.isPending || createSession.isPending}
          >
            {createSession.isPending
              ? t("catographer.companion.conversation.wake")
              : replyMutation.isPending
                ? t("catographer.companion.conversation.sending")
                : t("catographer.companion.conversation.send")}
          </button>
        </form>
        {inlineError ? <p className="cat-conversation-card__workspace-error">{inlineError}</p> : null}

        <div className="cat-conversation-card__workspace-deck">
          {promptDeck.slice(0, 3).map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="cat-conversation-card__workspace-prompt"
              onClick={() => setDraft(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
      </section>
    );
  }

  if (variant === "sidebar") {
    return (
      <section className="cat-conversation-card cat-conversation-card--sidebar">
        <form className="cat-conversation-card__sidebar-form" onSubmit={handleSend}>
          <input
            className="cat-conversation-card__sidebar-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onFocus={() => {
              if (!isAwake && !createSession.isPending) {
                void ensureSession();
              }
            }}
            placeholder={t("catographer.companion.conversation.placeholder")}
          />
          <Button type="submit" disabled={replyMutation.isPending || createSession.isPending}>
            {createSession.isPending
              ? t("catographer.companion.conversation.wake")
              : replyMutation.isPending
                ? t("catographer.companion.conversation.sending")
                : t("catographer.companion.conversation.send")}
          </Button>
        </form>
      </section>
    );
  }

  if (variant === "popup") {
    return (
      <section id="via-conversation-panel" data-via-conversation className="cat-conversation-card cat-conversation-card--popup">
        <div ref={threadRef} className="cat-conversation-card__popup-thread">
          {(conversation.length ? conversation : [fallbackMessage]).map((item) => (
            <div
              key={item.id}
              className={`cat-conversation-card__popup-row${item.role === "user" ? " is-user" : ""}`}
            >
              <div className="cat-conversation-card__popup-bubble">
                {item.text}
              </div>
            </div>
          ))}
        </div>
        {inlineError ? <p className="cat-conversation-card__popup-error">{inlineError}</p> : null}

        <form className="cat-conversation-card__popup-form" onSubmit={handleSend}>
          <input
            className="cat-conversation-card__popup-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onFocus={() => {
              if (!isAwake && !createSession.isPending) {
                void ensureSession();
              }
            }}
            placeholder={t("catographer.companion.conversation.placeholder")}
          />
          <button
            className="cat-conversation-card__popup-send"
            type="submit"
            disabled={replyMutation.isPending || createSession.isPending}
            aria-label={t("catographer.companion.conversation.send")}
          >
            {createSession.isPending
              ? t("catographer.companion.conversation.wake")
              : replyMutation.isPending
                ? t("catographer.companion.conversation.sending")
                : "↗"}
          </button>
        </form>
        {onClose ? (
          <button type="button" className="cat-conversation-card__popup-close-sr" onClick={onClose}>
            {t("catographer.companion.panel.close")}
          </button>
        ) : null}
      </section>
    );
  }

  return (
    <section
      id="via-conversation-panel"
      data-via-conversation
      className="cat-conversation-card rounded-[32px] border border-white/70 bg-[rgba(255,250,245,0.9)] p-6 shadow-[0_28px_60px_rgba(15,23,42,0.08)] backdrop-blur-xl"
    >
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[16px] font-bold text-slate-950">{t("catographer.companion.conversation.viaTitle")}</h3>
          <p className="mt-1 text-[11px] font-extrabold uppercase tracking-[0.18em] text-orange-500">
            {t("catographer.companion.conversation.eyebrow")}
          </p>
        </div>
        <div className="rounded-full border border-orange-200 bg-orange-50 px-4 py-2 text-xs font-bold uppercase tracking-[0.14em] text-slate-500">
          {activityLabel(activeActivity)}
        </div>
      </div>

      <div className="mb-5 flex items-start gap-5">
        <CatAvatar
          speaking={replyMutation.isPending}
          action={idleAction}
          activityState={activeActivity}
        />
        <div className="min-w-0 flex-1">
          <div
            ref={threadRef}
            className="cat-bubble-scroll pr-2"
          >
            <div className="grid gap-3">
              {(conversation.length ? conversation : [fallbackMessage]).map((item) => (
                <div
                  key={item.id}
                  className={item.role === "via" ? "pr-8" : "pl-8"}
                >
                  <CatBubble
                    title={
                      item.title ||
                      (item.role === "via"
                        ? t("catographer.companion.conversation.readyTitle")
                        : t("catographer.companion.conversation.userTitle"))
                    }
                    text={item.text}
                    scrollable={false}
                    className={item.role === "user" ? "border-slate-200 bg-slate-50 text-slate-700" : ""}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <form className="flex items-center gap-3" onSubmit={handleSend}>
        <input
          className="min-w-0 flex-1 rounded-full border border-slate-200 bg-white px-5 py-4 text-[15px] text-slate-700 outline-none transition focus:border-orange-300 focus:ring-4 focus:ring-orange-100"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={() => {
            if (!isAwake && !createSession.isPending) {
              void ensureSession();
            }
          }}
          placeholder={t("catographer.companion.conversation.placeholder")}
        />
        <Button type="submit" disabled={replyMutation.isPending || createSession.isPending}>
          {createSession.isPending
            ? t("catographer.companion.conversation.wake")
            : replyMutation.isPending
              ? t("catographer.companion.conversation.sending")
              : t("catographer.companion.conversation.send")}
        </Button>
      </form>
      {inlineError ? <p className="cat-conversation-card__workspace-error">{inlineError}</p> : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {promptDeck.slice(0, 4).map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="rounded-full border border-orange-200 bg-white/90 px-3 py-2 text-[12px] font-bold text-slate-600 transition hover:-translate-y-[1px] hover:border-orange-300 hover:bg-orange-50"
            onClick={() => setDraft(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </section>
  );
}
