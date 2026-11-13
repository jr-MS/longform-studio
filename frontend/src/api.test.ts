import { describe, expect, it } from "vitest";
import { clampPosition, fileUrl } from "./api";

describe("画布位置与素材路径", () => {
  it("拖动不能越过画布或使图层移出右边界", () => {
    expect(clampPosition(-.3, .2)).toBe(0);
    expect(clampPosition(.99, .2)).toBe(.8);
    expect(clampPosition(.3, .2)).toBe(.3);
  });
  it("素材文件名必须编码，不能拼接成查询参数", () => {
    expect(fileUrl({ id: "p1" }, "image?.png")).toBe("/api/projects/p1/files/image%3F.png");
  });
});
