import React from "react";
import EventsPage from "./pages/EventsPage.js";

type EventsMockupPageProps = {
  userName?: string;
};

export function EventsMockupPage({ userName }: EventsMockupPageProps) {
  const currentUser = React.useMemo(() => ({
    id: "j",
    name: userName || "Jia",
    initial: (userName || "J").slice(0, 1).toUpperCase(),
    color: "#a855f7",
  }), [userName]);

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#0a0a0d] text-white">
      <EventsPage currentUser={currentUser} />
    </div>
  );
}
