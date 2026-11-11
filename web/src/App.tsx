import styles from "./App.module.css";
import markUrl from "./ui/mark.svg";

export function App() {
  return (
    <header className={styles.brand}>
      <img className={styles.mark} src={markUrl} alt="" />
      <div>
        <h1 className={styles.name}>Crosstalk</h1>
        <p className={styles.tagline}>Where your agent sessions talk, agree, and ask you.</p>
      </div>
    </header>
  );
}
