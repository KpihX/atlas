import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AtlasSubtitles } from "./AtlasSubtitles";

describe("AtlasSubtitles", () => {
  it("shows only Atlas spoken text while enabled", () => {
    const { rerender } = render(
      <AtlasSubtitles enabled text="I found the source." companionName="Atlas" />,
    );
    expect(screen.getByRole("status").textContent).toContain("I found the source.");
    rerender(<AtlasSubtitles enabled={false} text="Hidden" companionName="Atlas" />);
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<AtlasSubtitles enabled text="Current spoken phrase" companionName="Atlas" />);
    expect(screen.getByRole("status").textContent).toContain("Current spoken phrase");
  });
});
