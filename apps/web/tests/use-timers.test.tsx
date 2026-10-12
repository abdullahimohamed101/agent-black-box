import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTimers } from "@/lib/useTimers";

afterEach(() => vi.useRealTimers());

describe("useTimers", () => {
  it("forgets a timer when it fires, so the pending count stays bounded", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useTimers());
    const fired = vi.fn();
    for (let i = 0; i < 1000; i++) act(() => result.current.later(fired, 10));
    expect(result.current.pending()).toBe(1000);
    act(() => void vi.advanceTimersByTime(20));
    expect(fired).toHaveBeenCalledTimes(1000);
    expect(result.current.pending()).toBe(0);
  });

  it("clears whatever is pending on unmount", () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useTimers());
    const fired = vi.fn();
    result.current.later(fired, 100);
    unmount();
    vi.advanceTimersByTime(200);
    expect(fired).not.toHaveBeenCalled();
  });
});
