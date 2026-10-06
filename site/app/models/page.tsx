import type { Metadata } from "next";
import { families, site } from "@/lib/data";
import { fmt } from "@/lib/format";

export const metadata: Metadata = { title: "What builders use" };

const MEDIA: Record<string, string> = {
  image: "image",
  video: "video",
  audio: "audio",
  "3d": "3D",
  training: "training",
  utility: "utility",
  multi: "several",
  unknown: "other",
};

export default function Models() {
  const f = families();
  const s = site();
  const used = f.families.filter((x) => x.repos > 0);
  const max = Math.max(1, ...used.map((x) => x.repos));
  const searched = f.families.filter((x) => x.repos === 0);
  return (
    <>
      <section className="hero">
        <p className="eyebrow">What builders use</p>
        <h1 className="figure">{fmt(used.length)}</h1>
        <p className="definition">
          fal model families seen in the code of {fmt(f.repos_with_model)} of the {fmt(f.repos)} repos.
          A lower bound: a family counts only where a model ID appears in code our searches read.
        </p>
        <p className="date">
          Data date <span className="mono">{f.data_date}</span>
          {s.queries_run.length < s.queries_total ? (
            <> · model searches run with the full collection, which is in progress</>
          ) : null}
        </p>
      </section>

      {used.length ? (
        <section className="section" aria-labelledby="ranked">
          <h2 id="ranked">By number of repos</h2>
          <p className="section-note">Repos with fal in the code that show the family. Files: GitHub&apos;s estimate of matching files.</p>
          <ol className="rows">
            {used.map((x, i) => (
              <li className="fam" key={x.id}>
                <span className="rank">{String(i + 1).padStart(2, "0")}</span>
                <a href={x.url}>{x.name}</a>
                <span className="num">
                  {fmt(x.repos)} repos{x.files != null ? ` · ${fmt(x.files)} files` : ""}
                </span>
                <svg className="bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
                  <rect
                    x="0.5"
                    y="0.5"
                    width={Math.max(1, (99 * x.repos) / max)}
                    height="7"
                    fill={i === 0 ? "var(--chalk)" : "var(--ink-2)"}
                    stroke="var(--ink)"
                    strokeWidth="0.5"
                    vectorEffect="non-scaling-stroke"
                  />
                </svg>
              </li>
            ))}
          </ol>
          <details>
            <summary>As a table</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Family</th>
                    <th>Repos</th>
                    <th>Files</th>
                  </tr>
                </thead>
                <tbody>
                  {used.map((x) => (
                    <tr key={x.id}>
                      <td>{x.name}</td>
                      <td>{fmt(x.repos)}</td>
                      <td>{x.files != null ? fmt(x.files) : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      ) : null}

      <section className="section" aria-labelledby="searched">
        <h2 id="searched">{used.length ? "Also searched for" : "What we search for"}</h2>
        <p className="section-note">
          {fmt(searched.length)} model families from fal&apos;s public catalogue, each linked to its fal page.
        </p>
        <ul className="rows">
          {searched.map((x) => (
            <li className="row" key={x.id}>
              <div className="row-head">
                <a className="row-name" href={x.url}>
                  {x.name}
                </a>
                <span className="row-stars">{MEDIA[x.media] ?? x.media}</span>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
