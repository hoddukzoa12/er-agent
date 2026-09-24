// Frame recorder helpers for ego-browser scripts (imported by path).
import fs from "node:fs/promises";
export async function record(page, dir, { until, maxMs = 180000, holdMs = 1500, action } = {}) {
  await fs.mkdir(dir, { recursive: true });
  const frames = [];
  const t0 = Date.now();
  let n = 0, doneAt = null, actionDone = !action;
  while (true) {
    const p = `${dir}/f${String(n++).padStart(5, "0")}.png`;
    await page.screenshot({ path: p });
    frames.push({ p, t: Date.now() - t0 });
    if (!actionDone) { actionDone = true; await action(); }
    if (doneAt === null && (await page.evaluate(until))) doneAt = Date.now();
    if (doneAt !== null && Date.now() - doneAt >= holdMs) break;
    if (Date.now() - t0 > maxMs) break;
  }
  await fs.writeFile(`${dir}/frames.json`, JSON.stringify(frames));
  return frames.length;
}
