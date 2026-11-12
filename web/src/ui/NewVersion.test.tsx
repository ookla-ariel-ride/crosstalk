// @vitest-environment jsdom
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { keys } from "../api/queries";
import type { Vitals } from "../api/types";
import { NewVersion } from "./NewVersion";
import { renderWith } from "./testing";

const vitals = (page_build: string | null): Vitals => ({
  disk_free_gb: 100,
  usage: null,
  usage_limit: null,
  build_host: null,
  model_families: [],
  page_build,
});

describe("NewVersion", () => {
  test("no bar while the build is the one the tab first read", async () => {
    const { qc } = renderWith(<NewVersion />, [[keys.vitals, vitals("aaa")]]);
    expect(screen.queryByText(/A new version of this page is ready/)).toBeNull();
    await act(async () => qc.setQueryData(keys.vitals, vitals("aaa")));
    await act(() => new Promise((ok) => setTimeout(ok, 20)));
    expect(screen.queryByText(/A new version of this page is ready/)).toBeNull();
  });

  test("a changed build shows the bar; the page reloads only on the button", async () => {
    const reload = vi.fn();
    const { qc } = renderWith(<NewVersion reload={reload} />, [[keys.vitals, vitals("aaa")]]);
    await act(async () => qc.setQueryData(keys.vitals, vitals("bbb")));
    expect(await screen.findByText("A new version of this page is ready.")).toBeTruthy();
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test("the build the page was built as counts from the first render: a deploy before the first vitals answer shows the bar", async () => {
    const meta = document.createElement("meta");
    meta.name = "crosstalk-build";
    meta.content = "aaa";
    document.head.append(meta);
    try {
      renderWith(<NewVersion />, [[keys.vitals, vitals("bbb")]]);
      expect(await screen.findByText("A new version of this page is ready.")).toBeTruthy();
    } finally {
      meta.remove();
    }
  });

  test("a server with no build named never shows the bar, and the first build named is the one kept", async () => {
    const { qc } = renderWith(<NewVersion />, [[keys.vitals, vitals(null)]]);
    await act(async () => qc.setQueryData(keys.vitals, vitals("aaa")));
    await act(() => new Promise((ok) => setTimeout(ok, 20)));
    expect(screen.queryByText(/A new version/)).toBeNull();
    await act(async () => qc.setQueryData(keys.vitals, vitals(null)));
    await act(() => new Promise((ok) => setTimeout(ok, 20)));
    expect(screen.queryByText(/A new version/)).toBeNull();
    await act(async () => qc.setQueryData(keys.vitals, vitals("bbb")));
    expect(await screen.findByText("A new version of this page is ready.")).toBeTruthy();
  });

  test("the Shell shows the bar above the page", async () => {
    const { Shell } = await import("./Shell");
    const { qc } = renderWith(
      <Shell route={{ name: "conversations", sort: "needs", open: [] }} mode="push"><p>x</p></Shell>,
      [[keys.vitals, vitals("aaa")]],
    );
    await act(async () => qc.setQueryData(keys.vitals, vitals("bbb")));
    await waitFor(() => expect(screen.getByRole("main").textContent).toContain("A new version of this page is ready."));
  });
});
