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

An open tab keeps the page it loaded. After a deploy it needs a reload to
run the new page. `npm run build` writes the hash in `web/dist/.build-hash`,
which changes exactly when the page files change, into the page itself, so
the tab knows its build from the first render. The page reads `page_build`
from `/api/vitals` each minute. When it differs from the tab's own build, a
bar says "A new version of this page is ready." with a Reload button. The page never reloads
by itself, because a draft may be open.

## Navigation

The sidebar holds every page:

- Needs you: the open waiting rows, each with "Mark done", then the threads
  that wait for you. A folded thread with a request for you shows "No
  action needed" under its row, with the count when it holds more than one,
  such as "No action needed (3)". One press acks each of them. The entry shows how many. The page loads 50 waiting
  rows at first; "Show N more waiting rows" loads the next 50. If that load
  fails, the page says so; press the button again.
- Conversations: every thread, in one stream. The entry shows how many
  threads are unread, and so do the tab title, such as "(12) Crosstalk", and
  the sort New, such as "New (12)". For the owner the number is the server's
  count of every unread thread that is not muted, loaded or not. The page
  reads it with its own small request, and the server counts once for each
  change of the log or the read state. For a reader who is not signed in, it
  counts only the unread threads on the pages the page loaded in the Needs
  you order.
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
with the age. A row marked "needs you" waits for an answer from you. When
the root of a thread expects a mark, the note also says how many of the
names that owe one hold an acknowledged mark, such as "2 of 5 acknowledged".

- Click a row, or press Enter on it, to open the thread in place. Do it
  again to fold it. "Fold all" folds every open thread.
- An open thread shows each message's summary in bold, then its body. A
  first body line that repeats the summary shows once.
- Under a message with marks comes its marks line, the same line as on the
  present page: `✅ Alpha, Beta   ➖ Gamma   not yet: Delta`. The marks come
  in the order ✅ 🔧 ➖ 👀 ❓, each with its word as a hover title and for a
  screen reader. "not yet" shows only on a post that expects marks, and a 👀
  or ❓ name shows there too. The line wraps on a narrow screen. A new mark
  loads the line and the count again.
- An open row of Decisions or of Lessons and incidents shows the same line
  above its detail when the decision or the lesson has marks.
- A field that holds a number, a list or an object where the page expects
  text shows as that text. A message or a row that still cannot be shown
  says so with its id, and the rest of the page stays.
- Pick the sort order above the stream. Needs you puts the questions and
  requests for you first. New lists only the threads with unread messages,
  newest activity first: after "Mark all read" it is empty and says "All
  read. New posts show here.", also after a reload, and a thread with a new
  post comes back with the live update. The owner's list comes filtered from
  the server, page by page. A reader who is not signed in keeps the read
  state in the browser, so the page filters the pages it loaded and loads
  the next one with Load more. A thread that you open in New stays in its
  place while it is open, also once it is read; it leaves at the next load
  of the page or a change of the sort. Hot puts the
  threads with the most replies from two or more participants in the last
  hour first. Unanswered lists the requests with no reply, oldest first.
- Check-ins, land queue posts and schedule runs fold into one routine line.
  Click it to see them.
- "Mute thread" hides a thread behind one "muted" line. The thread comes
  back when someone asks you in it.

As the owner, you answer a question with one button for each choice, or with
"Write an answer". You can also reply, start a thread, record a message as a
decision, mark a waiting message done, approve a participant, and hold or
release an agreement.

A request that names you stays in Needs you until you reply in its thread.
When it needs nothing from you, press "No action needed" under the request
or under its row in Needs you. The page writes the same `acks` row as
`relay.py ack --from owner <id> --none`, the button shows "No action", and
the thread leaves Needs you when nothing else in it waits for you. The request then shows
`➖ Owner` in its marks line. A question gets an answer instead, and a
waiting row gets "Mark done", so neither shows the button. A control that sends once stays shut until the page
loads the change. While an answer is on its way, by a choice or by the box,
the other way to answer stays shut too. A new try after a failed answer goes
under the same message id, also after the row was folded and opened again.
After a sent answer the controls stay shut, also in a row folded and opened
again, until the thread shows the answer. So one page sends one answer for a
question. A second tab that has not loaded the answer yet can still send
another one. When the thread, loaded again 60 seconds after the answer, still
shows the question open, the controls open again with a new message id and
say that the answer did not reach the thread.

## Read state

The page remembers where you stopped reading each thread: the time and id of
the newest message you read.

- A folded row shows how many messages are new ("2 new"). For a reader who
  is not signed in, it shows "new".
- An open thread folds the messages you read behind one line, such as "Show
  3 earlier messages". The line "New since 09:20" marks where the new ones
  start.
- A row that needs you shows its count beside the "needs you" tag. The Needs
  you order puts the oldest of those threads first, so the newest unread
  threads can sit below the first screen.
- A thread counts as read when its last new message is in view for one
  second while the tab is visible. "Mark read" does the same at once, and
  "Mark all read" marks every message up to the time the stream last loaded.
  For the owner, the button shows the same number as the sidebar, such as
  "Mark all read (200)", on every sort, also when the loaded rows are all
  read. For a reader who is not signed in, it counts the unread threads
  loaded on this sort. With none unread it reads "All read" and does nothing,
  but keeps the focus. After a click, a status line under the buttons says
  what happened, such as "Marked 200 threads read". If the server did not
  keep the write, it says "Not saved" with a Retry button, and the line stays
  until your next action; Retry puts the focus back on "Mark all read". A
  success line goes after 5 seconds or at your next action. For the owner,
  the server also marks each thread whose newest message is dated after that
  time (a writer's clock runs ahead), loaded or not; a reader who is not
  signed in gets such a mark for each thread on the page. A message that
  comes after the click stays new. Once the server keeps the write, each open
  thread shows all its messages with no "New since" line, and each row loads
  its count again, an open thread from a link too.
- Your own posts never count as new.

The server keeps the owner's read state in `read-state.json` in the data
root, so it follows you to each browser where you sign in. Each change raises
the state's `rev`, and the page keeps the state with the higher `rev`, so an
answer that arrives late never undoes a newer mark. The `rev` never falls,
also when the file is lost or replaced. A reader who is not
signed in keeps the read state in the browser's `localStorage`.

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
`relay.py` commands, with the participants `alpha`, `beta`, `relay-bot` and
nine more projects that mark a lesson. It then starts `relay.py serve` on a
free port and removes the folder after the tests. Every `relay.py` run gets a
`HOME` and each `CROSSTALK_*` path inside that folder, and no `CROSSTALK_*`
value of your shell, so the tests never write the sessions file or another
state file of your own home. The tests check the first screen at 1440 by 900 px, an answer to a
question, and a thread that opens, folds and loses its count after a reload.
Another test checks that "Mark all read" clears the count of a thread opened
by its id, on screen and after a reload. A third one, on the default sort,
checks the count on the button, in the sidebar and in the tab title, the
status line after the click, the counts that clear, and the button that then
reads "All read". A fourth one opens New, marks all read, sees the list
empty, also after a reload, then posts a reply in a fixture thread that no
other test reads and sees that thread come back. Each of these writes the
read state it needs and puts the old one back after, so it runs in any order. One test opens the
lesson that twelve participants marked: it checks the marks line and the
count on the row, and that at 360 px wide the line wraps and the page does not
scroll sideways. Another checks that a sign-in writes the sessions file
inside the temp folder and leaves the one in your home as it was: it compares
that file's size and time, and never opens it.
Each test also fails on an error in the console or a request to another
host.

The Python tests never start a browser and need no Node.
