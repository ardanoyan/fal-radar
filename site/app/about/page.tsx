import type { Metadata } from "next";
import { REPO_URL, crossRefs, site } from "@/lib/data";
import { fmt } from "@/lib/format";

export const metadata: Metadata = { title: "About" };

export default function About() {
  const s = site();
  const x = crossRefs();
  return (
    <div className="prose">
      <section className="hero">
        <p className="eyebrow">About</p>
        <h1>What this is</h1>
        <p className="definition" style={{ marginTop: 12 }}>
          fal radar finds public GitHub projects built on fal, the generative media platform,
          sorts them, and shows which fal models builders use. It is built to run once a week and
          to draft a short digest of what is new. Unofficial. Not affiliated with fal.
        </p>
      </section>

      <section className="section" aria-labelledby="why">
        <h2 id="why">Why I built this</h2>
        <p className="placeholder">[WHY I BUILT THIS]</p>
      </section>

      <section className="section" aria-labelledby="fal">
        <h2 id="fal">What I would do with it at fal</h2>
        <p className="placeholder">[WHAT I WOULD DO WITH IT AT FAL]</p>
      </section>

      <section className="section" aria-labelledby="method">
        <h2 id="method">Method</h2>
        <p>
          Everything comes from GitHub&apos;s REST API with a read-only token: code search, repository
          search, and lookups of each repository and owner. No pages are scraped.
        </p>
        <p>Two tiers of evidence:</p>
        <ul>
          <li>
            <strong>Code</strong>: a fal client or integration package in a manifest
            (<span className="mono">@fal-ai/client</span>, <span className="mono">fal-client</span>, the
            Swift, Kotlin and Dart clients), an import, a call to <span className="mono">fal.run</span> or{" "}
            <span className="mono">queue.fal.run</span>, or a fal model ID in a source file. Integration
            packages count too: Vercel AI SDK, TanStack AI, LiveKit, LiteLLM and fal&apos;s n8n node.
            Only this tier is counted in the headline, and only repos that are not forks and not
            fal&apos;s own.
          </li>
          <li>
            <strong>Mention</strong>: fal appears only in a README, description, name or topic. These
            have their own count
            {s.queries_run.some((q) => q.startsWith("mention-")) ? ` (${fmt(s.mentions)} in the last run)` : ""} and
            are never in the headline.
          </li>
        </ul>
        <p>
          GitHub&apos;s code search ignores punctuation, so every hit is checked against the exact
          string in the matched text before it counts. Searches over 1,000 results are split into
          smaller searches by file size or creation date. Model families are sampled: the first 300
          files per family, then a per-repo search for the most starred repos.
        </p>
        <h3 style={{ marginTop: 20 }}>Coverage of the last run</h3>
        <p className="section-note" style={{ marginTop: 4 }}>
          Run <span className="mono">{s.run_id ?? s.data_date}</span>, {fmt(s.queries_run.length)} of{" "}
          {fmt(s.queries_total)} searches. Total: GitHub&apos;s estimate. Slices: the sum over the split
          searches. Kept: files with the exact string, outside documentation.
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Search</th>
                <th>Total</th>
                <th>Slices</th>
                <th>Files</th>
                <th>Dropped</th>
                <th>Docs</th>
                <th>Repos</th>
              </tr>
            </thead>
            <tbody>
              {s.coverage.map((c) => (
                <tr key={c.id}>
                  <td>{c.id}</td>
                  <td>{fmt(c.total_count)}</td>
                  <td>{fmt(c.sum_of_slice_totals)}</td>
                  <td>{fmt(c.files)}</td>
                  <td>{fmt(c.dropped)}</td>
                  <td>{fmt(c.in_docs)}</td>
                  <td>{fmt(c.repos)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section" aria-labelledby="missing">
        <h2 id="missing">What is missing</h2>
        <ul>
          <li>Private repositories, closed-source products, and projects that live only on Discord or X.</li>
          <li>
            Code GitHub&apos;s search index does not hold: anything outside the default branch, files over
            384 KB, repositories with no activity in the last year, archived repositories, and most forks.
          </li>
          {s.api_facts.slice(0, 2).map((f) => (
            <li key={f}>{f}</li>
          ))}
          <li>
            Mentions are not counted because a name is weak evidence: &quot;fal&quot; is also the Turkish word
            for fortune-telling.
          </li>
          <li>Models per repo are a lower bound: a model counts only where its ID appears in code we read.</li>
        </ul>
      </section>

      <section className="section" aria-labelledby="cross">
        <h2 id="cross">Cross-references</h2>
        <p className="section-note" style={{ marginTop: 4 }}>
          {x.note}
        </p>
        <ul>
          {x.items.map((i) => (
            <li key={i.id}>
              {i.label}: <span className="mono">{fmt(i.value)}</span>
              {i.also ? ` ${i.also}` : ""}, read on <span className="mono">{i.read_on}</span> (
              <a href={i.source}>source</a>). {i.caveat}
            </li>
          ))}
        </ul>
      </section>

      <section className="section" aria-labelledby="fromfal">
        <h2 id="fromfal">From fal</h2>
        <p>
          {fmt(s.from_fal)} repositories with fal in the code belong to fal itself (the fal-ai and
          fal-ai-community organisations). They are left out of every count:{" "}
          {s.from_fal_repos.map((r, i) => (
            <span key={r}>
              <a href={`https://github.com/${r}`}>{r}</a>
              {i < s.from_fal_repos.length - 1 ? ", " : "."}
            </span>
          ))}
        </p>
      </section>

      <section className="section" aria-labelledby="removal">
        <h2 id="removal">Removal and submissions</h2>
        <p>
          The radar stores only what GitHub shows on public profiles and repositories. To have a
          project or profile removed, open a{" "}
          <a href={`${REPO_URL}/issues/new?template=remove-my-project.yml`}>removal request</a>; it is
          applied before the next weekly run. To add a project the searches missed, open an{" "}
          <a href={`${REPO_URL}/issues/new?template=add-a-project.yml`}>add request</a>.
        </p>
      </section>

      <section className="section" aria-labelledby="by">
        <h2 id="by">Built by</h2>
        <p>
          Arda Noyan Karasoglu. <a href="https://github.com/ardanoyan">GitHub</a>,{" "}
          <a href="https://www.linkedin.com/in/ardanoyankarasoglu">LinkedIn</a>.
        </p>
        <p>Unofficial. Not affiliated with fal.</p>
        <p className="muted">
          Python collector and a static Next.js site. Type: Marcellus, Instrument Sans, IBM Plex Mono.{" "}
          <a href={REPO_URL}>Source</a>, MIT licence.
        </p>
      </section>
    </div>
  );
}
