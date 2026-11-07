import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// Deterministic IDs for react-router focus management etc. when needed.
if (!crypto.randomUUID) {
  Object.defineProperty(crypto, "randomUUID", {
    value: () => "00000000-0000-4000-8000-000000000000",
  });
}

// Silence expected unhandled rejection noise in tests (e.g. aborted streams).
process.on("unhandledRejection", () => {});
