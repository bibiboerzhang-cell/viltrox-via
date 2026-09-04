import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../http", () => ({
  apiFetch: vi.fn(),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { apiFetch } from "../http";
import { listKolPoolFavorites } from "./kolPool-api";

const mockedFetch = vi.mocked(apiFetch);

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("listKolPoolFavorites in-flight reads", () => {
  it("coalesces the same identity and limit only while the request is pending", async () => {
    let resolve!: (value: { items: []; total: number }) => void;
    mockedFetch.mockReturnValue(new Promise((done) => { resolve = done; }) as never);

    const first = listKolPoolFavorites("same-user", 5000);
    const second = listKolPoolFavorites("same-user", 5000);

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    resolve({ items: [], total: 0 });
    await Promise.all([first, second]);

    mockedFetch.mockResolvedValue({ items: [], total: 0 } as never);
    await listKolPoolFavorites("same-user", 5000);
    expect(mockedFetch).toHaveBeenCalledTimes(2);
  });

  it("never shares an in-flight read across identities or limits", async () => {
    mockedFetch.mockResolvedValue({ items: [], total: 0 } as never);

    await Promise.all([
      listKolPoolFavorites("user-a", 5000),
      listKolPoolFavorites("user-b", 5000),
      listKolPoolFavorites("user-a", 2000),
    ]);

    expect(mockedFetch).toHaveBeenCalledTimes(3);
    expect(mockedFetch.mock.calls.map((call) => call[2])).toEqual(["user-a", "user-b", "user-a"]);
  });

  it("never shares one cookie-session promise across account keys", async () => {
    mockedFetch.mockResolvedValue({ items: [], total: 0 } as never);

    await Promise.all([
      listKolPoolFavorites("cookie-session", 5000, "staff-a"),
      listKolPoolFavorites("cookie-session", 5000, "staff-b"),
    ]);

    expect(mockedFetch).toHaveBeenCalledTimes(2);
  });
});
