import React from "react";
import { CockpitApp } from "../components/vkpi/cockpit/CockpitApp";
import { emptyDashboardData } from "../components/vkpi/data/emptyDashboardData";

export default function RealCockpitPrototype() {
  return (
    <CockpitApp
      apiToken=""
      userName="Local QA"
      userRole="admin"
      userAuthRole="admin"
      data={emptyDashboardData}
      viewMode="manager"
    />
  );
}
