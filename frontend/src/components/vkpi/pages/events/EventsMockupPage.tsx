import React from "react";
import EventsPage from "./pages/EventsPage.js";

type UiStaff = {
  id: string;
  name: string;
  email?: string;
  isAdmin?: boolean;
  avatar?: string;
  color?: string;
  title?: string;
};

type EventsMockupPageProps = {
  userName?: string;
  staff?: UiStaff[];
  currentUser?: { id?: string | number; name?: string; avatar?: string; email?: string };
  initialEventId?: string | null;
  onConsumeInitialEvent?: () => void;
};

export function EventsMockupPage({ userName, staff = [], currentUser: loggedInUser, initialEventId = null, onConsumeInitialEvent }: EventsMockupPageProps) {
  // Derive the events currentUser from the real logged-in user, matched against the
  // real staff list so its id lines up with event teamUserIds/ownerId (real staff ids).
  const currentUser = React.useMemo(() => {
    const meId = loggedInUser?.id != null ? String(loggedInUser.id) : "";
    const me =
      staff.find((s) => String(s.id) === meId) ||
      (loggedInUser?.email ? staff.find((s) => s.email === loggedInUser.email) : null) ||
      staff[0] ||
      null;
    const name = me?.name || loggedInUser?.name || userName || "Jia";
    return {
      id: me ? String(me.id) : (meId || "j"),
      name,
      initial: (me?.avatar || name || "J").slice(0, 1).toUpperCase(),
      color: me?.color || "#a855f7",
    };
  }, [loggedInUser, staff, userName]);

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#0a0a0d] text-white">
      <EventsPage
        currentUser={currentUser}
        staff={staff}
        initialEventId={initialEventId}
        onConsumeInitialEvent={onConsumeInitialEvent}
      />
    </div>
  );
}
