import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", () => ({
  m: new Proxy({}, {
    get: () => React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
      ({ children, ...props }, ref) => <div ref={ref} {...props}>{children}</div>,
    ),
  }),
  AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("./RealMap", () => ({
  RealMap: () => <div data-testid="real-map" />,
}));

vi.mock("./UpcomingEventsCard", () => ({
  UpcomingEventsCard: () => <div data-testid="upcoming-events" />,
}));

import { DashboardCommandCenter } from "./DashboardCommandCenter";

const viewModes = {
  kols: { label: "KOLs", desc: "KOL map", color: "#a855f7", available: true },
  dealers: { label: "Dealers", desc: "Dealer map", color: "#10b981", available: false },
  events: { label: "Events", desc: "Events map", color: "#fbbf24", available: false, isEvents: true },
};

const baseProps = {
  globeContainerRef: { current: null },
  isAvailable: false,
  pins: [],
  currentMode: null,
  venue: "",
  item: "",
  city: "",
  country: "",
  setPreviewEvent: vi.fn(),
  handleCountryChange: vi.fn(),
  handleCityChange: vi.fn(),
  handleItemChange: vi.fn(),
  setVenue: vi.fn(),
  setSelectedPin: vi.fn(),
  viewMode: null,
  setViewMode: vi.fn(),
  countryOptions: [],
  cityOptions: [],
  itemOptions: [],
  venueOptions: [],
  breadcrumb: [],
  goBack: vi.fn(),
  topListData: { title: "", items: [] },
  setSelectedEvent: vi.fn(),
  focusTarget: null,
  viewModes,
  showSettingsModal: false,
  upcomingEvents: [],
  onOpenEvents: vi.fn(),
};

describe("DashboardCommandCenter map source states", () => {
  it("renders a selected real map without the chooser", () => {
    render(<DashboardCommandCenter
      {...baseProps}
      viewMode="kols"
      currentMode={viewModes.kols}
      isAvailable={true}
    />);

    expect(screen.getByTestId("real-map")).toBeInTheDocument();
    expect(screen.getByText("KOLs")).toBeInTheDocument();
    expect(screen.queryByText("Choose what to map")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading map data")).not.toBeInTheDocument();
  });

  it("shows a loading state without exposing Viewing Select", () => {
    render(<DashboardCommandCenter {...baseProps} mapSelectionLoading={true} />);

    expect(screen.getByText("Loading map data")).toBeInTheDocument();
    expect(screen.queryByText("Viewing")).not.toBeInTheDocument();
    expect(screen.queryByText("Choose what to map")).not.toBeInTheDocument();
  });

  it("shows the chooser only after every source settles empty", () => {
    render(<DashboardCommandCenter
      {...baseProps}
      viewModes={{
        kols: { ...viewModes.kols, available: false },
        dealers: viewModes.dealers,
        events: viewModes.events,
      }}
    />);

    expect(screen.getByText("Choose what to map")).toBeInTheDocument();
    expect(screen.getByText("Viewing")).toBeInTheDocument();
    expect(screen.getByText(/当前没有可映射的真实位置/)).toBeInTheDocument();
    expect(screen.getAllByText(/ · EMPTY$/)).toHaveLength(3);
  });

  it("distinguishes a settled error from a genuinely empty map", () => {
    render(<DashboardCommandCenter
      {...baseProps}
      mapSelectionError="KOL failed | Dealer failed"
      viewModes={{
        kols: { ...viewModes.kols, available: false, error: "KOL failed" },
        dealers: { ...viewModes.dealers, error: "Dealer failed" },
        events: viewModes.events,
      }}
    />);

    expect(screen.getByText("Map data unavailable")).toBeInTheDocument();
    expect(screen.getByText(/地图源当前均不可用/)).toBeInTheDocument();
    expect(screen.getByText("KOLs · ERROR")).toBeInTheDocument();
    expect(screen.getByText("Dealers · ERROR")).toBeInTheDocument();
    expect(screen.queryByText("Loading map data")).not.toBeInTheDocument();
  });
});
