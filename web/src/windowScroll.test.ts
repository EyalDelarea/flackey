import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Every stylesheet the app loads: theme.css from main.tsx, app.css from App.tsx.
const css = ['theme.css', 'app.css']
  .map(f => readFileSync(resolve(__dirname, f), 'utf8'))
  .join('\n')
  .replace(/\/\*[\s\S]*?\*\//g, '')

/** The declarations of every rule whose selector list names exactly this element, `@media` and all. */
function declarations(selector: string): string[] {
  const rules = css.matchAll(/([^{}]+)\{([^{}]*)\}/g)
  return [...rules]
    .filter(([, sel]) => sel.split(',').some(s => s.trim() === selector))
    .flatMap(([, , body]) => body.split(';').map(d => d.trim()).filter(Boolean))
}

// The app shell is exactly as tall as the window and a screen scrolls inside its own pane, so the outer
// document has nothing to scroll -- but WebKit will still rubber-band the main frame under a trackpad,
// sliding the whole app up and showing bare desktop through the gap. jsdom does no layout and cannot
// watch that happen, so what these hold is the pair of rules that decides whether it can.
it('stops the root frame rubber-banding out from under the app', () => {
  expect(declarations('html')).toContain('overscroll-behavior: none')
})

it('leaves a document that really is too tall free to scroll', () => {
  // `overflow: hidden` on the document would stop the bounce too -- by making real overflow unreachable.
  // If a screen ever outgrows the window, it must scroll rather than lose its bottom in silence.
  for (const el of ['html', 'body', '#root']) {
    expect(declarations(el).filter(d => /^overflow(-y)?:\s*hidden$/.test(d))).toEqual([])
  }
})
