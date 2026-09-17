// What the page knows about the window it lives in. Set by src/flackey/desktop.py: the launcher adds
// `?titlebar=inset` on macOS. Nothing more: the window's size is the owner's, chosen by dragging it and
// remembered across launches by the launcher, so the page has no business asking for one.
export const insetTitlebar = (): boolean => new URLSearchParams(window.location.search).get('titlebar') === 'inset'
