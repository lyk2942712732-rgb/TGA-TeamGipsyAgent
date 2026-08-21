import { describe, expect, it } from "vitest";
import { newTaskId } from "./NewTaskPage";

describe("newTaskId", () => {
  it("still generates an id when an HTTP page does not expose crypto.randomUUID", () => {
    const cryptoApi = globalThis.crypto;
    const original = cryptoApi.randomUUID;
    Object.defineProperty(cryptoApi, "randomUUID", { configurable: true, value: undefined });

    try {
      expect(newTaskId()).toMatch(/^task_[a-f0-9]{12}$/);
    } finally {
      Object.defineProperty(cryptoApi, "randomUUID", { configurable: true, value: original });
    }
  });
});
