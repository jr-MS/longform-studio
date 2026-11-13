import { describe, expect, it } from "vitest";
import { editText, inkStyle } from "./textStyle";
import type { Layer } from "./generated";

const layer: Layer = {
  id: "title", kind: "text", content: "强劲气流", color: "#222222",
  gradient_end: null, gradient_angle: 180, x: 0, y: 0, width: 1, height: 1,
  font: "PingFang SC", font_size: 60, weight: 400, line_height: 1.35,
  align: "left", opacity: 1, radius: 0, fact_ids: [],
  runs: [
    { text: "强劲", color: "#222222", gradient_end: null, gradient_angle: 180 },
    { text: "气流", color: "#b58d61", gradient_end: "#e5d7b3", gradient_angle: 90 },
  ],
};

describe("改字保留墨色", () => {
  it("替换与插入保留对应文字的分色和渐变", () => {
    const changed = editText(layer, "强劲柔和气流");
    expect(changed.runs?.map(run => [run.text, run.color])).toEqual([["强劲柔和", "#222222"], ["气流", "#b58d61"]]);
    expect(changed.runs?.[1].gradient_end).toBe("#e5d7b3");
    expect(editText(layer, "柔和送风").runs?.map(run => [run.text, run.color])).toEqual([["柔和", "#222222"], ["送风", "#b58d61"]]);
  });
  it("删除、换行和连续编辑不会重置颜色", () => {
    const changed = { ...layer, ...editText(layer, "强劲\n气流") };
    expect(editText(changed, "强劲\n气流更远").runs?.at(-1)?.color).toBe("#b58d61");
    expect(editText(layer, "气流").runs?.[0].gradient_end).toBe("#e5d7b3");
    expect(editText(layer, "").runs).toEqual([]);
  });
  it("按 Unicode 字符编辑，不拆散 emoji", () => {
    const value = { ...layer, content: "冷🌬暖", runs: [
      { ...layer.runs[0], text: "冷🌬" }, { ...layer.runs[1], text: "暖" },
    ] };
    expect(editText(value, "冷暖").runs?.map(run => run.text)).toEqual(["冷", "暖"]);
    expect(inkStyle(layer.runs[1]).backgroundImage).toBe("linear-gradient(90deg, #b58d61, #e5d7b3)");
  });
});
