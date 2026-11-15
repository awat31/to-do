# to-do

**Bright Tasks** — a lightweight to-do list tracking app.

## Features

- Add, complete, and delete tasks
- Priority levels (low / medium / high) shown as bright colour tags
- Filter: All / Active / Done
- "Clear completed" button and a rainbow progress bar
- Tasks persist in the browser via `localStorage`

## Colour scheme

Light cream background with bright accent colours:

| Colour | Hex | Used for |
|--------|-----|----------|
| Coral | `#ff6b6b` | High priority |
| Amber | `#ffb627` | Medium priority |
| Teal | `#2ec4b6` | Low priority / checkboxes |
| Violet | `#9b5de5` | Add button |
| Sky blue | `#4cc9f0` | Focus highlights |
| Pink | `#f15bb5` | Clear completed |

## Run it

No build step or dependencies — just open the file in a browser:

```sh
open index.html        # macOS
xdg-open index.html    # Linux
```

Or serve it locally:

```sh
python3 -m http.server 8000
# then visit http://localhost:8000
```
