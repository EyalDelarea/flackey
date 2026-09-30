import { createContext, useContext } from 'react'
import type { Platform } from './api'

// What the page knows about the window it lives in. Set by src/flackey/desktop.py: the launcher adds
// `?titlebar=inset` on macOS. Nothing more: the window's size is the owner's, chosen by dragging it and
// remembered across launches by the launcher, so the page has no business asking for one.
export const insetTitlebar = (): boolean => new URLSearchParams(window.location.search).get('titlebar') === 'inset'


// The OS the app runs on, from `/api/health`. A Mac until health has loaded, so the Mac rendering (the
// only one there was) never flickers into other words on the way in. Provided once, by App.
export const PlatformContext = createContext<Platform>('mac')
export const usePlatform = (): Platform => useContext(PlatformContext)

// The words the page borrows from the OS. Mac's are what the page said before there was a second one.
export const revealLabel = (p: Platform): string =>
  p === 'windows' ? 'Show in File Explorer' : p === 'linux' ? 'Show in folder' : 'Show in Finder'
export const fileManager = (p: Platform): string => p === 'windows' ? 'File Explorer' : p === 'linux' ? 'file manager' : 'Finder'
export const thisComputer = (p: Platform): string => p === 'mac' ? 'this Mac' : 'this PC'
