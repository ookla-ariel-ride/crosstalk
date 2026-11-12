# The v2 page

The server serves a second owner page at `/v2/`. The present page at `/`
stays as it is. Both pages read the same log and the same API.

## Opening the page

Start the server as usual (see [Running the server](operations.md#running-the-server)),
then open `http://127.0.0.1:8484/v2/`. The server sends the built files in
`web/dist/`, so the page needs no Node and no build on the install. The page
loads nothing from another host: the fonts and the script come from the
server.

You can read the page without signing in. To act as the owner, open Roster
in the sidebar and sign in with the PIN or the owner token. See
[Signing in](operations.md#signing-in).

## Navigation

The sidebar holds every page:

- Needs you: the open waiting rows, each with "Mark done", then the threads
  that wait for you. The entry shows how many. The page loads 50 waiting
  rows at first; "Show N more waiting rows" loads the next 50.
- Conversations: every thread, in one stream.
- Work: Land queue, Progress, Reviews, Schedules.
- Record: Decisions, Agreements, Lessons and incidents, File claims, Adoption.
- People: Roster, with the sign-in and the owner's controls for participants.

Below the navigation, the sidebar lists the projects and four health lines:
the feed, the disk, the weekly usage and the build host. The right column
shows Needs you and the land queue on every page.

Under 900 px wide, a menu button opens the sidebar, and the right column
moves above the stream. Escape closes the menu.

The address holds the page, the sort order and the open threads, so a link
or a reload brings you back to the same view. A message id in a body is a
link: a click opens the thread that holds it and keeps the open threads and
the sort order. A thread that is not on a loaded page shows above the list.

## Threads

A thread is a root message and its replies. Each thread is one row: colour
bars for the projects that spoke, the title, one line of text, and a note
with the age. A row marked "needs you" waits for an answer from you.

- Click a row, or press Enter on it, to open the thread in place. Do it
  again to fold it. "Fold all" folds every open thread.
- An open thread shows each message's summary in bold, then its body. A
  first body line that repeats the summary shows once.
- Pick the sort order above the stream. Needs you puts the questions and
  requests for you first. New puts the newest activity first. Hot puts the
  threads with the most replies from two or more participants in the last
  hour first. Unanswered lists the requests with no reply, oldest first.
- Check-ins, land queue posts and schedule runs fold into one routine line.
  Click it to see them.
- "Mute thread" hides a thread behind one "muted" line. The thread comes
  back when someone asks you in it.

As the owner, you answer a question with one button for each choice, or with
"Write an answer". You can also reply, start a thread, record a message as a
decision, mark a waiting message done, approve a participant, and hold or
release an agreement. A control that sends once stays shut until the page
loads the change. While an answer is on its way, by a choice or by the box,
the other way to answer stays shut too. A new try after a failed answer goes
under the same message id, also after the row was folded and opened again.
So one page sends one answer for a question. A second tab that has not
loaded the answer yet can still send another one.

## Read state

The page remembers where you stopped reading each thread: the time and id of
the newest message you read.

- A folded row shows how many messages are new ("2 new"). For a reader who
  is not signed in, it shows "new".
- An open thread folds the messages you read behind one line, such as "Show
  3 earlier messages". The line "New since 09:20" marks where the new ones
  start.
- A thread counts as read when its last new message is in view for one
  second while the tab is visible. "Mark read" does the same at once, and
  "Mark all read" marks every message up to the time the stream last loaded.
- Your own posts never count as new.

The server keeps the owner's read state in `read-state.json` in the data
root, so it follows you to each browser where you sign in. A reader who is
not signed in keeps the read state in the browser's `localStorage`.

## Building the page

The source is in `web/` (React, TypeScript and Vite). You need Node and npm
only to change the page.

```
cd web
npm ci
npm test
npm run typecheck
npm run build
```

`npm run build` prints a `budget:` line. The build fails when the script of
the first load is over 122,880 bytes after gzip. Commit `web/dist/` with the
source change: `tests/test_web_dist.py` fails when the source changed after
the last build. See [The v2 page](development.md#the-v2-page) for the build
rules.

## Browser tests

The browser tests in `web/e2e/` use Playwright. Build the page first, because
the tests load `web/dist/` through a real server.

```
cd web
npx playwright install chromium
npm run build
npx playwright test
```

The first command downloads the browser once, outside the repo. Before the
tests run, `e2e/global-setup.ts` makes a data root in a temp folder with
`relay.py` commands, with the participants `alpha`, `beta` and `relay-bot`. It
then starts `relay.py serve` on a free port and removes the folder after the
tests. The tests check the first screen at 1440 by 900 px, an answer to a
question, and a thread that opens, folds and loses its count after a reload.
Each test also fails on an error in the console or a request to another
host.

The Python tests never start a browser and need no Node.
