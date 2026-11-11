// The right column, on every screen (spec, "Layout"): the threads that need
// the owner, and the land queue. Both lists are short: their boxes grow to
// hold every row.
import { useQueue, useRoster, useThreads } from "../api/queries";
import { ago } from "../logic/ago";
import { href } from "../logic/route";
import { nameOf } from "./Bars";
import styles from "./RightColumn.module.css";
import { Row } from "./Row";
import { RowList } from "./RowList";
import { useNow } from "./useNow";

const QUEUE_HREF = href({ name: "section", section: "queue", q: "" });

function NeedsYou({ now }: { now: Date }) {
  const query = useThreads("needs");
  const roster = useRoster().data?.roster;
  const needs = (query.data?.pages.flatMap((p) => p.threads) ?? []).filter((t) => t.needs_owner);
  let body;
  if (query.isError) body = <p className={styles.empty}>The threads did not load.</p>;
  else if (query.isPending) body = <p className={styles.empty}>Loading.</p>;
  else if (needs.length === 0) body = <p className={styles.empty}>Nothing needs you now.</p>;
  else
    body = (
      <div className={styles.list}>
        <RowList
          short
          label="Needs you"
          items={needs}
          itemKey={(t) => t.root}
          estimateSize={64}
          renderRow={(t) => (
            <Row
              projects={t.projects}
              title={t.title}
              text={[t.projects[0] ? nameOf(t.projects[0], roster) : "", ago(t.last_t, now)].filter(Boolean).join(", ")}
              href={href({ name: "conversations", sort: "needs", open: [t.root] })}
            />
          )}
        />
      </div>
    );
  return (
    <section className={styles.part} aria-labelledby="right-needs">
      <h2 id="right-needs" className={styles.head}>
        Needs you
      </h2>
      {body}
    </section>
  );
}

function LandQueue({ now }: { now: Date }) {
  const query = useQueue();
  const roster = useRoster().data?.roster;
  const queue = query.data;
  const label = (project: string, branches: string[]) => [nameOf(project, roster), ...branches].join(", ");
  let body;
  if (query.isError) body = <p className={styles.empty}>The land queue did not load.</p>;
  else if (!queue) body = <p className={styles.empty}>Loading.</p>;
  else if (queue.holders.length + queue.waiting.length === 0) body = <p className={styles.empty}>No land runs and no one waiting.</p>;
  else
    body = (
      <div className={styles.list}>
        <RowList
          short
          label="Land queue"
          items={[
            ...queue.holders.map((h) => ({ id: h.id, project: h.project, title: label(h.project, h.branches), note: ago(h.since, now) })),
            ...queue.waiting.map((w, i) => ({
              id: w.id,
              project: w.project,
              title: (i === 0 ? "Next: " : "Then: ") + label(w.project, w.branches),
              note: w.granted ? "granted" : ago(w.t, now),
            })),
          ]}
          itemKey={(item) => item.id}
          estimateSize={44}
          renderRow={(item) => <Row projects={[item.project]} title={item.title} note={item.note} href={QUEUE_HREF} />}
        />
      </div>
    );
  return (
    <section className={styles.part} aria-labelledby="right-queue">
      <h2 id="right-queue" className={styles.head}>
        Land queue
      </h2>
      {body}
    </section>
  );
}

export function RightColumn() {
  const now = useNow();
  return (
    <>
      <NeedsYou now={now} />
      <LandQueue now={now} />
    </>
  );
}
