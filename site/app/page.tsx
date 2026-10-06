import RepoList from "@/components/RepoList";
import {
  CLIENT_LABELS,
  builders,
  familyNames,
  families,
  findings,
  list,
  site,
  thisWeek,
} from "@/lib/data";
import { avatar, fmt, stars } from "@/lib/format";

function RepoRows({ repos, names }: { repos: ReturnType<typeof thisWeek>["new"]["repos"]; names: Record<string, string> }) {
  return (
    <ul className="rows">
      {repos.map((r) => (
        <li className="row" key={r.full_name}>
          <div className="row-head">
            <a className="row-name" href={r.url}>
              {r.full_name}
            </a>
            <span className="row-stars">{stars(r.stars)}</span>
          </div>
          {r.description ? <p className="row-desc">{r.description}</p> : null}
          {r.models.length ? (
            <div className="row-meta">
              {r.models.slice(0, 4).map((m) => (
                <span className="chip" key={m}>
                  {names[m] ?? m}
                </span>
              ))}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export default function Home() {
  const s = site();
  const f = findings();
  const b = builders();
  const w = thisWeek();
  const l = list();
  const fams = families();
  const names = familyNames();
  const partial = s.queries_run.length < s.queries_total;
  const clients = Object.entries(CLIENT_LABELS)
    .filter(([id]) => l.rows.some((r) => r.c.includes(id)))
    .map(([id, label]) => ({ id, label }));
  const familyOptions = fams.families.filter((x) => x.repos > 0).map((x) => ({ id: x.id, label: x.name }));

  return (
    <>
      <section className="hero" aria-labelledby="headline">
        <p className="eyebrow">Built on fal, mapped weekly</p>
        <h1 id="headline" className="figure">
          {fmt(s.headline)}
        </h1>
        <p className="definition">
          public GitHub repositories with fal in their code, from {fmt(s.builders)} builders. Not
          forks, not fal&apos;s own repos.
        </p>
        <p className="date">
          Data date <span className="mono">{s.data_date}</span>
          {partial ? (
            <>
              {" "}
              · from {fmt(s.queries_run.length)} of {fmt(s.queries_total)} searches; the full run is in progress
            </>
          ) : null}
        </p>
        <details>
          <summary>What counts</summary>
          <p>
            A repository counts when its code shows fal: a fal client or integration package in a
            manifest, an import, a call to fal&apos;s API, or a fal model ID in a source file. Mentions in
            a README or description do not count. Private repos and closed-source products are
            invisible to this tool. <a href="/about/">Method and caveats</a>.
          </p>
        </details>
      </section>

      <section className="section" aria-labelledby="found">
        <h2 id="found">What we found</h2>
        <ol className="findings">
          {f.findings.map((x) => (
            <li key={x.id}>
              <p>{x.text}</p>
              <details>
                <summary>How this was counted</summary>
                <p className="mono">{x.query}</p>
              </details>
            </li>
          ))}
        </ol>
      </section>

      <section className="section" aria-labelledby="builders">
        <h2 id="builders">Builders to know</h2>
        <p className="section-note">
          {fmt(b.builders.length)} builders with a repo of 20 or more stars pushed in the last 90 days, by the
          stars of their best fal repo. Not fal, no forks, no templates.
        </p>
        <ol className="rows">
          {b.builders.map((x, i) => (
            <li className="row builder" key={x.login}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={avatar(x.avatar_url)} alt="" width={40} height={40} loading="lazy" />
              <div>
                <div className="row-head">
                  <a className="row-name" href={x.profile_url}>
                    <span className="rank">{String(i + 1).padStart(2, "0")}</span>
                    {x.name ?? x.login}
                  </a>
                  <span className="row-stars">{stars(x.best.stars)}</span>
                </div>
                <p className="row-desc">
                  <a href={x.best.url}>{x.best.full_name}</a>
                  {x.best.description ? `: ${x.best.description}` : ""}
                </p>
                {x.best.models.length ? (
                  <div className="row-meta">
                    <span>models seen in code, at least:</span>
                    {x.best.models.slice(0, 4).map((m) => (
                      <span className="chip" key={m}>
                        {names[m] ?? m}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="section" aria-labelledby="week">
        <div className="plate">
          <h2 id="week">This week</h2>
          {w.first_issue ? (
            <p className="section-note">First issue: week-over-week changes start with the second weekly run.</p>
          ) : null}
          <h3>
            New <span className="count">{fmt(w.new.count)}</span>
          </h3>
          <p className="section-note">Repos with fal in the code, created in the last {w.new.days} days.</p>
          <RepoRows repos={w.new.repos.slice(0, 5)} names={names} />
          <h3>Most starred</h3>
          <p className="section-note">Created in the last {w.most_starred.days} days.</p>
          <RepoRows repos={w.most_starred.repos} names={names} />
          <h3>
            Models this week <span className="count">{fmt(w.models.count)}</span>
          </h3>
          <p className="section-note">
            Endpoints fal&apos;s catalogue lists as created between {w.models.from} and {w.models.to}.
          </p>
          <ul className="rows">
            {w.models.endpoints.map((e) => (
              <li className="row" key={e.id}>
                <div className="row-head">
                  <span className="row-name">{e.name ?? e.id}</span>
                  <span className="row-stars">{e.category}</span>
                </div>
                <p className="row-desc mono">{e.id}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="section" aria-labelledby="all">
        <h2 id="all">All repos</h2>
        <RepoList
          initial={l.rows.slice(0, 50)}
          total={l.count}
          clients={clients}
          families={familyOptions}
          familyNames={names}
          clientLabels={CLIENT_LABELS}
        />
      </section>
    </>
  );
}
