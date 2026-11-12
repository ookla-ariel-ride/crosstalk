// A render error of one part (one message, one row, one page) stays in that
// part: the part shows what failed and what to do, and the rest of the page
// stays. React catches render errors only in a class component with
// getDerivedStateFromError (react.dev, Component).
import { Component, type ReactNode } from "react";
import styles from "./Guard.module.css";

export interface GuardProps {
  /** What the part is, for the message: "Message beta-0002", "This page". */
  what: string;
  /** The part's data: when it changes, a failed Guard shows the part again. */
  data?: unknown;
  children: ReactNode;
}

export class Guard extends Component<GuardProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev: GuardProps) {
    // A refetch can bring the row back fixed: try it again.
    if (this.state.failed && prev.data !== this.props.data) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <p role="alert" className={styles.failed}>
        {this.props.what} could not be shown. Reload the page. If it still fails, the row holds data the page cannot read.
      </p>
    );
  }
}
