// A message body as React elements. parseBody gives data, never markup, and
// React sets every string as text, so a body that holds HTML shows it as text.
import { type Block, type Inline, parseBody } from "../logic/parseBody";
import { href } from "../logic/route";
import styles from "./Body.module.css";

const isWebLink = (url: string): boolean => /^https?:\/\//i.test(url);

function Parts({ parts }: { parts: Inline[] }) {
  return parts.map((part, i) => {
    switch (part.type) {
      case "code":
        return <code key={i}>{part.text}</code>;
      case "link":
        return isWebLink(part.href) ? (
          <a key={i} href={part.href} rel="noopener noreferrer" target="_blank">
            {part.href}
          </a>
        ) : (
          part.href
        );
      case "ref":
        // An id may name a thread or a row of a section; the stream opens it as a thread.
        return (
          <a key={i} href={href({ name: "conversations", sort: "needs", open: [part.id] })}>
            {part.id}
          </a>
        );
      default:
        return part.text;
    }
  });
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "p":
      return (
        <p>
          <Parts parts={block.parts} />
        </p>
      );
    case "ol":
      return (
        <ol start={block.start}>
          {block.items.map((item, i) => (
            <li key={i}>
              <Parts parts={item} />
            </li>
          ))}
        </ol>
      );
    case "ul":
      return (
        <ul>
          {block.items.map((item, i) => (
            <li key={i}>
              <Parts parts={item} />
            </li>
          ))}
        </ul>
      );
    case "code":
      return (
        <pre>
          <code>{block.text}</code>
        </pre>
      );
  }
}

/** `text` goes through parseBody; `blocks` are its output, already built (a section row's detail). */
export function Body({ text, blocks }: { text?: string; blocks?: Block[] }) {
  return (
    <div className={styles.body}>
      {(blocks ?? parseBody(text ?? "")).map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}
