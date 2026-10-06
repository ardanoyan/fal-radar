// Copies the list the home page loads after first paint into public/data, after
// checking its shape. Everything else is read from ../data at build time.
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const data = join(here, "..", "..", "data");
const out = join(here, "..", "public", "data");

const list = JSON.parse(readFileSync(join(data, "list.json"), "utf8"));
if (!Array.isArray(list.rows) || list.rows.length !== list.count) {
  throw new Error("data/list.json: rows and count disagree; run `uv run collector site-data`");
}
mkdirSync(out, { recursive: true });
copyFileSync(join(data, "list.json"), join(out, "list.json"));
console.log(`public/data/list.json: ${list.count} rows, data date ${list.data_date}`);
