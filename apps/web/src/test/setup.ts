import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }) });
Object.assign(navigator, { clipboard: { writeText: async () => undefined } });
Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { writable: true, value: () => undefined });

class ResizeObserverStub {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
}
Object.assign(window, { ResizeObserver: ResizeObserverStub });
Object.assign(globalThis, { ResizeObserver: ResizeObserverStub });
