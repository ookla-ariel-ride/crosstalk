// The votes on a post (relay.py ack --vote), read only: the count per choice with
// ADOPT and OBJECT first, where a proposal with a due time stands, then each voter
// with the choice and the note. It reads only the `votes` key that the server puts
// in a post's marks (marks.vote_view), the same data as relay.py tally.
import type { MarksView, RosterEntry, VoteState, VotesView } from "../api/types";
import { byName, displayName } from "./MarksLine";
import { useNow } from "./useNow";
import styles from "./Votes.module.css";

type Roster = Record<string, RosterEntry> | undefined;

/** The choices the block always counts, in this order; other choices follow by name. */
const FIRST = ["ADOPT", "OBJECT"];

function choiceOrder(a: string, b: string): number {
  const ia = FIRST.indexOf(a);
  const ib = FIRST.indexOf(b);
  if (ia !== -1 || ib !== -1) return (ia === -1 ? FIRST.length : ia) - (ib === -1 ? FIRST.length : ib);
  return a < b ? -1 : a > b ? 1 : 0;
}

function dueText(due: string): string {
  const d = new Date(due);
  return Number.isNaN(d.getTime()) ? due : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/**
 * The state the block shows: the server's state, or its outcome once the due time
 * passed while the view still says open (the server builds a view only when the
 * log changes).
 */
export function shownState(votes: VotesView, now: Date): VoteState | null {
  if (votes.state === "open" && votes.due && now.getTime() >= Date.parse(votes.due)) return votes.outcome ?? "open";
  return votes.state;
}

const STATE_WORD: Record<VoteState, string> = {
  open: "Open until",
  passed: "Passed at",
  objected: "Objected, due",
  retracted: "Retracted",
};

/** `roster` comes from the caller, as for MarksLine. A post with no votes and no due time shows nothing. */
export function Votes({ marks, roster }: { marks: MarksView | undefined; roster: Roster }) {
  const votes = marks?.votes;
  if (!votes || (votes.votes.length === 0 && !votes.due)) return null;
  return <VotesBlock votes={votes} roster={roster} />;
}

/** The block itself: only a post that shows votes runs the clock that moves the state on. */
function VotesBlock({ votes, roster }: { votes: VotesView; roster: Roster }) {
  const now = useNow();
  const choices = [...new Set([...FIRST, ...Object.keys(votes.counts)])].sort(choiceOrder);
  const order = byName(roster);
  const list = [...votes.votes].sort((a, b) => choiceOrder(a.vote, b.vote) || order(a.from, b.from));
  const state = shownState(votes, now);
  return (
    <div className={styles.votes} data-votes="">
      <div className={styles.head}>
        <span role="img" aria-label="votes" title="votes">
          🗳️
        </span>
        {votes.votes.length === 0 ? (
          <span className={styles.count}>No votes yet</span>
        ) : (
          choices.map((c) => (
            <span key={c} className={styles.count} data-choice={c}>
              {c + " " + (votes.counts[c] ?? 0)}
            </span>
          ))
        )}
        {state ? (
          <span className={styles.state} data-state={state}>
            {state === "retracted" ? STATE_WORD[state] : STATE_WORD[state] + " " + dueText(votes.due ?? "")}
          </span>
        ) : null}
      </div>
      {list.length > 0 ? (
        <ul className={styles.list}>
          {list.map((v) => (
            <li key={v.from} className={styles.voter}>
              <span className={styles.choice}>{v.vote}</span> {displayName(v.from, roster)}
              {v.note ? <span className={styles.note}>{": " + v.note}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
