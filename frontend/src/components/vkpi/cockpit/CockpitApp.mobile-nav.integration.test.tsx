import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  LocaleProvider,
  LOCALE_STORAGE_KEY,
} from "../../../app/providers/LocaleProvider";

const serviceMocks = vi.hoisted(() => ({
  getDealerLocations: vi.fn(async () => ({ pins: [] })),
  listUpcomingEvents: vi.fn(async () => ({ items: [] })),
  listStaffGroups: vi.fn(async () => ({ items: [] })),
}));
const storageMocks = vi.hoisted(() => ({
  state: {} as Record<string, unknown>,
  saveStoredState: vi.fn(),
}));

vi.mock("framer-motion", async () => {
  const ReactModule = await import("react");
  return {
    LazyMotion: ({ children }: { children: React.ReactNode }) => ReactModule.createElement(ReactModule.Fragment, null, children),
    domMax: {},
    m: {
      div: ({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) => ReactModule.createElement("div", props, children),
    },
  };
});

vi.mock("../../../app/providers/ThemeProvider", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn() }),
}));

vi.mock("../../../hooks/usePermissions", () => ({
  usePermissions: () => ({
    canViewBoard: () => true,
    isOwner: () => true,
  }),
}));

vi.mock("../../../lib/browserAssist/enable", () => ({
  isBrowserAssistEnabled: () => false,
  useBrowserAssist: () => undefined,
}));

vi.mock("../../../services/vkpi/dealers-api", () => ({
  getDealerLocations: serviceMocks.getDealerLocations,
}));

vi.mock("../../../services/vkpi/events-api", () => ({
  listUpcomingEvents: serviceMocks.listUpcomingEvents,
}));

vi.mock("../../../services/vkpi/groups-api", () => ({
  listStaffGroups: serviceMocks.listStaffGroups,
  toUiGroup: (value: unknown) => value,
}));

vi.mock("./CockpitApp.shellHooks", () => ({
  useCockpitNavigationEvents: () => undefined,
  useCockpitPresenceHeartbeat: () => undefined,
  useCockpitVersionBadge: () => null,
}));

vi.mock("./useCockpitRuntime", () => ({
  useCockpitRuntime: () => ({
    currentUser: { id: 1, name: "Admin", role: "owner" },
    runtimeNotifications: [],
    setRuntimeNotifications: vi.fn(),
    runtimeReminders: [],
    kolPoolRows: [],
    kolPoolLoading: false,
    kolPoolError: "",
    dashboardRuntime: { mapHierarchy: {}, metrics: [] },
    dashboardLoading: false,
    dashboardError: "",
  }),
}));

vi.mock("./CockpitApp.Sections", () => ({
  CockpitOverlays: () => [],
}));

vi.mock("./CockpitSidebar", () => ({
  CockpitSidebar: () => null,
}));

vi.mock("./CockpitTopbar", () => ({
  CockpitTopbar: ({ activeNav }: { activeNav: string }) => React.createElement("header", { "data-testid": "active-nav" }, activeNav),
}));

vi.mock("./DashboardReplicaPage", () => ({
  DashboardReplicaPage: () => React.createElement("section", { "data-testid": "dashboard-board" }, "Dashboard content"),
}));

vi.mock("./CockpitApp.lazyBoards", async () => {
  const ReactModule = await import("react");
  const board = (testId: string, text: string) => () => ReactModule.createElement("section", { "data-testid": testId }, text);
  return {
    COCKPIT_BOARDS: [
      "dashboard", "kol-pool", "my-kol", "projects", "events", "shopify", "dealers",
      "triage", "dataQuery", "marketTrends", "skillStudio", "intelligent", "replyQueue",
      "sku360", "kolProfile", "launchpad", "autonomy", "marketVoice", "creativeLibrary",
      "strategyBoard", "gtmCommand",
    ],
    KOLPoolPage: board("kol-pool-board", "KOL Pool content"),
    ShopifyBoardPage: board("shopify-board", "Shopify content"),
    DealerMapPage: board("dealers-board", "Dealers content"),
    MyKolBoardPage: board("my-kol-board", "MY KOL content"),
    LegacyProjectsPage: board("projects-board", "Projects content"),
    EventsMockupPage: board("events-board", "活动雷达"),
    DataQualityPage: board("triage-board", "Triage content"),
    DataQueryPage: board("data-query-board", "Data query content"),
    MarketTrendsPage: board("market-trends-board", "Market trends content"),
    SkillStudioPage: board("skill-studio-board", "Skill Studio content"),
    IntelligentPage: board("intelligent-board", "Intelligent content"),
    ReplyQueuePage: board("reply-queue-board", "Reply Queue content"),
    Sku360Page: board("sku360-board", "SKU 360 content"),
    KolProfilePage: board("kol-profile-board", "KOL Profile content"),
    LaunchPadPage: board("launchpad-board", "Launchpad content"),
    AutonomyBoardPage: board("autonomy-board", "Autonomy content"),
    MarketVoicePage: board("market-voice-board", "Market Voice content"),
    CreativeLibraryPage: board("creative-library-board", "Creative Library content"),
    StrategyBoardPage: board("strategy-board", "Strategy content"),
    GtmCommandPage: board("gtm-command-board", "GTM content"),
  };
});

