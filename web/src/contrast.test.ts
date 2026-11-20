import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// jsdom computes no colours, so these read the rules themselves. The audit (WEB-10) measured the vivid
// --red at about 3.6:1 and the house blue at about 4.0:1 on white, both under the 4.5:1 that 12 px text
// needs; the text forms of those colours exist for exactly this.
const css = readFileSync(resolve(__dirname, 'theme.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

const rule = (selector: string) => {
  const m = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].find(([, sel]) => sel.split(',').some(s => s.trim() === selector))
  return m?.[2] ?? ''
}
const lightToken = (name: string) => css.slice(0, css.indexOf('@media')).match(new RegExp(`${name}:\\s*([^;]+);`))?.[1].trim()

it('writes error text in the red made to be read, not the red made for dots', () => {
  expect(rule('.err')).toMatch(/color:\s*var\(--red-text\)/)
})

it('gives link buttons a blue that clears 4.5:1 on white', () => {
  const color = rule('.btn-link').match(/color:\s*var\((--[\w-]+)\)/)?.[1]
  expect(color).toBeDefined()
  expect(color).not.toBe('--accent')
  expect(lightToken(color!)).toBe('#0060DF')
})
