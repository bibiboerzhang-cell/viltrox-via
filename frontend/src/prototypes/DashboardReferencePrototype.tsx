import React, { useLayoutEffect } from "react";
import { MockupDashboard } from "../components/vkpi/cockpit/MockupDashboard";
import "./dashboard-reference-prototype.css";

export default function DashboardReferencePrototype() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const previousStyle = root.getAttribute("data-style");
    const previousTheme = root.getAttribute("data-theme");

    root.setAttribute("data-style", "glass");
    root.setAttribute("data-theme", "dark");

    return () => {
      if (previousStyle) root.setAttribute("data-style", previousStyle);
      else root.removeAttribute("data-style");

      if (previousTheme) root.setAttribute("data-theme", previousTheme);
      else root.removeAttribute("data-theme");
    };
  }, []);

  return (
    <div className="dashboard-reference-prototype">
      <MockupDashboard />
    </div>
  );
}