vi.mock("./CockpitApp.helpers", () => ({
  buildMappedEvents: () => [],
  filterUpcomingEvents: () => [],
  buildUpcomingEvents: () => [],
  buildEventPins: () => [],
  buildPins: () => [],
  buildFocusTarget: () => null,
  buildTopListData: () => [],
  buildCountryOptions: () => [],
  buildCityOptions: () => [],
  displayCityLabel: (value: string) => value,
  buildItemOptions: () => [],
  buildVenueOptions: () => [],
}));

vi.mock("./normalizers", () => ({
  normalizeEventsHierarchy: () => null,
  normalizeDealersHierarchy: () => null,
}));

vi.mock("./mapViewSelection", () => ({
  resolveDashboardMapSelection: () => ({ mode: null, pending: false }),
}));

vi.mock("./lib/storage", () => ({
  loadStoredState: () => storageMocks.state,
  saveStoredState: storageMocks.saveStoredState,
}));

vi.mock("./lib/kpiScopeStorage", () => ({
  loadKpiScopeForStaff: () => null,
  saveKpiScopeForStaff: vi.fn(),
}));

vi.mock("../../../services/vkpi/staffAdapter", () => ({
  toUiStaffList: () => [],
}));

import { CockpitApp } from "./CockpitApp";

describe("CockpitApp mobile navigation integration", () => {
  const renderCockpit = (props: Record<string, unknown> = {}) => render(
    <LocaleProvider>
      <CockpitApp {...props} />
    </LocaleProvider>,
  );

  beforeEach(() => {
    serviceMocks.getDealerLocations.mockClear();
    serviceMocks.listUpcomingEvents.mockClear();
    serviceMocks.listStaffGroups.mockClear();
    storageMocks.state = {};
    storageMocks.saveStoredState.mockReset();
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    window.scrollTo = vi.fn();
  });

  it("does not let the Cockpit shell duplicate the Dealers page locations request", async () => {
    window.history.replaceState({}, "", "/?cockpit=dealers");
    renderCockpit({ apiToken: "token" });

    expect(await screen.findByTestId("dealers-board")).toHaveTextContent("Dealers content");
    await waitFor(() => expect(serviceMocks.listUpcomingEvents).toHaveBeenCalledTimes(1));
    expect(serviceMocks.getDealerLocations).not.toHaveBeenCalled();
  });

  it("starts only one Dashboard locations read during the production StrictMode effect replay", async () => {
    render(
      <LocaleProvider>
        <React.StrictMode>
          <CockpitApp apiToken="token" />
        </React.StrictMode>
      </LocaleProvider>,
    );

    await waitFor(() => expect(serviceMocks.getDealerLocations).toHaveBeenCalledTimes(1));
  });

  it("aborts the shell map request when navigation hands locations ownership to Dealers", async () => {
    renderCockpit({ apiToken: "token" });
    await waitFor(() => expect(serviceMocks.getDealerLocations).toHaveBeenCalledTimes(1));
    const signal = serviceMocks.getDealerLocations.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    const burger = screen.getByRole("button", { name: "打开导航菜单" });
    fireEvent.click(burger);
    fireEvent.click(screen.getByRole("button", { name: "经销商" }));

    expect(await screen.findByTestId("dealers-board")).toHaveTextContent("Dealers content");
    await waitFor(() => expect(signal.aborted).toBe(true));
    expect(serviceMocks.getDealerLocations).toHaveBeenCalledTimes(1);
  });

  it("uses CockpitApp state to replace Dashboard with Events and then navigate back", async () => {
    renderCockpit();

    expect(screen.getByTestId("dashboard-board")).toBeTruthy();
    expect(screen.getByTestId("active-nav")).toHaveTextContent("dashboard");

    const burger = screen.getByRole("button", { name: "打开导航菜单" });
    fireEvent.click(burger);
    expect(burger).toHaveAttribute("aria-expanded", "true");
    const drawer = screen.getByRole("dialog", { name: "主导航" });
    const overlay = drawer.parentElement;
    expect(drawer).toHaveStyle({ transform: "translateX(0)" });
    expect(overlay).toHaveAttribute("aria-hidden", "false");
    expect(overlay).not.toHaveClass("pointer-events-none");

    fireEvent.click(screen.getByRole("button", { name: "活动" }));

    await waitFor(() => {
      expect(screen.getByTestId("events-board")).toHaveTextContent("活动雷达");
      expect(screen.queryByTestId("dashboard-board")).toBeNull();
      expect(screen.getByTestId("active-nav")).toHaveTextContent("events");
      expect(burger).toHaveAttribute("aria-expanded", "false");
      expect(screen.getByRole("dialog", { name: "主导航", hidden: true })).toHaveStyle({ transform: "translateX(-100%)" });
      expect(overlay).toHaveAttribute("aria-hidden", "true");
      expect(overlay).toHaveClass("pointer-events-none");
    });

    fireEvent.click(burger);
    fireEvent.click(screen.getByRole("button", { name: "仪表盘" }));

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-board")).toHaveTextContent("Dashboard content");
      expect(screen.queryByTestId("events-board")).toBeNull();
      expect(screen.getByTestId("active-nav")).toHaveTextContent("dashboard");
    });
  });

  it("restores and persists the English language preference", async () => {
    window.localStorage.setItem("vkpi-dashboard-state-v1", JSON.stringify({ lang: "en" }));
    renderCockpit();

    expect(await screen.findByRole("button", { name: "Open navigation menu" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Primary navigation", hidden: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Events", hidden: true })).toBeInTheDocument();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    await waitFor(() => expect(storageMocks.saveStoredState).toHaveBeenCalledWith(
      expect.objectContaining({ lang: "en" }),
    ));
  });
});
