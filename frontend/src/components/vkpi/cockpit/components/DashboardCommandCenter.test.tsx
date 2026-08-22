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
import { I18nContext, makeT } from "../lib/i18n";
import { I18N_EN } from "../data/i18nEn";

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
  it("renders a selected real map without the chooser", async () => {
    render(<DashboardCommandCenter
      {...baseProps}
      viewMode="kols"
      currentMode={viewModes.kols}
      isAvailable={true}
    />);

    expect(await screen.findByTestId("real-map")).toBeInTheDocument();
    expect(screen.getByText("KOL")).toBeInTheDocument();
    expect(screen.queryByText("选择地图内容")).not.toBeInTheDocument();
    expect(screen.queryByText("正在加载地图数据")).not.toBeInTheDocument();
  });

  it("shows a loading state without exposing Viewing Select", () => {
    render(<DashboardCommandCenter {...baseProps} mapSelectionLoading={true} />);

    expect(screen.getByText("正在加载地图数据")).toBeInTheDocument();
    expect(screen.queryByText("查看维度")).not.toBeInTheDocument();
    expect(screen.queryByText("选择地图内容")).not.toBeInTheDocument();
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

    expect(screen.getByText("选择地图内容")).toBeInTheDocument();
    expect(screen.getByText("查看维度")).toBeInTheDocument();
    expect(screen.getByText(/当前没有可映射的真实位置/)).toBeInTheDocument();
    expect(screen.getAllByText(/ · 暂无数据$/)).toHaveLength(3);
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

    expect(screen.getByText("地图数据不可用")).toBeInTheDocument();
    expect(screen.getByText(/地图源当前均不可用/)).toBeInTheDocument();
    expect(screen.getByText("KOL · 异常")).toBeInTheDocument();
    expect(screen.getByText("经销商 · 异常")).toBeInTheDocument();
    expect(screen.queryByText("正在加载地图数据")).not.toBeInTheDocument();
  });

  it("keeps the command center in English when lang=en", () => {
    render(
      <I18nContext.Provider value={{ t: makeT("en", I18N_EN), lang: "en", setLang: vi.fn() }}>
        <DashboardCommandCenter
          {...baseProps}
          viewModes={{
            kols: { ...viewModes.kols, available: false },
            dealers: viewModes.dealers,
            events: viewModes.events,
          }}
        />
      </I18nContext.Provider>,
    );

    expect(screen.getByText("Marketing Command Center")).toBeInTheDocument();
    expect(screen.getByText("Choose what to map")).toBeInTheDocument();
    expect(screen.getByText("Viewing")).toBeInTheDocument();
    expect(screen.getByText("KOLs · EMPTY")).toBeInTheDocument();
  });
});
