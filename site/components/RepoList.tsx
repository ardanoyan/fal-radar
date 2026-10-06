"use client";

import { useEffect, useMemo, useState } from "react";

type Row = {
  n: string;
  u: string;
  d: string | null;
  s: number;
  c: string[];
  m: string[];
  l: string | null;
  k: string;
  cr: string;
  p: string | null;
};

type Option = { id: string; label: string };

const STEP = 50;
const SORTS = [
  { id: "stars", label: "Most stars" },
  { id: "pushed", label: "Recently pushed" },
  { id: "created", label: "Newest" },
];

const fmt = (n: number) => n.toLocaleString("en-US");
const stars = (n: number) => `${fmt(n)} ${n === 1 ? "star" : "stars"}`;

export default function RepoList({
  initial,
  total,
  clients,
  families,
  familyNames,
  clientLabels,
}: {
  initial: Row[];
  total: number;
  clients: Option[];
  families: Option[];
  familyNames: Record<string, string>;
  clientLabels: Record<string, string>;
}) {
  const [rows, setRows] = useState<Row[]>(initial);
  const [loaded, setLoaded] = useState(initial.length >= total);
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState("");
  const [client, setClient] = useState("");
  const [model, setModel] = useState("");
  const [sort, setSort] = useState("stars");
  const [shown, setShown] = useState(STEP);

  // The full list loads after first paint; the first rows are already in the page.
  useEffect(() => {
    if (loaded) return;
    let live = true;
    fetch("/data/list.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((doc: { rows: Row[] }) => {
        if (live) {
          setRows(doc.rows);
          setLoaded(true);
        }
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [loaded]);

  useEffect(() => setShown(STEP), [q, client, model, sort]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = rows.filter(
      (r) =>
        (!needle || r.n.toLowerCase().includes(needle) || (r.d ?? "").toLowerCase().includes(needle)) &&
        (!client || r.c.includes(client)) &&
        (!model || r.m.includes(model)),
    );
    const key: Record<string, (r: Row) => string | number> = {
      stars: (r) => r.s,
      pushed: (r) => r.p ?? "",
      created: (r) => r.cr,
    };
    const k = key[sort];
    return [...out].sort((a, b) => {
      const x = k(a);
      const y = k(b);
      return x < y ? 1 : x > y ? -1 : a.n.localeCompare(b.n);
    });
  }, [rows, q, client, model, sort]);

  const visible = filtered.slice(0, shown);
  const filtering = Boolean(q || client || model);

  return (
    <div>
      <div className="controls" role="search">
        <div className="wide">
          <label htmlFor="q">Search name or description</label>
          <input id="q" type="search" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
        </div>
        <div>
          <label htmlFor="client">Client</label>
          <select id="client" value={client} onChange={(e) => setClient(e.target.value)}>
            <option value="">All clients</option>
            {clients.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="model">Model family</label>
          <select id="model" value={model} onChange={(e) => setModel(e.target.value)} disabled={!families.length}>
            <option value="">{families.length ? "All models" : "None seen yet"}</option>
            {families.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="wide">
          <label htmlFor="sort">Sort</label>
          <select id="sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="list-status" aria-live="polite">
        {filtering ? `${fmt(filtered.length)} of ${fmt(total)} repos match.` : `${fmt(total)} repos.`}
        {!loaded && !failed ? " Loading the full list." : ""}
        {failed ? " The full list did not load; showing the first rows." : ""}
      </p>
      <ul className="rows">
        {visible.map((r) => (
          <li className="row" key={r.n}>
            <div className="row-head">
              <a className="row-name" href={r.u}>
                {r.n}
              </a>
              <span className="row-stars">{stars(r.s)}</span>
            </div>
            {r.d ? <p className="row-desc">{r.d}</p> : null}
            <div className="row-meta">
              {r.c.map((c) => (
                <span className="chip" key={c}>
                  {clientLabels[c] ?? c}
                </span>
              ))}
              {r.m.slice(0, 4).map((m) => (
                <span className="chip" key={m}>
                  {familyNames[m] ?? m}
                </span>
              ))}
              {r.p ? <span className="mono">pushed {r.p}</span> : null}
            </div>
          </li>
        ))}
      </ul>
      {filtered.length > shown ? (
        <button type="button" className="more" onClick={() => setShown((n) => n + STEP)}>
          Show {fmt(Math.min(STEP, filtered.length - shown))} more
        </button>
      ) : null}
    </div>
  );
}
