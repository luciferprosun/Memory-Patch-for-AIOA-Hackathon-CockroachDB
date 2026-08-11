import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const expected = "22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313";
const path = new URL("../src/aioa_memory_kernel/personal_memory_ui/static/htmx.min.js", import.meta.url);
const bytes = await readFile(path);
const actual = createHash("sha256").update(bytes).digest("hex");
if (actual !== expected) {
  throw new Error("vendored htmx.org 2.0.8 digest mismatch");
}
if (!bytes.includes(Buffer.from("htmx"))) {
  throw new Error("vendored HTMX asset is invalid");
}
console.log(`STEP35_UI_ASSET_CHECK=PASS sha256=${actual}`);
