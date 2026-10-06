export const FORMAT_LABELS: Record<string, string> = { aiff: 'AIFF', wav: 'WAV', flac: 'FLAC' }

/** `defaultFormat` is the server's `default_filing_format`: AIFF on the Mac, FLAC on Windows, whose
 *  window (Chromium) cannot play AIFF. "Recommended" follows it, so the two never disagree. */
export function formatNote(format: string, defaultFormat = 'aiff'): string {
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
