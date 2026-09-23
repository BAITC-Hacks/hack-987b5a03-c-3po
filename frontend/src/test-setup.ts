import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Some Node/Vitest builds expose a global localStorage placeholder and prevent
// jsdom from installing its own Storage object.
if (!window.localStorage) {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        values.set(key, value);
      },
      removeItem: (key: string) => {
        values.delete(key);
      },
      clear: () => {
        values.clear();
      },
    },
  });
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  window.localStorage?.clear();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
