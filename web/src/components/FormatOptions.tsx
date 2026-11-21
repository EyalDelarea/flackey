export const FORMAT_LABELS: Record<string, string> = { aiff: 'AIFF', wav: 'WAV', flac: 'FLAC' }

/** `defaultFormat` is the server's `default_filing_format`: AIFF on the Mac, FLAC on Windows, whose
 *  window (Chromium) cannot play AIFF. "Recommended" follows it, so the two never disagree. */
function formatNote(format: string, defaultFormat = 'aiff'): string {
  if (format === 'aiff') return defaultFormat === 'aiff'
    ? 'Recommended for Rekordbox: uncompressed audio, rich tags, and artwork.'
    : 'Uncompressed audio with rich tags and artwork for Rekordbox.'
  if (format === 'wav') return 'Uncompressed audio with Rekordbox-readable text tags. Rekordbox does not import cover art from WAV files.'
  if (format === 'flac') return 'Smaller lossless files with tags and artwork, supported by Rekordbox.'
  return 'Lossless filing format.'
}

/** One line for the Settings row, under the compact control: the setup step's cards have room for
 *  `formatNote`, a settings row does not. */
export function shortFormatNote(format: string, defaultFormat = 'aiff'): string {
  const note = format === 'aiff' ? 'Uncompressed, with tags and artwork'
    : format === 'wav' ? 'Uncompressed; Rekordbox skips its cover art'
    : format === 'flac' ? 'Smaller lossless files, with tags and artwork'
    : 'Lossless'
  return format === defaultFormat ? `${note}. Recommended.` : `${note}.`
}

/** The one-line warning a WAV choice gets (idea 13, issue #17): Rekordbox shows no cover art for a WAV file,
 *  whatever the file carries -- six ways of embedding it were tried and none showed. The way out is one
 *  press. It offers the system's default rather than AIFF everywhere: on Windows the default is FLAC
 *  because the app's own player (WebView2) cannot play AIFF, and FLAC keeps artwork in Rekordbox too. */
export function WavCoverWarning({ format, defaultFormat = 'aiff', onSwitch, disabled = false }: {
  format: string; defaultFormat?: string; onSwitch: (format: string) => void; disabled?: boolean
}) {
  if (format !== 'wav') return null
  const instead = defaultFormat === 'wav' ? 'aiff' : defaultFormat
  return (<div className="format-warn" role="note">
    <span>Rekordbox won't show cover art for WAV files.</span>
    <button type="button" className="btn-secondary" onClick={() => onSwitch(instead)} disabled={disabled}>
      Use {FORMAT_LABELS[instead] ?? instead.toUpperCase()} instead</button>
  </div>)
}

export default function FormatOptions({ formats, value, onChange, disabled = false, legend = 'File format', defaultFormat = 'aiff' }: {
  formats: string[]; value: string; onChange: (format: string) => void; disabled?: boolean; legend?: string; defaultFormat?: string
}) {
  return (<fieldset className="format-options" aria-label={legend}>
    {formats.map(format => {
      const selected = format === value
      return <button key={format} type="button" className={`format-choice${selected ? ' selected' : ''}`}
        aria-pressed={selected} onClick={() => onChange(format)} disabled={disabled} title={formatNote(format, defaultFormat)}>
        <span className="format-name">{FORMAT_LABELS[format] ?? format.toUpperCase()}</span>
        {format === defaultFormat && <span className="format-badge">Default</span>}
        <span className="format-note">{formatNote(format, defaultFormat)}</span>
      </button>
    })}
  </fieldset>)
}
