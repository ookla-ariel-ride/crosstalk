import { useEffect, useState } from "react";

/** The time now, renewed each `ms`, so an age such as "6 min" moves on its own. */
export function useNow(ms = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
