const release = document.querySelector('#release-line')
const command = document.querySelector('#xattr-command')
const copy = document.querySelector('#copy-command')

fetch('https://api.github.com/repos/EyalDelarea/flackey/releases/latest')
  .then((response) => (response.ok ? response.json() : Promise.reject()))
  .then((data) => {
    const asset = data.assets?.find((item) => item.name === 'Flackey.zip')
    if (!release || !asset) return
    const version = String(data.tag_name || '').replace(/^v/, '')
    const size = `${(asset.size / 1e6).toFixed(1)} MB`
    const date = new Intl.DateTimeFormat('en-GB', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    }).format(new Date(data.published_at))
    release.textContent = `Version ${version} · ${size} · ${date}`
  })
  .catch(() => {})

copy?.addEventListener('click', async () => {
  const text = command?.textContent || ''
  try {
    await navigator.clipboard.writeText(text)
    copy.textContent = 'Copied'
  } catch {
    const range = document.createRange()
    range.selectNodeContents(command)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    copy.textContent = 'Selected'
  }
  window.setTimeout(() => {
    copy.textContent = 'Copy'
  }, 1600)
})
