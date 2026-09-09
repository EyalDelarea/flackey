import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')

it('index.html links the SVG and PNG favicons and both files exist', () => {
  const html = readFileSync(resolve(root, 'index.html'), 'utf8')
  expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/icon.svg" />')
  expect(html).toContain('<link rel="icon" type="image/png" sizes="256x256" href="/icon.png" />')
  expect(existsSync(resolve(root, 'public/icon.svg'))).toBe(true)
  expect(existsSync(resolve(root, 'public/icon.png'))).toBe(true)
})
