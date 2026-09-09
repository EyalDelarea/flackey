export type IconName = 'download' | 'library' | 'settings' | 'playlist' | 'search' | 'folder' | 'check' | 'x' | 'chevron' | 'phone' | 'upload'
const PATHS: Record<IconName, string> = {
  download: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7v9M8.5 12.5 12 16l3.5-3.5',
  library: 'M4 6h10M4 12h10M4 18h6M19.5 16V7l3-1M14.5 16a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1',
  playlist: 'M4 7h12M4 12h12M4 17h7M17 17.5a2.5 2.5 0 1 0 2.5-2.5V8l3-1',
  search: 'M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm5 11.5 4.5 4.5',
  folder: 'M3 7.5A1.5 1.5 0 0 1 4.5 6h5l2 2h8A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z',
  check: 'm5 12.5 4.5 4.5L19 7.5',
  x: 'M7 7l10 10M17 7 7 17',
  upload: 'M12 19V5m0 0-6 6m6-6 6 6',
  chevron: 'm9 6 6 6-6 6',
  phone: 'M7 2.5h10v19H7zM11 18h2',
}
export default function Icon({ name, size = 16, stroke = 1.6 }: { name: IconName; size?: number; stroke?: number }) {
  return (<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flex: 'none' }}><path d={PATHS[name]} /></svg>)
}
