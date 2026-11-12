import { useLive } from "./api/queries";
import { MarkDoneButton, NewThread, OwnerScope, ownerActions, People } from "./features/owner";
import { Conversations, NeedsYou } from "./features/conversations/Stream";
import { SectionPage } from "./features/sections/SectionPage";
import type { Route } from "./logic/route";
import { Shell, useHashRoute } from "./ui/Shell";

/** The page of a route. The streams carry the owner's controls, which read the roster once for the page. */
export function page(route: Route) {
  switch (route.name) {
    case "people":
      return <People />;
    case "conversations":
      return (
        <OwnerScope>
          <Conversations route={route} renderActions={ownerActions} top={<NewThread />} />
        </OwnerScope>
      );
    case "needs":
      return (
        <OwnerScope>
          <NeedsYou renderActions={ownerActions} waitingActions={(row) => <MarkDoneButton id={row.id} />} />
        </OwnerScope>
      );
    case "section":
      return <SectionPage section={route.section} q={route.q} />;
  }
}

export function App() {
  const route = useHashRoute();
  // The one place that starts the live updates.
  const mode = useLive();
  return (
    <Shell route={route} mode={mode}>
      {page(route)}
    </Shell>
  );
}
