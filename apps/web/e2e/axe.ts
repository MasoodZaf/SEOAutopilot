import AxeBuilder from "@axe-core/playwright";
import {expect, type Page} from "@playwright/test";

/** Serious and critical WCAG 2.2 AA violations fail; the rest are reported. */
export async function expectAccessible(page: Page, label: string) {
  const results = await new AxeBuilder({page}).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const blocking = results.violations.filter(v => v.impact === "serious" || v.impact === "critical");
  const summary = blocking.map(v => `${v.id} (${v.impact}): ${v.nodes.length}× — ${v.nodes.slice(0, 3).map(n => n.target.join(" ")).join(" | ")}`);
  expect(summary, `${label}: accessibility violations`).toEqual([]);
}
