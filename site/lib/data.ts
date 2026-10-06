import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

// Every figure on the site comes from these files in ../data, written by the collector.
const DATA = join(process.cwd(), "..", "data");

function read<T>(name: string, schema: z.ZodType<T>): T {
  const raw = JSON.parse(readFileSync(join(DATA, name), "utf8"));
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    throw new Error(`data/${name} does not match its schema: ${parsed.error.message}`);
  }
  return parsed.data;
}

const Coverage = z.object({
  id: z.string(),
  kind: z.string(),
  q: z.string(),
  total_count: z.number(),
  sum_of_slice_totals: z.number(),
  slices: z.number(),
  truncated_slices: z.number(),
  sampled: z.boolean(),
  files: z.number(),
  literal_match: z.number(),
  dropped: z.number(),
  in_docs: z.number(),
  vendored: z.number().optional(),
  repos: z.number(),
});

export const Site = z.object({
  data_date: z.string(),
  run_id: z.string().nullable(),
  queries_run: z.array(z.string()),
  queries_total: z.number(),
  headline: z.number(),
  builders: z.number(),
  notable: z.number(),
  active: z.number(),
  mentions: z.number(),
  from_fal: z.number(),
  from_fal_repos: z.array(z.string()),
  coverage: z.array(Coverage),
  api_facts: z.array(z.string()),
});

const Finding = z.object({
  id: z.string(),
  text: z.string().regex(/\d/, "a finding needs a number"),
  query: z.string(),
  numbers: z.record(z.string(), z.unknown()),
});
export const Findings = z.object({ data_date: z.string(), findings: z.array(Finding) });

const Repo = z.object({
  full_name: z.string(),
  url: z.string().url(),
  description: z.string().nullable(),
  stars: z.number(),
  models: z.array(z.string()),
  created_at: z.string().optional(),
  pushed_at: z.string().nullable().optional(),
});

export const Builders = z.object({
  data_date: z.string(),
  rule: z.string(),
  builders: z.array(
    z.object({
      login: z.string(),
      name: z.string().nullable(),
      type: z.string(),
      profile_url: z.string().url(),
      avatar_url: z.string(),
      repos: z.number(),
      best: Repo,
    }),
  ),
});

export const ThisWeek = z.object({
  data_date: z.string(),
  first_issue: z.boolean(),
  new: z.object({ days: z.number(), count: z.number(), repos: z.array(Repo) }),
  most_starred: z.object({ days: z.number(), repos: z.array(Repo) }),
  models: z.object({
    from: z.string(),
    to: z.string(),
    count: z.number(),
    catalogue_read_on: z.string().nullable(),
    endpoints: z.array(
      z.object({ id: z.string(), name: z.string().nullable(), category: z.string().nullable() }),
    ),
  }),
});

export const Families = z.object({
  data_date: z.string(),
  repos_with_model: z.number(),
  repos: z.number(),
  families: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      media: z.string(),
      url: z.string().url(),
      repos: z.number(),
      files: z.number().nullable(),
      files_sampled: z.boolean().nullable(),
    }),
  ),
});

export const Row = z.object({
  n: z.string(),
  u: z.string(),
  d: z.string().nullable(),
  s: z.number(),
  c: z.array(z.string()),
  m: z.array(z.string()),
  l: z.string().nullable(),
  k: z.string(),
  cr: z.string(),
  p: z.string().nullable(),
});
export type RowT = z.infer<typeof Row>;
export const List = z.object({ data_date: z.string(), count: z.number(), rows: z.array(Row) });

export const CrossRefs = z.object({
  note: z.string(),
  items: z.array(
    z.object({
      id: z.string(),
      label: z.string(),
      value: z.number(),
      also: z.string().optional(),
      source: z.string().url(),
      read_on: z.string(),
      caveat: z.string(),
    }),
  ),
});

export const site = () => read("site.json", Site);
export const findings = () => read("findings.json", Findings);
export const builders = () => read("builders.json", Builders);
export const thisWeek = () => read("this_week.json", ThisWeek);
export const families = () => read("families.json", Families);
export const list = () => read("list.json", List);
export const crossRefs = () => read("cross_references.json", CrossRefs);

export const fmt = (n: number) => n.toLocaleString("en-US");

export function familyNames(): Record<string, string> {
  return Object.fromEntries(families().families.map((f) => [f.id, f.name]));
}

export const CLIENT_LABELS: Record<string, string> = {
  js: "JavaScript",
  python: "Python",
  swift: "Swift",
  kotlin: "Kotlin",
  dart: "Dart",
  http: "HTTP",
  integration: "Integration",
};

export const REPO_URL = "https://github.com/ardanoyan/fal-radar";
