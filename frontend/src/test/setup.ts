// W5 测试全局 setup:jest-dom 断言 + 常用浏览器 API stub(jsdom 不带的)。
import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// 一些组件用到的浏览器 API,在 jsdom 里补 stub(避免渲染崩)。
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

if (!(window as unknown as { ResizeObserver?: unknown }).ResizeObserver) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

if (!(window as unknown as { IntersectionObserver?: unknown }).IntersectionObserver) {
  (window as unknown as { IntersectionObserver: unknown }).IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  };
}
