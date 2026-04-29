import { useEffect, useRef } from "react";

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];

function parseEventPayload<TPayload>(event: Event): TPayload {
  const message = event as MessageEvent;
  try {
    return JSON.parse(message.data) as TPayload;
  } catch {
    return (message as unknown as { data: TPayload }).data;
  }
}

function withAfterId(url: string, afterId: string): string {
  if (!afterId) {
    return url;
  }
  try {
    const next = new URL(url, typeof window !== "undefined" ? window.location.origin : "http://localhost");
    next.searchParams.set("after_id", afterId);
    return next.toString();
  } catch {
    return url;
  }
}

export function useSSE<TPayload = unknown>(
  url: string | null,
  handlers: Record<string, (payload: TPayload) => void>,
) {
  const sourceRef = useRef<EventSource | null>(null);
  const handlersRef = useRef(handlers);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const closedManuallyRef = useRef(false);
  const lastEventIdRef = useRef("");

  useEffect(() => {
    handlersRef.current = handlers;
  }, [handlers]);

  useEffect(() => {
    closedManuallyRef.current = false;
    reconnectAttemptRef.current = 0;
    lastEventIdRef.current = "";

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const closeSource = () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };

    const scheduleReconnect = () => {
      if (closedManuallyRef.current || !url || reconnectTimerRef.current !== null) {
        return;
      }
      const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        reconnectAttemptRef.current += 1;
        openSource();
      }, delay);
    };

    const openSource = () => {
      if (!url || closedManuallyRef.current) {
        return;
      }
      closeSource();
      const source = new EventSource(withAfterId(url, lastEventIdRef.current));
      sourceRef.current = source;
      source.onopen = () => {
        reconnectAttemptRef.current = 0;
      };
      source.onerror = () => {
        if (closedManuallyRef.current) {
          return;
        }
        closeSource();
        scheduleReconnect();
      };
      Object.keys(handlersRef.current).forEach((eventName) => {
        source.addEventListener(eventName, (event) => {
          const typedEvent = event as MessageEvent;
          const nextId = String(
            typedEvent.lastEventId ||
              (typeof typedEvent.data === "string" ? (() => {
                try {
                  return JSON.parse(typedEvent.data)?.id || "";
                } catch {
                  return "";
                }
              })() : ""),
          ).trim();
          if (nextId) {
            lastEventIdRef.current = nextId;
          }
          const handler = handlersRef.current[eventName];
          if (!handler) {
            return;
          }
          handler(parseEventPayload<TPayload>(event));
        });
      });
    };

    clearReconnectTimer();
    closeSource();
    if (url) {
      openSource();
    }

    return () => {
      closedManuallyRef.current = true;
      clearReconnectTimer();
      closeSource();
    };
  }, [url]);

  return {
    close: () => {
      closedManuallyRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      sourceRef.current?.close();
      sourceRef.current = null;
    },
  };
}
